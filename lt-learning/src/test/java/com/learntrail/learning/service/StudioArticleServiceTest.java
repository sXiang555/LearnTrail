package com.learntrail.learning.service;

import com.learntrail.api.client.course.CourseClient;
import com.learntrail.api.client.user.UserClient;
import com.learntrail.common.exceptions.ForbiddenException;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.exceptions.UnauthorizedException;
import com.learntrail.common.utils.UserContext;
import com.learntrail.learning.domain.po.StudioArticle;
import com.learntrail.learning.mapper.StudioArticleMapper;
import org.junit.jupiter.api.*;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class StudioArticleServiceTest {
    private final StudioArticleMapper mapper = mock(StudioArticleMapper.class);
    private final UserClient users = mock(UserClient.class);
    private final CourseClient courses = mock(CourseClient.class);
    private final StudioArticleService service = new StudioArticleService(mapper, users, courses);
    private StudioArticle article;

    @BeforeEach
    void setup() {
        UserContext.setUser(2L);
        when(users.queryUserType(2L)).thenReturn(2);
        when(courses.studioAccess()).thenReturn(true);
        article = new StudioArticle();
        article.setId(10L);
        article.setAuthorUserId(2L);
        article.setStatus("draft");
        when(mapper.lock(10L)).thenReturn(article);
        when(mapper.selectById(10L)).thenReturn(article);
        when(mapper.insert(any(StudioArticle.class))).thenReturn(1);
        when(mapper.updateById(any(StudioArticle.class))).thenReturn(1);
    }

    @AfterEach
    void cleanup() { UserContext.removeUser(); }

    private StudioArticle.Edit form() {
        StudioArticle.Edit form = new StudioArticle.Edit();
        form.setTitle("文章"); form.setSummary("摘要"); form.setBody("正文");
        return form;
    }

    @Test
    void creationBindsCurrentUserAndStartsAsDraft() {
        service.create(form());
        verify(mapper).insert(argThat((StudioArticle row) -> row.getAuthorUserId().equals(2L)
                && row.getStatus().equals("draft") && row.getSlug().startsWith("article-")));
    }

    @Test
    void accountWithoutCreatorSpaceCannotCreate() {
        when(courses.studioAccess()).thenReturn(false);
        assertThrows(ForbiddenException.class, () -> service.create(form()));
        verify(mapper, never()).insert(any(StudioArticle.class));
    }

    @Test
    void foreignAuthorCannotReadOrModifyManagedArticle() {
        article.setAuthorUserId(3L);
        assertThrows(ForbiddenException.class, () -> service.editable(10L));
        assertThrows(ForbiddenException.class, () -> service.edit(10L, form()));
        verify(mapper, never()).updateById(any(StudioArticle.class));
    }

    @Test
    void authorCannotRepublishBlockedArticle() {
        article.setStatus("blocked");
        assertThrows(ForbiddenException.class, () -> service.state(10L, "published"));
        assertThrows(ForbiddenException.class, () -> service.edit(10L, form()));
    }

    @Test
    void ordinaryAuthorCannotUseOperatorBlockingAction() {
        assertThrows(ForbiddenException.class, () -> service.state(10L, "blocked"));
        verify(mapper, never()).updateById(any(StudioArticle.class));
    }

    @Test
    void realStaffCanManageSeedArticleWithoutAuthor() {
        when(users.queryUserType(2L)).thenReturn(1);
        article.setAuthorUserId(null);
        service.state(10L, "blocked");
        assertEquals("blocked", article.getStatus());
        verify(mapper).updateById(article);
    }

    @Test
    void missingUserServiceResponseDoesNotGrantOperatorAccess() {
        when(users.queryUserType(2L)).thenReturn(null);
        article.setAuthorUserId(3L);
        assertThrows(ForbiddenException.class, () -> service.edit(10L, form()));
    }

    @Test
    void publishPreservesIdentityAndSetsPublicationTime() {
        service.state(10L, "published");
        assertEquals(10L, article.getId());
        assertEquals(2L, article.getAuthorUserId());
        assertNotNull(article.getPublishedAt());
    }

    @Test
    void anonymousCannotCreate() {
        UserContext.removeUser();
        assertThrows(UnauthorizedException.class, () -> service.create(form()));
        verifyNoInteractions(mapper, users, courses);
    }

    @Test
    void anonymousCanReadPublishedContentWithoutRewardQuery() {
        UserContext.removeUser();
        article.setAuthorUserId(null);
        article.setStatus("published");
        when(mapper.selectOne(any())).thenReturn(article);
        assertSame(article, service.published("article-10"));
        assertFalse(article.isRewarded());
        verify(mapper, never()).rewarded(any(), any());
        verifyNoInteractions(users, courses);
    }

    @Test
    void anonymousCannotUseMineOrAdminScope() {
        UserContext.removeUser();
        assertThrows(UnauthorizedException.class, () -> service.page("mine", null, null, 1, 10));
        assertThrows(UnauthorizedException.class, () -> service.page("admin", null, null, 1, 10));
        verifyNoInteractions(mapper, users, courses);
    }

    @Test
    void publishedAuthorCountsRetainLongAuthorIdentity() {
        StudioArticle.AuthorCount count = new StudioArticle.AuthorCount();
        count.setAuthorUserId(2120072161584844802L);
        count.setArticleCount(3L);
        when(mapper.publishedCounts()).thenReturn(List.of(count));
        assertEquals(Map.of(2120072161584844802L, 3L), service.publishedCounts());
    }

    @Test
    void emptyPublicationCountsAreARealEmptyResult() {
        when(mapper.publishedCounts()).thenReturn(List.of());
        assertTrue(service.publishedCounts().isEmpty());
    }

    @Test
    void markdownLongBodyIsSavedWithoutTruncation() {
        StudioArticle.Edit edit = form();
        edit.setContentFormat("markdown");
        edit.setBody("长文章".repeat(20000));
        service.create(edit);
        verify(mapper).insert(argThat((StudioArticle row) -> row.getBody().length() == 60000
                && "markdown".equals(row.getContentFormat())));
    }

    @Test
    void blankOrOversizedBodyCannotBeWritten() {
        StudioArticle.Edit edit = form();
        edit.setBody("x".repeat(200001));
        assertThrows(BadRequestException.class, () -> service.create(edit));
        edit.setBody(" \n ");
        assertThrows(BadRequestException.class, () -> service.create(edit));
        verify(mapper, never()).insert(any(StudioArticle.class));
    }

    @Test
    void unknownFormatAndNullSourceTypeAreRejected() {
        StudioArticle.Edit edit = form();
        edit.setContentFormat("html");
        assertThrows(BadRequestException.class, () -> service.create(edit));
        edit.setContentFormat("text"); edit.setSourceType(null);
        assertThrows(BadRequestException.class, () -> service.create(edit));
        verify(mapper, never()).insert(any(StudioArticle.class));
    }

    private StudioArticle.Edit reference() {
        StudioArticle.Edit edit = form();
        edit.setSourceType("link"); edit.setSourceAuthor("原作者"); edit.setSourceUrl("https://example.com/docs");
        return edit;
    }

    @Test
    void referencePreservesAuthorAndUrlWithoutClaimingFullTextPermission() {
        StudioArticle.Edit edit = reference();
        edit.setLicenseNote("不能作为授权转载声明");
        service.create(edit);
        verify(mapper).insert(argThat((StudioArticle row) -> "原作者".equals(row.getSourceAuthor())
                && "https://example.com/docs".equals(row.getSourceUrl()) && row.getLicenseNote().isEmpty()
                && "link".equals(row.getSourceType()) && row.getAuthorUserId().equals(2L)));
    }

    @Test
    void unsafeOrOverlongSourceUrlsNeverReachInsert() {
        for (String url : List.of("javascript:alert(1)", "data:text/html,x", "//example.com", "https://user:pass@example.com",
                "https://example.com:99999/docs", "https://example.com/" + "字".repeat(1000))) {
            StudioArticle.Edit edit = reference(); edit.setSourceUrl(url);
            assertThrows(BadRequestException.class, () -> service.create(edit));
        }
        verify(mapper, never()).insert(any(StudioArticle.class));
    }

    @Test
    void externalSourcesRequireOriginalAuthor() {
        StudioArticle.Edit edit = reference(); edit.setSourceAuthor(" ");
        assertThrows(BadRequestException.class, () -> service.create(edit));
        verify(mapper, never()).insert(any(StudioArticle.class));
    }

    @Test
    void reprintRequiresPermissionOrOpenLicenseDescription() {
        StudioArticle.Edit edit = reference(); edit.setSourceType("reprint");
        assertThrows(BadRequestException.class, () -> service.create(edit));
        verify(mapper, never()).insert(any(StudioArticle.class));
        edit.setLicenseNote("作者书面授权，保留署名与链接");
        service.create(edit);
        verify(mapper).insert(argThat((StudioArticle row) -> row.getLicenseNote().contains("书面授权")));
    }

    @Test
    void externalReferenceIsLimitedToAReadingGuide() {
        StudioArticle.Edit edit = reference(); edit.setBody("x".repeat(5001));
        assertThrows(BadRequestException.class, () -> service.create(edit));
        verify(mapper, never()).insert(any(StudioArticle.class));
    }

    @Test
    void returningToOriginalClearsObsoleteSourceFields() {
        article.setSourceAuthor("旧作者"); article.setSourceUrl("https://example.com"); article.setLicenseNote("旧说明");
        service.edit(10L, form());
        assertEquals("", article.getSourceAuthor());
        assertEquals("", article.getSourceUrl());
        assertEquals("", article.getLicenseNote());
    }
}
