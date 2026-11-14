package com.learntrail.learning.service.impl;

import com.learntrail.api.client.course.*;
import com.learntrail.api.client.user.UserClient;
import com.learntrail.api.dto.course.SectionInfoDTO;
import com.learntrail.common.exceptions.*;
import com.learntrail.common.utils.UserContext;
import com.learntrail.learning.domain.dto.QuestionFormDTO;
import com.learntrail.learning.domain.po.InteractionQuestion;
import com.learntrail.learning.domain.query.QuestionPageQuery;
import com.learntrail.learning.domain.vo.QuestionVO;
import com.learntrail.learning.mapper.InteractionReplyMapper;
import com.learntrail.learning.service.DiscussionAccess;
import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DiscussionSecurityTest {
    private final CourseClient courses = mock(CourseClient.class);
    private final UserClient users = mock(UserClient.class);
    private final DiscussionAccess access = new DiscussionAccess(courses, users);
    private final InteractionQuestionServiceImpl service = spy(new InteractionQuestionServiceImpl(
            mock(InteractionReplyMapper.class), users, courses, null, null, null, null, access));

    @BeforeEach void setup() { UserContext.setUser(2L); when(courses.studioCanDiscuss(10L)).thenReturn(true); }
    @AfterEach void cleanup() { UserContext.removeUser(); }
    private InteractionQuestion question() {
        return new InteractionQuestion().setId(3L).setCourseId(10L).setUserId(2L).setTitle("问题").setAnonymity(true).setHidden(false);
    }

    @Test void detailReturnsRealValueWithoutLeakingAnonymousIdentity() {
        doReturn(question()).when(service).getById(3L);
        QuestionVO result = service.queryQuestionById(3L);
        assertNotNull(result); assertEquals("问题", result.getTitle()); assertTrue(result.isMine());
        assertNull(result.getUserId()); assertNull(result.getUserName());
        verify(users, never()).queryUserById(anyLong());
    }
    @Test void hiddenQuestionCannotBeRead() {
        doReturn(question().setHidden(true)).when(service).getById(3L);
        assertThrows(BadRequestException.class, () -> service.queryQuestionById(3L));
        verifyNoInteractions(courses);
    }
    @Test void anotherUserCannotEditOrDelete() {
        doReturn(question().setUserId(99L)).when(service).getById(3L);
        assertThrows(ForbiddenException.class, () -> service.updateQuestion(3L, new QuestionFormDTO()));
        assertThrows(ForbiddenException.class, () -> service.deleteById(3L));
        verify(service, never()).removeById(anyLong());
    }
    @Test void unownedCourseAndMissingIdentityAreDenied() {
        assertThrows(ForbiddenException.class, () -> access.course(99L));
        UserContext.removeUser();
        assertThrows(UnauthorizedException.class, () -> access.course(10L));
    }
    @Test void whitespaceAndOversizedBodiesAreDenied() {
        assertThrows(BadRequestException.class, () -> access.text("  \n", 254));
        assertThrows(BadRequestException.class, () -> access.text("x".repeat(10001), 10000));
    }
    @Test void clientSortingIsIgnoredAndPageSizeIsBounded() {
        QuestionPageQuery query = new QuestionPageQuery(); query.setSortBy("bad SQL");
        access.page(query); assertEquals("id", query.getSortBy()); assertFalse(query.getIsAsc());
        query.setPageSize(31); assertThrows(BadRequestException.class, () -> access.page(query));
    }
    @Test void foreignSectionCannotBeAttachedToQuestion() {
        when(courses.sectionInfo(4L)).thenReturn(new SectionInfoDTO(99L, 1L, false, 0));
        QuestionFormDTO form = new QuestionFormDTO(); form.setCourseId(10L); form.setSectionId(4L);
        form.setTitle("问题"); form.setDescription("描述");
        assertThrows(BadRequestException.class, () -> service.saveQuestion(form));
        verify(service, never()).save(any(InteractionQuestion.class));
    }
}
