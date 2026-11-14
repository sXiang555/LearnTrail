package com.tianji.learning.service;

import com.tianji.api.client.course.CourseClient;
import com.tianji.api.dto.course.*;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.common.exceptions.*;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.domain.po.LearningActivity;
import com.tianji.learning.domain.vo.StudioPointsView;
import com.tianji.learning.mapper.*;
import org.junit.jupiter.api.*;
import org.springframework.test.util.ReflectionTestUtils;
import java.time.*;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class LearningActivityServiceTest {
    private final LearningActivityMapper mapper = mock(LearningActivityMapper.class);
    private final StudioPointsMapper points = mock(StudioPointsMapper.class);
    private final StudioPointsService wallet = mock(StudioPointsService.class);
    private final CourseClient courses = mock(CourseClient.class);
    private final ISignRecordService signs = mock(ISignRecordService.class);
    private final LearningActivityService service = new LearningActivityService(mapper, points, wallet, courses, signs);
    private final Instant start = Instant.parse("2026-11-11T00:00:00Z");
    private LearningActivity activity;
    @BeforeEach void setup() {
        UserContext.setUser(1L); time(0);
        activity = new LearningActivity(); activity.setId(5L); activity.setUserId(1L); activity.setKind("READ");
        activity.setTargetId(10L); activity.setCreditedSeconds(0); activity.setLastPosition(0.0);
        activity.setLastSeen(LocalDateTime.ofInstant(start, ZoneId.of("Asia/Shanghai")));
        when(mapper.lock(5L, 1L)).thenReturn(activity);
        PointsTrade.Rule rule = new PointsTrade.Rule(); rule.setAmount(20); rule.setDailyCap(200); rule.setMinSeconds(30);
        when(wallet.rule(anyString())).thenReturn(rule);
        StudioPointsView.Article article = new StudioPointsView.Article(); article.setId(10L); article.setSlug("article"); article.setTitle("文章");
        when(points.articleById(1L, 10L)).thenReturn(article);
        StudioPointsView.Result result = new StudioPointsView.Result(); result.setAwarded(20);
        when(wallet.read("article")).thenReturn(result);
        StudioPointsView.Wallet account = new StudioPointsView.Wallet(); account.setBalance(1020); when(wallet.wallet()).thenReturn(account);
    }
    @AfterEach void cleanup() { UserContext.removeUser(); }
    private void time(int seconds) { ReflectionTestUtils.setField(service, "clock", Clock.fixed(start.plusSeconds(seconds), ZoneId.of("Asia/Shanghai"))); }
    private void video() {
        activity.setKind("WATCH"); when(courses.sectionInfo(10L)).thenReturn(new SectionInfoDTO(2L, 3L, false, 0));
        when(courses.studioCanDiscuss(2L)).thenReturn(true);
        CatalogueDTO episode = new CatalogueDTO(); episode.setId(10L); episode.setMediaDuration(120); episode.setName("视频");
        CourseFullInfoDTO course = new CourseFullInfoDTO(); course.setChapters(List.of(episode));
        when(courses.getCourseInfoById(2L, true, false)).thenReturn(course);
    }
    @Test void rapidRetriesCannotAccumulateTime() {
        time(2); assertEquals(0, service.heartbeat(5L, 1).getSeconds());
        verify(wallet, never()).read(anyString()); verify(mapper, never()).update(any());
    }
    @Test void validReadingRequiresTimeAndEndPosition() {
        time(10); service.heartbeat(5L, .2); time(20); service.heartbeat(5L, .7);
        verify(wallet, never()).read(anyString());
        time(30); assertEquals(20, service.heartbeat(5L, .9).getAwarded());
    }
    @Test void elapsedTimeAloneWithoutReadingPositionDoesNotAward() {
        time(10); service.heartbeat(5L, .1); time(20); service.heartbeat(5L, .1); time(30); service.heartbeat(5L, .1);
        verify(wallet, never()).read(anyString());
    }
    @Test void inactiveGapDoesNotCountAsLearning() {
        time(100); assertEquals(0, service.heartbeat(5L, 1).getSeconds());
        verify(wallet, never()).read(anyString());
    }
    @Test void seekingToEndDoesNotCountAsWatchedTime() {
        video(); time(10); assertEquals(0, service.heartbeat(5L, 100).getSeconds());
        verify(wallet, never()).award(anyString(), anyLong(), anyString());
    }
    @Test void continuousVideoProgressAccumulatesActualTime() {
        video(); time(10); assertEquals(10, service.heartbeat(5L, 10).getSeconds());
    }
    @Test void previewWithoutFullPermissionCannotStartRewardSession() {
        video(); when(courses.studioCanDiscuss(2L)).thenReturn(false);
        assertThrows(ForbiddenException.class, () -> service.start("WATCH", 10L));
        verify(mapper, never()).insert(any());
    }
    @Test void foreignActivityAndAnonymousRequestsAreDenied() {
        UserContext.setUser(9L); assertThrows(BadRequestException.class, () -> service.heartbeat(5L, 1));
        UserContext.removeUser(); assertThrows(UnauthorizedException.class, () -> service.start("READ", 10L));
    }
}
