package com.tianji.learning.service.impl;

import com.tianji.api.client.course.CourseClient;
import com.tianji.api.dto.course.SectionInfoDTO;
import com.tianji.api.dto.leanring.LearningRecordFormDTO;
import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.ForbiddenException;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.domain.po.LearningLesson;
import com.tianji.learning.domain.po.LearningRecord;
import com.tianji.learning.enums.LessonStatus;
import com.tianji.learning.service.ILearningLessonService;
import com.tianji.learning.utils.LearningRecordDelayTaskHandler;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import java.time.LocalDateTime;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** Isolated access/regression tests: no Spring context, Redis, DB, or RabbitMQ. */
class LearningRecordAccessTest {
    private final ILearningLessonService lessons = mock(ILearningLessonService.class);
    private final CourseClient courses = mock(CourseClient.class);
    private final LearningRecordDelayTaskHandler delay = mock(LearningRecordDelayTaskHandler.class);
    private final RabbitMqHelper mq = mock(RabbitMqHelper.class);
    private final LearningRecordServiceImpl service = new LearningRecordServiceImpl(lessons, courses, delay, mq);
    private LearningLesson lesson;
    private LearningRecordFormDTO form;

    @BeforeEach
    void setup() {
        UserContext.setUser(2L);
        lesson = new LearningLesson().setId(10L).setUserId(2L).setCourseId(100L)
                .setStatus(LessonStatus.LEARNING).setExpireTime(LocalDateTime.now().plusDays(1));
        when(lessons.getById(10L)).thenReturn(lesson);
        form = new LearningRecordFormDTO();
        form.setLessonId(10L);
        form.setSectionId(20L);
        form.setSectionType(1);
        form.setDuration(100);
        form.setMoment(10);
        form.setCommitTime(LocalDateTime.now());
    }

    @AfterEach
    void cleanup() { UserContext.removeUser(); }

    @Test
    void anotherUsersLessonIsRejectedBeforeRemoteLookup() {
        lesson.setUserId(3L);
        assertThrows(ForbiddenException.class, () -> service.addLearningRecord(form));
        verifyNoInteractions(courses, delay, mq);
    }

    @Test
    void expiredEntitlementIsRejected() {
        lesson.setExpireTime(LocalDateTime.now().minusSeconds(1));
        assertThrows(ForbiddenException.class, () -> service.addLearningRecord(form));
        verifyNoInteractions(courses, delay, mq);
    }

    @Test
    void expiredStatusAlsoRejectsFutureExpiry() {
        lesson.setStatus(LessonStatus.EXPIRED);
        assertThrows(ForbiddenException.class, () -> service.addLearningRecord(form));
        verifyNoInteractions(courses, delay, mq);
    }

    @Test
    void sectionFromAnotherSeriesIsRejected() {
        when(courses.sectionInfo(20L)).thenReturn(new SectionInfoDTO(101L, 30L, false, 0));
        assertThrows(ForbiddenException.class, () -> service.addLearningRecord(form));
        verifyNoInteractions(delay, mq);
    }

    @Test
    void impossibleVideoProgressIsRejected() {
        when(courses.sectionInfo(20L)).thenReturn(new SectionInfoDTO(100L, 30L, false, 0));
        form.setMoment(101);
        assertThrows(BadRequestException.class, () -> service.addLearningRecord(form));
        verifyNoInteractions(delay, mq);
    }

    @Test
    void replayOfCompletedVideoKeepsCompletionAndDoesNotAwardPointsAgain() {
        lesson.setExpireTime(null);
        when(courses.sectionInfo(20L)).thenReturn(new SectionInfoDTO(100L, 30L, false, 0));
        LearningRecord previous = new LearningRecord();
        previous.setId(40L);
        previous.setFinished(true);
        when(delay.readRecordCache(10L, 20L)).thenReturn(previous);
        form.setMoment(80);
        service.addLearningRecord(form);
        ArgumentCaptor<LearningRecord> capture = ArgumentCaptor.forClass(LearningRecord.class);
        verify(delay).addLearningRecordTask(capture.capture());
        assertTrue(capture.getValue().getFinished());
        assertEquals(80, capture.getValue().getMoment());
        verifyNoInteractions(mq);
        verify(lessons, never()).lambdaUpdate();
    }
}
