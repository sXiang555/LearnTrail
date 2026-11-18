package com.learntrail.search.mq;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.learntrail.search.service.PublicContentIndex;
import lombok.RequiredArgsConstructor;
import org.springframework.amqp.core.AcknowledgeMode;
import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.config.RetryInterceptorBuilder;
import org.springframework.amqp.rabbit.config.SimpleRabbitListenerContainerFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.retry.RejectAndDontRequeueRecoverer;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.amqp.SimpleRabbitListenerContainerFactoryConfigurer;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.io.IOException;

@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(name = "learntrail.search.content.enabled", havingValue = "true")
@RequiredArgsConstructor
public class PublicContentConsumer {

    private static final String OUTBOX = "learntrail_search_outbox";
    private final PublicContentIndex index;
    private final ObjectMapper mapper;

    @Bean
    public static Declarables publicContentTopology(
            @Value("${learntrail.search.content.exchange:learntrail.content.cdc}") String exchangeName,
            @Value("${learntrail.search.content.queue:learntrail.content.index}") String queueName) {
        DirectExchange exchange = new DirectExchange(exchangeName);
        DirectExchange dead = new DirectExchange(exchangeName + ".dead");
        Queue queue = QueueBuilder.durable(queueName)
                .deadLetterExchange(dead.getName()).deadLetterRoutingKey("content.outbox.dead").build();
        Queue deadQueue = QueueBuilder.durable(queueName + ".dead").build();
        Binding binding = BindingBuilder.bind(queue).to(exchange).with("content.outbox");
        Binding deadBinding = BindingBuilder.bind(deadQueue).to(dead).with("content.outbox.dead");
        return new Declarables(exchange, queue, dead, deadQueue, binding, deadBinding);
    }

    @Bean
    public static SimpleRabbitListenerContainerFactory publicContentListener(
            SimpleRabbitListenerContainerFactoryConfigurer configurer, ConnectionFactory connectionFactory) {
        SimpleRabbitListenerContainerFactory factory = new SimpleRabbitListenerContainerFactory();
        configurer.configure(factory, connectionFactory);
        factory.setAutoStartup(true);
        factory.setAcknowledgeMode(AcknowledgeMode.AUTO);
        factory.setPrefetchCount(4);
        factory.setDefaultRequeueRejected(false);
        factory.setAdviceChain(RetryInterceptorBuilder.stateless().maxAttempts(4)
                .backOffOptions(1000, 2, 8000).recoverer(new RejectAndDontRequeueRecoverer()).build());
        return factory;
    }

    @RabbitListener(queues = "${learntrail.search.content.queue:learntrail.content.index}",
            containerFactory = "publicContentListener")
    public void consume(byte[] message) throws IOException {
        JsonNode envelope = mapper.readTree(message);
        if (!OUTBOX.equals(envelope.path("table").asText()) || !"INSERT".equals(envelope.path("type").asText())) {
            return;
        }
        JsonNode data = envelope.path("data");
        if (!data.isArray() || data.isEmpty()) {
            throw new IllegalArgumentException("Missing public content outbox rows");
        }
        for (JsonNode row : data) {
            String kind = required(row, "content_kind");
            String id = required(row, "content_id");
            long revision = Long.parseLong(required(row, "revision"));
            String visibility = required(row, "visible");
            if (!("article".equals(kind) || "series".equals(kind)) || Long.parseLong(id) < 1 || revision < 1
                    || !("0".equals(visibility) || "1".equals(visibility))) {
                throw new IllegalArgumentException("Invalid public content outbox row");
            }
            index.write(kind, id, revision, "1".equals(visibility), required(row, "slug"),
                    required(row, "title"), required(row, "summary"), required(row, "body"));
        }
    }

    private static String required(JsonNode row, String key) {
        JsonNode value = row.path(key);
        if (!value.isValueNode() || value.isNull()) {
            throw new IllegalArgumentException("Missing public content field " + key);
        }
        return value.asText();
    }
}
