package com.learntrail.media.service;

import com.learntrail.media.config.MediaTaskConfiguration;
import com.learntrail.media.config.MediaUploadProperties;
import com.learntrail.media.domain.dto.MediaUploadModels.*;
import com.learntrail.media.mapper.MediaTaskMapper;
import com.learntrail.media.mapper.MediaUploadMapper;
import com.rabbitmq.client.Channel;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.support.AmqpHeaders;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.messaging.handler.annotation.Header;
import org.springframework.stereotype.Component;
import java.io.IOException;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

@Component
@Slf4j
@ConditionalOnProperty(prefix = "learntrail.media.upload", name = "enabled", havingValue = "true")
public class MediaTaskWorker {
    private final MediaTaskMapper tasks;
    private final MediaUploadMapper uploads;
    private final MediaUploadStore store;
    private final MediaTranscoder transcoder;
    private final MediaUploadProperties config;
    private final ThreadPoolExecutor workers;
    private final ScheduledExecutorService leases;

    public MediaTaskWorker(MediaTaskMapper tasks, MediaUploadMapper uploads, MediaUploadStore store,
            MediaTranscoder transcoder, MediaUploadProperties config,
            @Qualifier("mediaProcessingPool") ThreadPoolExecutor workers,
            @Qualifier("mediaLeaseScheduler") ScheduledExecutorService leases) {
        this.tasks = tasks; this.uploads = uploads; this.store = store; this.transcoder = transcoder;
        this.config = config; this.workers = workers; this.leases = leases;
    }

    @RabbitListener(queues = MediaTaskConfiguration.QUEUE, containerFactory = "mediaTaskListenerFactory")
    public void consume(Long id, Channel channel, @Header(AmqpHeaders.DELIVERY_TAG) long tag) throws IOException {
        Future<?> future = null;
        try {
            if (id == null || id <= 0) throw new IllegalArgumentException("无效媒体任务");
            future = workers.submit(() -> execute(id));
            future.get(config.getProcessTimeoutSeconds() + 120L, TimeUnit.SECONDS);
            // ACK 在消费线程发出，禁止多个 Worker 线程并发操作同一个 Channel。
            channel.basicAck(tag, false);
        } catch (InterruptedException e) {
            if (future != null) future.cancel(true);
            Thread.currentThread().interrupt(); channel.basicNack(tag, false, false);
        } catch (ExecutionException | TimeoutException | RejectedExecutionException | IllegalArgumentException e) {
            if (future != null) future.cancel(true);
            // 失败消息保留在独立死信队列；数据库任务仍由到期扫描重新投递，不立即热循环 requeue。
            channel.basicNack(tag, false, false);
            log.warn("媒体任务 {} 消费未完成，将按持久化状态恢复", id);
        }
    }

    public void execute(Long id) {
        Task recorded = tasks.find(id);
        if (recorded == null) throw new IllegalArgumentException("媒体任务不存在");
        String token = UUID.randomUUID().toString().replace("-", "");
        if (tasks.claim(id, token, config.getLeaseSeconds(), config.getMaxAttempts()) != 1) return;
        Task task = tasks.find(id);
        if (task == null || !token.equals(task.getRunToken())) return;
        AtomicBoolean alive = new AtomicBoolean(true);
        int interval = Math.max(10, config.getLeaseSeconds() / 3);
        ScheduledFuture<?> renewal = leases.scheduleWithFixedDelay(() -> {
            try { if (tasks.renew(id, token, config.getLeaseSeconds()) != 1) alive.set(false); }
            catch (RuntimeException e) { alive.set(false); }
        }, interval, interval, TimeUnit.SECONDS);
        try {
            Session session = uploads.find(task.getUploadId());
            if (session == null || !task.getMediaId().equals(session.getMediaId())) {
                throw new MediaTranscoder.Failure("TASK_SOURCE_INVALID", true);
            }
            if (tasks.processing(id, token) != 1) throw new MediaTranscoder.Failure("LEASE_LOST", false);
            Output output = transcoder.process(session, token, alive::get);
            if (!alive.get()) throw new MediaTranscoder.Failure("LEASE_LOST", false);
            store.succeeded(task, session, output);
        } catch (Exception error) {
            String code = error instanceof MediaTranscoder.Failure
                    ? ((MediaTranscoder.Failure) error).getCode() : "MEDIA_PROCESS_RETRY";
            boolean permanent = error instanceof MediaTranscoder.Failure && ((MediaTranscoder.Failure) error).isPermanent();
            store.failed(task, code, config.getMaxAttempts(), permanent);
            log.warn("媒体任务 {} 第 {} 次处理结束，状态码 {}", id, task.getAttempts(), code);
        } finally {
            alive.set(false); renewal.cancel(false);
        }
    }
}
