package com.tianji.learning.service;

import com.tianji.api.dto.remark.ContentStatsDTO;
import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import com.tianji.learning.mapper.StudioArticleMapper;
import com.tianji.learning.mq.ArticleStatsListener;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class ArticleStatsListenerTest {
    private final StudioArticleMapper mapper = mock(StudioArticleMapper.class);
    private final RabbitMqHelper mq = mock(RabbitMqHelper.class);
    private final ArticleStatsListener listener = new ArticleStatsListener(mapper, mq);
    private ContentStatsDTO snapshot() {
        ContentStatsDTO dto = new ContentStatsDTO(); dto.setBizType("ARTICLE"); dto.setBizId(1L);
        dto.setLikedTimes(2L); dto.setViewTimes(5L); dto.setVersion(100L); return dto;
    }
    @Test void acknowledgeOnlyAfterDatabaseStatement() {
        ContentStatsDTO dto = snapshot(); listener.receive(dto);
        var ordered = inOrder(mapper, mq);
        ordered.verify(mapper).updateContentStats(dto);
        ordered.verify(mq).send(ContentStatsDTO.EXCHANGE, ContentStatsDTO.ACK_ROUTE, dto);
    }
    @Test void databaseFailureDoesNotAcknowledge() {
        ContentStatsDTO dto = snapshot();
        when(mapper.updateContentStats(dto)).thenThrow(new IllegalStateException("database unavailable"));
        assertThrows(IllegalStateException.class, () -> listener.receive(dto));
        verifyNoInteractions(mq);
    }
    @Test void duplicateOrOlderSnapshotCanBeAcknowledgedAfterNoOpUpdate() {
        ContentStatsDTO dto = snapshot(); when(mapper.updateContentStats(dto)).thenReturn(0);
        listener.receive(dto); verify(mq).send(ContentStatsDTO.EXCHANGE, ContentStatsDTO.ACK_ROUTE, dto);
    }
    @Test void invalidTypeAndNegativeCountersCannotReachDatabase() {
        ContentStatsDTO dto = snapshot(); dto.setBizType("SERIES");
        assertThrows(IllegalArgumentException.class, () -> listener.receive(dto));
        dto.setBizType("ARTICLE"); dto.setViewTimes(-1L);
        assertThrows(IllegalArgumentException.class, () -> listener.receive(dto));
        verifyNoInteractions(mapper, mq);
    }
}
