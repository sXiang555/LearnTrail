package com.learntrail.media.config;

import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.config.SimpleRabbitListenerContainerFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

@Configuration
@EnableScheduling
@ConditionalOnProperty(prefix = "learntrail.media.upload", name = "enabled", havingValue = "true")
public class MediaTaskConfiguration {
    public static final String EXCHANGE = "learntrail.media.tasks";
    public static final String QUEUE = "learntrail.media.process.v1";
    public static final String ROUTE = "process.v1";

    @Bean
    public Declarables mediaTaskQueues() {
        DirectExchange exchange = new DirectExchange(EXCHANGE, true, false);
        Queue tasks = QueueBuilder.durable(QUEUE).deadLetterExchange(EXCHANGE).deadLetterRoutingKey("failed.v1").build();
        Queue failed = QueueBuilder.durable("learntrail.media.process.failed.v1").build();
        return new Declarables(exchange, tasks, failed, BindingBuilder.bind(tasks).to(exchange).with(ROUTE),
                BindingBuilder.bind(failed).to(exchange).with("failed.v1"));
    }

    @Bean(name = "mediaTaskListenerFactory")
    public SimpleRabbitListenerContainerFactory mediaTaskListenerFactory(ConnectionFactory connection,
            MessageConverter converter, MediaUploadProperties properties) {
        SimpleRabbitListenerContainerFactory factory = new SimpleRabbitListenerContainerFactory();
        factory.setConnectionFactory(connection); factory.setMessageConverter(converter);
        factory.setAcknowledgeMode(AcknowledgeMode.MANUAL);
        factory.setConcurrentConsumers(properties.getWorkers());
        factory.setMaxConcurrentConsumers(properties.getWorkers()); factory.setPrefetchCount(1);
        factory.setDefaultRequeueRejected(false); return factory;
    }

    @Bean(name = "mediaProcessingPool", destroyMethod = "shutdownNow")
    public ThreadPoolExecutor mediaProcessingPool(MediaUploadProperties properties) {
        AtomicInteger count = new AtomicInteger();
        return new ThreadPoolExecutor(properties.getWorkers(), properties.getWorkers(), 0, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(properties.getQueueCapacity()), runnable -> {
                    Thread thread = new Thread(runnable, "media-process-" + count.incrementAndGet());
                    thread.setDaemon(true); return thread;
                }, new ThreadPoolExecutor.AbortPolicy());
    }

    @Bean(name = "mediaLeaseScheduler", destroyMethod = "shutdownNow")
    public ScheduledExecutorService mediaLeaseScheduler() {
        return Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "media-lease-renewal"); thread.setDaemon(true); return thread;
        });
    }

    @Bean(name = "taskScheduler")
    public ThreadPoolTaskScheduler mediaTaskScheduler() {
        ThreadPoolTaskScheduler scheduler = new ThreadPoolTaskScheduler();
        scheduler.setPoolSize(2); scheduler.setThreadNamePrefix("media-task-scan-");
        scheduler.setRemoveOnCancelPolicy(true); return scheduler;
    }
}
