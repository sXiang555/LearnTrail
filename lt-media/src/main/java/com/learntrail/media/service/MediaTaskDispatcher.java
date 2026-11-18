package com.learntrail.media.service;

import com.learntrail.media.config.MediaTaskConfiguration;
import com.learntrail.media.config.MediaUploadProperties;
import com.learntrail.media.mapper.MediaTaskMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.util.concurrent.TimeUnit;

@Component
@Slf4j
@ConditionalOnProperty(prefix = "learntrail.media.upload", name = "enabled", havingValue = "true")
public class MediaTaskDispatcher {
    private final MediaTaskMapper tasks;
    private final MediaUploadService uploads;
    private final MediaUploadProperties config;
    private final RabbitTemplate publisher;

    public MediaTaskDispatcher(MediaTaskMapper tasks, MediaUploadService uploads, MediaUploadProperties config,
            ConnectionFactory connection, MessageConverter converter) {
        if (!(connection instanceof CachingConnectionFactory)
                || !((CachingConnectionFactory) connection).isPublisherConfirms()
                || !((CachingConnectionFactory) connection).isPublisherReturns()) {
            throw new IllegalStateException("媒体任务需要启用 correlated publisher confirms 与 publisher returns");
        }
        this.tasks = tasks; this.uploads = uploads; this.config = config;
        // 私有模板不注册为全局 Bean，不替换原 RabbitTemplate 或其他业务的投递行为。
        publisher = new RabbitTemplate(connection); publisher.setMessageConverter(converter); publisher.setMandatory(true);
    }

    @Scheduled(fixedDelay = 5000, initialDelay = 5000)
    public void dispatch() {
        for (Long id : tasks.due()) {
            try {
                CorrelationData correlation = new CorrelationData("media-task-" + id);
                publisher.convertAndSend(MediaTaskConfiguration.EXCHANGE, MediaTaskConfiguration.ROUTE, id, message -> {
                    message.getMessageProperties().setMessageId("media-task-" + id);
                    message.getMessageProperties().setDeliveryMode(MessageDeliveryMode.PERSISTENT); return message;
                }, correlation);
                CorrelationData.Confirm confirm = correlation.getFuture().get(5, TimeUnit.SECONDS);
                if (!confirm.isAck() || correlation.getReturned() != null) throw new IllegalStateException("MEDIA_TASK_NOT_ROUTED");
                tasks.dispatched(id);
            } catch (Exception error) {
                tasks.dispatchFailed(id);
                if (error instanceof InterruptedException) { Thread.currentThread().interrupt(); return; }
                log.warn("媒体任务 {} 投递未确认，保留数据库记录重试", id);
            }
        }
    }

    @Scheduled(fixedDelay = 15000, initialDelay = 15000)
    public void recover() {
        tasks.reclaim(config.getMaxAttempts());
        tasks.syncFailure(null);
        uploads.recover();
    }
}
