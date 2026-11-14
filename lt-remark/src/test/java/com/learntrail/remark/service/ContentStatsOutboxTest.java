package com.learntrail.remark.service;

import com.learntrail.api.dto.remark.ContentStatsDTO;
import com.learntrail.common.autoconfigure.mq.RabbitMqHelper;
import com.learntrail.remark.task.ContentStatsOutbox;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ZSetOperations;
import org.springframework.data.redis.core.script.RedisScript;
import java.util.List;
import java.util.Set;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ContentStatsOutboxTest {
    private final StringRedisTemplate redis = mock(StringRedisTemplate.class);
    private final ZSetOperations<String, String> pending = mock(ZSetOperations.class);
    private final RabbitMqHelper mq = mock(RabbitMqHelper.class);
    private final ContentStatsOutbox outbox = new ContentStatsOutbox(redis, mq);
    private void snapshot() {
        when(redis.opsForZSet()).thenReturn(pending);
        when(pending.range(ContentStatsOutbox.DIRTY, 0, 29)).thenReturn(Set.of("ARTICLE:1"));
        when(redis.execute(any(RedisScript.class), eq(List.of("likes:set:biz:ARTICLE:1", "likes:content:stats:ARTICLE:1"))))
                .thenReturn(List.of(3L, "8", "1001"));
    }
    @Test void publishUsesVersionedSnapshotAndDoesNotPopPendingOnSend() {
        snapshot(); outbox.publishPending();
        verify(mq).send(eq(ContentStatsDTO.EXCHANGE), eq("ARTICLE.content.stats"), argThat((ContentStatsDTO item) ->
                item.valid("ARTICLE") && item.getLikedTimes() == 3 && item.getViewTimes() == 8 && item.getVersion() == 1001));
        verify(pending, never()).remove(anyString(), any());
        verify(redis).execute(argThat((RedisScript<Long> script) -> script.getScriptAsString().contains("'XX'")),
                eq(List.of(ContentStatsOutbox.DIRTY)), anyString(), eq("ARTICLE:1"));
    }
    @Test void failedSendIsRetriedWithoutDroppingSnapshot() {
        snapshot(); doThrow(new IllegalStateException("offline")).when(mq).send(anyString(), anyString(), any());
        assertDoesNotThrow(outbox::publishPending);
        assertDoesNotThrow(outbox::publishPending);
        verify(mq, times(2)).send(anyString(), anyString(), any());
        verify(pending, never()).remove(anyString(), any());
    }
    @Test void acknowledgementIsConditionalOnExactVersion() {
        ContentStatsDTO item = new ContentStatsDTO(); item.setBizType("SERIES"); item.setBizId(1L);
        item.setLikedTimes(0L); item.setViewTimes(3L); item.setVersion(42L);
        outbox.acknowledge(item);
        verify(redis).execute(argThat((RedisScript<Long> script) -> script.getScriptAsString().contains("==ARGV[1]")),
                eq(List.of("likes:content:stats:SERIES:1", ContentStatsOutbox.DIRTY)), eq("42"), eq("SERIES:1"));
    }
    @Test void invalidAcknowledgementCannotRemovePending() {
        ContentStatsDTO item = new ContentStatsDTO(); item.setBizType("QA");
        assertThrows(IllegalArgumentException.class, () -> outbox.acknowledge(item));
        verifyNoInteractions(redis);
    }
}
