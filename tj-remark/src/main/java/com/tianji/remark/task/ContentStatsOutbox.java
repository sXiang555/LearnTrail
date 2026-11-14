package com.tianji.remark.task;

import com.tianji.api.dto.remark.ContentStatsDTO;
import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.annotation.*;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.util.*;

@Slf4j
@Component
@RequiredArgsConstructor
public class ContentStatsOutbox {
    public static final String DIRTY = "likes:content:dirty";
    public static String meta(String type, Long id) { return "likes:content:stats:" + type + ":" + id; }
    private final StringRedisTemplate redis;
    private final RabbitMqHelper mq;
    private static final DefaultRedisScript<List> SNAPSHOT = new DefaultRedisScript<>(
            "local v=redis.call('HGET',KEYS[2],'version'); if not v then return {} end; " +
            "return {redis.call('SCARD',KEYS[1]),redis.call('HGET',KEYS[2],'views') or '0',v}", List.class);
    private static final DefaultRedisScript<Long> ROTATE = new DefaultRedisScript<>(
            "return redis.call('ZADD',KEYS[1],'XX',ARGV[1],ARGV[2])", Long.class);
    private static final DefaultRedisScript<Long> ACK = new DefaultRedisScript<>(
            "if redis.call('HGET',KEYS[1],'version')==ARGV[1] then " +
            "return redis.call('ZREM',KEYS[2],ARGV[2]) end; return 0", Long.class);

    @Scheduled(fixedDelay = 5000)
    public void publishPending() {
        Set<String> members = redis.opsForZSet().range(DIRTY, 0, 29);
        if (members == null) return;
        for (String member : members) {
            try {
                String[] identity = member.split(":", -1);
                if (identity.length != 2 || !("ARTICLE".equals(identity[0]) || "SERIES".equals(identity[0]))) continue;
                Long id = Long.valueOf(identity[1]);
                List<?> values = redis.execute(SNAPSHOT,
                        List.of("likes:set:biz:" + member, meta(identity[0], id)));
                if (values != null && values.size() == 3) {
                    ContentStatsDTO dto = new ContentStatsDTO();
                    dto.setBizType(identity[0]); dto.setBizId(id);
                    dto.setLikedTimes(Long.valueOf(values.get(0).toString()));
                    dto.setViewTimes(Long.valueOf(values.get(1).toString()));
                    dto.setVersion(Long.valueOf(values.get(2).toString()));
                    mq.send(ContentStatsDTO.EXCHANGE, identity[0] + ".content.stats", dto);
                }
            } catch (RuntimeException error) {
                // Leave the pending marker intact; no false delivery acknowledgement.
                log.warn("内容统计同步暂未完成，将继续重试，类型：{}", error.getClass().getSimpleName());
            } finally {
                // XX prevents a fast consumer acknowledgement from being accidentally re-added.
                redis.execute(ROTATE, List.of(DIRTY), Long.toString(System.currentTimeMillis()), member);
            }
        }
    }

    @RabbitListener(bindings = @QueueBinding(
            value = @org.springframework.amqp.rabbit.annotation.Queue(value = "learntrail.content.stats.ack", durable = "true"),
            exchange = @org.springframework.amqp.rabbit.annotation.Exchange(value = ContentStatsDTO.EXCHANGE, type = ExchangeTypes.TOPIC),
            key = ContentStatsDTO.ACK_ROUTE))
    public void acknowledge(ContentStatsDTO dto) {
        if (!dto.valid("ARTICLE") && !dto.valid("SERIES")) throw new IllegalArgumentException("无效统计确认");
        redis.execute(ACK, List.of(meta(dto.getBizType(), dto.getBizId()), DIRTY),
                dto.getVersion().toString(), dto.getBizType() + ":" + dto.getBizId());
    }
}
