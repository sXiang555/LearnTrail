package com.tianji.learning.service;

import com.tianji.api.client.course.CourseClient;
import com.tianji.api.dto.course.CourseFullInfoDTO;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.learning.domain.po.InteractionReply;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.UnauthorizedException;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.domain.vo.StudioPointsView;
import com.tianji.learning.mapper.StudioPointsMapper;
import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class StudioPointsServiceTest {
    private final StudioPointsMapper mapper = mock(StudioPointsMapper.class);
    private final CourseClient courses = mock(CourseClient.class);
    private final StudioPointsService service = new StudioPointsService(mapper, courses);

    @BeforeEach
    void setup() {
        UserContext.setUser(1L);
        when(mapper.lockBalance(1L)).thenReturn(1000);
        when(mapper.validLesson(1L, 10L)).thenReturn(null);
        when(mapper.changeBalance(eq(1L), anyInt())).thenReturn(1);
        when(mapper.rule(anyString())).thenAnswer(invocation -> {
            PointsTrade.Rule rule = new PointsTrade.Rule(); rule.setKind(invocation.getArgument(0));
            rule.setAmount(20); rule.setDailyCap(200); rule.setMinSeconds(30); return rule;
        });
        when(mapper.effectiveRead(1L, 2L, 30)).thenReturn(1);
        StudioPointsView.Article article = new StudioPointsView.Article();
        article.setId(2L);
        article.setTitle("文章");
        when(mapper.article(1L, "article")).thenReturn(article);
        when(courses.studioUnlockCost(10L)).thenReturn(100);
        CourseFullInfoDTO course = new CourseFullInfoDTO();
        course.setName("专栏");
        when(courses.getCourseInfoById(10L, false, false)).thenReturn(course);
    }

    @AfterEach
    void cleanup() { UserContext.removeUser(); }

    @Test
    void initialWalletAndFirstReadUseChosenAmounts() {
        StudioPointsView.Result result = service.read("article");
        assertEquals(1020, result.getBalance());
        assertEquals(20, result.getAwarded());
        verify(mapper).createWallet(1L, 1000);
        verify(mapper).addEntry(1L, "INITIAL", 0L, "初始积分", 1000);
        verify(mapper).addEntry(1L, "READ", 2L, "文章", 20);
    }

    @Test
    void repeatedReadDoesNotAwardAgain() {
        when(mapper.hasEntry(1L, "READ", 2L)).thenReturn(1);
        assertEquals(0, service.read("article").getAwarded());
        verify(mapper, never()).changeBalance(anyLong(), anyInt());
    }

    @Test
    void insufficientPointsDoNotCreateEntitlement() {
        when(mapper.lockBalance(1L)).thenReturn(99);
        assertThrows(BadRequestException.class, () -> service.unlock(10L));
        verify(mapper, never()).changeBalance(anyLong(), anyInt());
        verify(mapper, never()).grantLesson(anyLong(), anyLong(), anyLong());
    }

    @Test
    void existingEntitlementDoesNotCharge() {
        when(mapper.validLesson(1L, 10L)).thenReturn(11L);
        StudioPointsView.Result result = service.unlock(10L);
        assertEquals(0, result.getSpent());
        assertEquals(11L, result.getLessonId());
        verify(mapper, never()).changeBalance(anyLong(), anyInt());
    }

    @Test
    void unlockChargesServerCostAndCreatesRealLesson() {
        when(mapper.validLesson(1L, 10L)).thenReturn(null, 11L);
        StudioPointsView.Result result = service.unlock(10L);
        assertEquals(900, result.getBalance());
        assertEquals(100, result.getSpent());
        assertEquals(11L, result.getLessonId());
        verify(mapper).changeBalance(1L, -100);
        verify(mapper).grantLesson(anyLong(), eq(1L), eq(10L));
    }

    @Test
    void priorUnlockRestoresLessonWithoutChargingAgain() {
        when(mapper.validLesson(1L, 10L)).thenReturn(null, 11L);
        when(mapper.hasEntry(1L, "UNLOCK", 10L)).thenReturn(1);
        assertEquals(0, service.unlock(10L).getSpent());
        verify(mapper, never()).changeBalance(anyLong(), anyInt());
        verify(mapper).grantLesson(anyLong(), eq(1L), eq(10L));
    }

    @Test
    void missingOrFreeSeriesCannotBeUnlockedWithPoints() {
        when(courses.studioUnlockCost(10L)).thenReturn(null);
        assertThrows(BadRequestException.class, () -> service.unlock(10L));
        verifyNoInteractions(mapper);
    }

    @Test
    void missingArticleDoesNotReward() {
        assertThrows(BadRequestException.class, () -> service.read("missing"));
        verify(mapper, never()).changeBalance(anyLong(), anyInt());
    }

    @Test
    void externalReferenceDoesNotAwardOrCreateReadEntry() {
        StudioPointsView.Article article = new StudioPointsView.Article();
        article.setId(20L); article.setTitle("外部资料"); article.setSourceType("link");
        when(mapper.article(1L, "reference")).thenReturn(article);
        assertThrows(BadRequestException.class, () -> service.read("reference"));
        verify(mapper, never()).changeBalance(anyLong(), anyInt());
        verify(mapper, never()).addEntry(eq(1L), eq("READ"), anyLong(), anyString(), anyInt());
    }

    @Test
    void unauthenticatedRequestIsRejected() {
        UserContext.removeUser();
        assertThrows(UnauthorizedException.class, service::wallet);
        verifyNoInteractions(mapper, courses);
    }

    @Test void openingArticleWithoutEffectiveReadingDoesNotAward() {
        when(mapper.effectiveRead(1L, 2L, 30)).thenReturn(0);
        assertThrows(BadRequestException.class, () -> service.read("article"));
        verify(mapper, never()).changeBalance(anyLong(), anyInt());
    }
    @Test void dailyCapCannotBeExceeded() {
        when(mapper.earnedToday(eq(1L), eq("READ"), any())).thenReturn(200);
        assertEquals(0, service.read("article").getAwarded());
        verify(mapper, never()).addEntry(eq(1L), eq("READ"), anyLong(), anyString(), anyInt());
    }
    @Test void repeatedCommentBodyDoesNotAwardEvenWithAnotherReplyId() {
        InteractionReply reply = new InteractionReply().setId(3L).setUserId(1L)
                .setContent("这个事务应该同时覆盖积分流水和真实学习权益，否则出现部分成功时无法恢复。");
        when(mapper.rememberComment(eq(1L), anyString(), eq(3L))).thenReturn(0);
        service.rewardComment(reply);
        verify(mapper, never()).changeBalance(anyLong(), anyInt());
    }
    @Test void meaninglessCommentDoesNotEnterRewardFlow() {
        service.rewardComment(new InteractionReply().setId(3L).setUserId(1L).setContent("好好好好好好好好好好好好好好好好好"));
        verifyNoInteractions(mapper);
    }
}
