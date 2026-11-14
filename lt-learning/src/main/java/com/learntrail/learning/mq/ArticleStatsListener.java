package com.learntrail.learning.mq;

import com.learntrail.api.dto.remark.ContentStatsDTO;
import com.learntrail.common.autoconfigure.mq.RabbitMqHelper;
import com.learntrail.learning.mapper.StudioArticleMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.amqp.core.ExchangeTypes;
import org.springframework.amqp.rabbit.annotation.*;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
public class ArticleStatsListener {
    private final StudioArticleMapper mapper;
    private final RabbitMqHelper mq;

    @RabbitListener(bindings = @QueueBinding(
            value = @Queue(value = "learntrail.article.stats", durable = "true"),
            exchange = @Exchange(value = ContentStatsDTO.EXCHANGE, type = ExchangeTypes.TOPIC),
            key = "ARTICLE.content.stats"))
    public void receive(ContentStatsDTO snapshot) {
        if (!snapshot.valid("ARTICLE")) throw new IllegalArgumentException("无效文章统计");
        // One auto-committed SQL statement completes before the acknowledgement.
        mapper.updateContentStats(snapshot);
        mq.send(ContentStatsDTO.EXCHANGE, ContentStatsDTO.ACK_ROUTE, snapshot);
    }
}
