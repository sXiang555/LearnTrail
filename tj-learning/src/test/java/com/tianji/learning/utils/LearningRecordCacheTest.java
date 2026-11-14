package com.tianji.learning.utils;

import com.tianji.learning.domain.po.LearningRecord;
import com.tianji.learning.mapper.LearningRecordMapper;
import com.tianji.learning.service.ILearningLessonService;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.HashOperations;
import org.springframework.data.redis.core.StringRedisTemplate;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LearningRecordCacheTest {
    @Test
    void compactCachedProgressCanBeReadAndWrittenAgain() {
        StringRedisTemplate redis = mock(StringRedisTemplate.class);
        HashOperations<String, Object, Object> hashes = mock(HashOperations.class);
        when(redis.opsForHash()).thenReturn(hashes);
        when(hashes.get("learning:record:10", "20"))
                .thenReturn("{\"id\":\"30\",\"moment\":25,\"finished\":false}");
        LearningRecordDelayTaskHandler handler = new LearningRecordDelayTaskHandler(
                redis, mock(LearningRecordMapper.class), mock(ILearningLessonService.class));
        LearningRecord record = handler.readRecordCache(10L, 20L);
        assertNotNull(record);
        assertEquals(10L, record.getLessonId()); assertEquals(20L, record.getSectionId());
        assertEquals(30L, record.getId()); assertEquals(25, record.getMoment());
        handler.writeRecordCache(record);
        // 原问题被 catch 吞掉而不抛出，因此必须确认确实完成回写。
        verify(hashes).put(eq("learning:record:10"), eq("20"), argThat(value -> value.toString().contains("\"moment\":25")));
    }
}
