package com.tianji.course.service;

import com.tianji.api.client.learning.LearningClient;
import com.tianji.api.client.media.StudioMediaClient;
import com.tianji.api.client.user.UserClient;
import com.tianji.api.dto.leanring.LearningLessonDTO;
import com.tianji.api.dto.leanring.LearningRecordDTO;
import com.tianji.api.dto.learning.StudioArticleLinkDTO;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.ForbiddenException;
import com.tianji.common.utils.UserContext;
import com.tianji.course.domain.po.CourseCatalogueDraft;
import com.tianji.course.domain.vo.StudioView;
import com.tianji.course.mapper.StudioMapper;
import org.junit.jupiter.api.*;
import org.mockito.ArgumentCaptor;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class StudioLifecycleTest {
    private final StudioMapper mapper = mock(StudioMapper.class);
    private final LearningClient learning = mock(LearningClient.class);
    private final StudioMediaClient media = mock(StudioMediaClient.class);
    private final ICategoryService categories = mock(ICategoryService.class);
    private final ICourseDraftService drafts = mock(ICourseDraftService.class);
    private final ICourseCatalogueDraftService draftCatalogue = mock(ICourseCatalogueDraftService.class);
    private final ICourseCatalogueService catalogue = mock(ICourseCatalogueService.class);
    private final UserClient users = mock(UserClient.class);
    private final StudioService service = new StudioService(mapper, learning, media, categories, drafts, draftCatalogue, catalogue, users);
    private StudioView.Series series;

    @BeforeEach
    void setup() {
        UserContext.setUser(2L);
        series = new StudioView.Series();
        series.setId(10L); series.setCourseId(100L); series.setCategoryId(3L);
        series.setStatus("draft"); series.setUnlockPoints(100); series.setPreviouslyPublished(true);
        when(mapper.lockSeries(10L, 2L)).thenReturn(series);
        when(mapper.changeState(anyLong(), anyLong(), anyString())).thenReturn(1);
        when(users.queryUserType(2L)).thenReturn(2);
    }
    @AfterEach
    void cleanup() { UserContext.removeUser(); }

    @Test void likesRequirePublicSeriesRegardlessOfPriceOrOwnership() {
        when(mapper.find(10L, null, null)).thenReturn(series);
        series.setStatus("published"); series.setAvailable(true);
        series.setFree(1); assertTrue(service.seriesLikeable(10L));
        series.setFree(0); assertTrue(service.seriesLikeable(10L));
        series.setStatus("draft"); assertFalse(service.seriesLikeable(10L));
        series.setStatus("unlisted"); assertFalse(service.seriesLikeable(10L));
        series.setStatus("published"); series.setBlocked(true); assertFalse(service.seriesLikeable(10L));
        series.setBlocked(false); series.setAvailable(false); assertFalse(service.seriesLikeable(10L));
        assertFalse(service.seriesLikeable(11L));
        verifyNoInteractions(learning);
    }

    @Test
    void blockedCreatorCannotRepublishOrStartRevision() {
        series.setBlocked(true);
        assertThrows(ForbiddenException.class, () -> service.publish(10L));
        assertThrows(ForbiddenException.class, () -> service.beginRevision(10L));
        verifyNoInteractions(drafts);
    }

    @Test
    void unlistingDoesNotDeleteLearningEntitlements() {
        series.setStatus("published");
        service.changeState(10L, "unlisted");
        verify(mapper).changeState(10L, 2L, "unlisted");
        verifyNoInteractions(learning, drafts);
    }

    @Test
    void publishedRevisionCannotChangeUnlockCost() {
        StudioView.SeriesCreate form = new StudioView.SeriesCreate();
        form.setUnlockPoints(101); form.setCategoryId(3L);
        assertThrows(BadRequestException.class, () -> service.editDraft(10L, form));
        verifyNoInteractions(drafts);
    }

    @Test
    void ordinaryAccountCannotModerate() {
        assertThrows(ForbiddenException.class, () -> service.moderate(10L, true));
        verify(mapper, never()).setBlocked(anyLong(), anyBoolean());
    }

    @Test
    void unchangedVideoKeepsSectionId() {
        StudioView.DraftEpisode old = new StudioView.DraftEpisode();
        old.setId(20L); old.setMediaId(30L);
        when(mapper.draftEpisodes(100L)).thenReturn(List.of(old));
        when(mapper.draftChapter(100L)).thenReturn(99L);
        StudioMediaClient.Asset asset = new StudioMediaClient.Asset();
        asset.setId(30L); asset.setFilename("video.mp4"); asset.setDuration(70); asset.setReady(true);
        when(media.ownedAssets(anyList(), eq(100L))).thenReturn(List.of(asset));
        when(draftCatalogue.saveBatch(anyCollection())).thenReturn(true);
        StudioView.EpisodeEdit item = new StudioView.EpisodeEdit();
        item.setMediaId(30L); item.setTitle("更新标题");
        StudioView.EpisodesEdit form = new StudioView.EpisodesEdit();
        form.setEpisodes(List.of(item)); form.setPreviewMediaId(30L);
        service.saveEpisodes(10L, form);
        ArgumentCaptor<Collection<CourseCatalogueDraft>> rows = ArgumentCaptor.forClass(Collection.class);
        verify(draftCatalogue).saveBatch(rows.capture());
        assertEquals(20L, rows.getValue().iterator().next().getId());
        verify(mapper).updatePreview(10L, 20L, 3);
    }

    @Test
    void progressIgnoresRetiredSectionsAndDuplicateRecords() {
        series.setAvailable(false); series.setEpisodeCount(2);
        when(mapper.find(null, "series", 2L)).thenReturn(series);
        StudioView.Episode first = new StudioView.Episode(); first.setId(20L);
        StudioView.Episode second = new StudioView.Episode(); second.setId(21L);
        when(mapper.episodes(100L)).thenReturn(List.of(first, second));
        when(learning.isLessonValid(100L)).thenReturn(50L);
        LearningLessonDTO lesson = new LearningLessonDTO(); lesson.setId(50L);
        LearningRecordDTO current = new LearningRecordDTO(); current.setSectionId(20L); current.setFinished(true);
        LearningRecordDTO retired = new LearningRecordDTO(); retired.setSectionId(90L); retired.setFinished(true);
        lesson.setRecords(List.of(current, current, retired));
        when(learning.queryLearningRecordByCourse(100L)).thenReturn(lesson);
        StudioView.Series result = service.detail("series");
        assertTrue(result.isOwned());
        assertEquals(50, result.getProgressPercent());
    }

    @Test void publicDetailOnlyExposesPublishedArticleReferences() {
        series.setAvailable(true);
        when(mapper.find(null, "series", 2L)).thenReturn(series);
        when(mapper.articleIds(10L, "published")).thenReturn(List.of(20L, 21L));
        StudioArticleLinkDTO published = new StudioArticleLinkDTO(); published.setId(20L); published.setSlug("article");
        when(learning.studioArticleLinks(List.of(20L, 21L), false)).thenReturn(List.of(published));
        assertEquals(List.of(published), service.detail("series").getArticles());
    }
    @Test void draftReturnsSelectedIdsForEditingAndUnlinking() {
        when(mapper.articleIds(10L, "draft")).thenReturn(List.of(20L, 21L));
        StudioView.Series result = (StudioView.Series) service.draft(10L).get("series");
        assertEquals(List.of(20L, 21L), result.getArticleIds());
    }
    @Test void revisionCopiesPublishedAssociationsIntoDraftWithoutReplacingPublishedVersion() {
        series.setStatus("published"); when(mapper.beginRevision(eq(10L), eq(2L), anyString())).thenReturn(1);
        service.beginRevision(10L);
        verify(mapper).clearArticles(10L, "draft"); verify(mapper).copyArticles(10L, "published", "draft");
        verify(mapper, never()).clearArticles(10L, "published");
    }
    @Test void articleServiceFailureIsNotTreatedAsEmptyRelation() {
        series.setAvailable(true); when(mapper.find(null, "series", 2L)).thenReturn(series);
        when(mapper.articleIds(10L, "published")).thenReturn(List.of(20L));
        when(learning.studioArticleLinks(List.of(20L), false)).thenReturn(null);
        assertThrows(BadRequestException.class, () -> service.detail("series"));
    }
}
