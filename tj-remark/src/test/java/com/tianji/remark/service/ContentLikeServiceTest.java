package com.tianji.remark.service;

import com.tianji.api.client.course.CourseClient;
import com.tianji.api.client.learning.LearningClient;
import com.tianji.api.dto.learning.StudioArticleLinkDTO;
import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import com.tianji.common.exceptions.*;
import com.tianji.common.utils.UserContext;
import com.tianji.remark.domain.dto.LikeRecordFormDTO;
import com.tianji.remark.service.impl.LikedRecordServiceRedisImpl;
import org.junit.jupiter.api.*;
import org.springframework.data.redis.core.*;
import org.springframework.data.redis.core.script.RedisScript;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class ContentLikeServiceTest {
    private final RabbitMqHelper mq = mock(RabbitMqHelper.class);
    private final RedisTemplate legacy = mock(RedisTemplate.class);
    private final StringRedisTemplate redis = mock(StringRedisTemplate.class);
    private final CourseClient courses = mock(CourseClient.class);
    private final LearningClient learning = mock(LearningClient.class);
    private final LikedRecordServiceRedisImpl service = new LikedRecordServiceRedisImpl(mq, legacy, redis, courses, learning);

    @BeforeEach void setup() { UserContext.setUser(42L); }
    @AfterEach void cleanup() { UserContext.removeUser(); }
    private LikeRecordFormDTO form(String type, boolean liked) {
        LikeRecordFormDTO form = new LikeRecordFormDTO();
        form.setBizId(1L); form.setBizType(type); form.setLiked(liked); return form;
    }
    private void publishedArticle() {
        StudioArticleLinkDTO article = new StudioArticleLinkDTO(); article.setId(1L);
        when(learning.studioArticleLinks(List.of(1L), false)).thenReturn(List.of(article));
    }

    @Test void anonymousCannotReadOrWriteLikes() {
        UserContext.removeUser();
        assertThrows(UnauthorizedException.class, () -> service.contentLikeState("ARTICLE", 1L));
        assertThrows(UnauthorizedException.class, () -> service.addLikeRecord(form("SERIES", true)));
        assertThrows(UnauthorizedException.class, () -> service.recordContentView("ARTICLE", 1L));
        verifyNoInteractions(redis, legacy, learning, courses);
    }
    @Test void unavailableArticleCannotMutateRedis() {
        when(learning.studioArticleLinks(List.of(1L), false)).thenReturn(List.of());
        assertThrows(ForbiddenException.class, () -> service.addLikeRecord(form("ARTICLE", true)));
        assertThrows(ForbiddenException.class, () -> service.contentLikeState("ARTICLE", 1L));
        assertThrows(ForbiddenException.class, () -> service.recordContentView("ARTICLE", 1L));
        verifyNoInteractions(redis, legacy);
    }
    @Test void mismatchedArticleIdentityIsRejected() {
        StudioArticleLinkDTO other = new StudioArticleLinkDTO(); other.setId(2L);
        when(learning.studioArticleLinks(List.of(1L), false)).thenReturn(List.of(other));
        assertThrows(ForbiddenException.class, () -> service.addLikeRecord(form("ARTICLE", true)));
        verifyNoInteractions(redis);
    }
    @Test void unavailableSeriesCannotMutateRedis() {
        when(courses.studioSeriesLikeable(1L)).thenReturn(false);
        assertThrows(ForbiddenException.class, () -> service.addLikeRecord(form("SERIES", true)));
        verifyNoInteractions(redis, legacy);
    }
    @Test void upstreamFailureCannotBecomeSuccessfulEmptyContent() {
        when(learning.studioArticleLinks(List.of(1L), false)).thenReturn(null);
        when(courses.studioSeriesLikeable(1L)).thenReturn(null);
        assertThrows(BadRequestException.class, () -> service.addLikeRecord(form("ARTICLE", true)));
        assertThrows(BadRequestException.class, () -> service.contentLikeState("SERIES", 1L));
        verifyNoInteractions(redis, legacy);
    }
    @Test void invalidTypeAndIdsAreRejectedBeforeRemoteCalls() {
        assertThrows(BadRequestException.class, () -> service.contentLikeState("QA", 1L));
        assertThrows(BadRequestException.class, () -> service.contentLikeState("ARTICLE", 0L));
        assertThrows(BadRequestException.class, () -> service.contentLikeState("SERIES", null));
        verifyNoInteractions(redis, learning, courses);
    }
    @Test void contentTypesUseDifferentKeysAndReadRealCountAndMembership() {
        publishedArticle(); when(courses.studioSeriesLikeable(1L)).thenReturn(true);
        List<String> keys = new ArrayList<>();
        doAnswer(call -> { keys.addAll(call.getArgument(1)); return List.of(9L, 1L, 7L); })
                .when(redis).execute(any(RedisScript.class), anyList(), eq("42"), anyString(), anyString());
        assertEquals(Map.of("bizId", 1L, "bizType", "ARTICLE", "likedTimes", 9L, "liked", true, "viewTimes", 7L),
                service.contentLikeState("ARTICLE", 1L));
        service.contentLikeState("SERIES", 1L);
        assertEquals(List.of("likes:set:biz:ARTICLE:1", "likes:content:stats:ARTICLE:1", "likes:content:dirty",
                "likes:set:biz:SERIES:1", "likes:content:stats:SERIES:1", "likes:content:dirty"), keys);
        verifyNoInteractions(legacy, mq);
    }
    @Test void contentWriteUsesServerIdentityAndExplicitLikeOrUnlike() {
        publishedArticle();
        List<List<String>> arguments = new ArrayList<>();
        doAnswer(call -> { arguments.add(List.of(call.getArgument(2), call.getArgument(3))); return 1L; })
                .when(redis).execute(any(RedisScript.class), anyList(), eq("42"), anyString(), anyString(), eq("ARTICLE:1"));
        service.addLikeRecord(form("ARTICLE", true)); service.addLikeRecord(form("ARTICLE", false));
        assertEquals(List.of(List.of("42", "1"), List.of("42", "0")), arguments);
        verifyNoInteractions(legacy, mq);
    }
    @Test void redisFailureIsNotReportedAsSuccess() {
        publishedArticle();
        assertThrows(IllegalStateException.class, () -> service.addLikeRecord(form("ARTICLE", true)));
        assertThrows(IllegalStateException.class, () -> service.contentLikeState("ARTICLE", 1L));
        assertThrows(IllegalStateException.class, () -> service.recordContentView("ARTICLE", 1L));
    }
    @Test void viewsUseAuthenticatedIdentityAndServerDayWithExplicitDedupResult() {
        publishedArticle();
        String day = java.time.LocalDate.now(java.time.ZoneId.of("Asia/Shanghai"))
                .format(java.time.format.DateTimeFormatter.BASIC_ISO_DATE);
        List<String> expected = List.of("likes:set:biz:ARTICLE:1", "likes:content:stats:ARTICLE:1",
                "likes:content:dirty", "likes:content:view:ARTICLE:1:" + day);
        when(redis.execute(any(RedisScript.class), eq(expected), eq("42"), anyString(), eq("ARTICLE:1")))
                .thenReturn(List.of(8L, 1L), List.of(8L, 0L));
        assertEquals(Map.of("viewTimes", 8L, "counted", true), service.recordContentView("ARTICLE", 1L));
        assertEquals(Map.of("viewTimes", 8L, "counted", false), service.recordContentView("ARTICLE", 1L));
        verifyNoInteractions(legacy, mq);
    }
    @Test void legacyQaKeepsItsExistingKeyAndCounterQueue() {
        SetOperations sets = mock(SetOperations.class); ZSetOperations sorted = mock(ZSetOperations.class);
        when(legacy.opsForSet()).thenReturn(sets); when(legacy.opsForZSet()).thenReturn(sorted);
        when(sets.add("likes:set:biz:1", "42")).thenReturn(1L);
        when(sets.size("likes:set:biz:1")).thenReturn(3L);
        service.addLikeRecord(form("QA", true));
        verify(sorted).add("likes:times:type:QA", "1", 3D);
        verifyNoInteractions(redis, learning, courses);
    }
}
