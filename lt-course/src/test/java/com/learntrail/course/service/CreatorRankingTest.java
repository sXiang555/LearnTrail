package com.learntrail.course.service;

import com.learntrail.api.client.learning.LearningClient;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.utils.UserContext;
import com.learntrail.course.domain.vo.StudioView;
import com.learntrail.course.mapper.StudioMapper;
import org.junit.jupiter.api.*;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class CreatorRankingTest {
    private final StudioMapper mapper = mock(StudioMapper.class);
    private final LearningClient learning = mock(LearningClient.class);
    private final StudioService service = new StudioService(mapper, learning, null, null, null, null, null, null);

    @BeforeEach
    void setup() { UserContext.setUser(2L); }
    @AfterEach
    void cleanup() { UserContext.removeUser(); }

    @Test
    void secondPageContinuesRanksAndUsesSameCountsForTotalAndRows() {
        Map<Long, Long> counts = Map.of(2L, 3L);
        StudioView.Creator creator = new StudioView.Creator();
        creator.setSeriesCount(1); creator.setArticleCount(3L);
        when(learning.studioPublishedArticleCounts()).thenReturn(counts);
        when(mapper.countCreators(null, 2L, counts)).thenReturn(2L);
        when(mapper.creatorsPage(null, 2L, 1, 1, counts)).thenReturn(List.of(creator));
        var result = service.creatorsPage(null, 2, 1);
        assertEquals(2, creator.getRank());
        assertEquals(4L, creator.getContentCount());
        assertEquals(2L, result.getTotal());
        verify(mapper).countCreators(null, 2L, counts);
    }

    @Test
    void searchResultsDoNotClaimGlobalRank() {
        StudioView.Creator creator = new StudioView.Creator();
        when(learning.studioPublishedArticleCounts()).thenReturn(Map.of());
        when(mapper.creatorsPage("工坊", 2L, 0, 12, Map.of())).thenReturn(List.of(creator));
        service.creatorsPage("工坊", 1, 12);
        assertNull(creator.getRank());
    }

    @Test
    void failedCountQueryDoesNotPublishAnIncompleteRanking() {
        when(learning.studioPublishedArticleCounts()).thenThrow(new BadRequestException("统计不可用"));
        assertThrows(BadRequestException.class, () -> service.creatorsPage(null, 1, 12));
        verifyNoInteractions(mapper);
    }

    @Test
    void missingCountResponseIsNotTreatedAsZeroPublications() {
        when(learning.studioPublishedArticleCounts()).thenReturn(null);
        assertThrows(BadRequestException.class, () -> service.creatorsPage(null, 1, 12));
        verifyNoInteractions(mapper);
    }
}
