package com.tianji.course.mq;

import com.tianji.api.dto.remark.ContentStatsDTO;
import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import com.tianji.course.mapper.StudioMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.amqp.core.ExchangeTypes;
import org.springframework.amqp.rabbit.annotation.*;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
public class SeriesStatsListener {
    private final StudioMapper mapper;
    private final RabbitMqHelper mq;

    @RabbitListener(bindings = @QueueBinding(
            value = @Queue(value = "learntrail.series.stats", durable = "true"),
            exchange = @Exchange(value = ContentStatsDTO.EXCHANGE, type = ExchangeTypes.TOPIC),
            key = "SERIES.content.stats"))
    public void receive(ContentStatsDTO snapshot) {
        if (!snapshot.valid("SERIES")) throw new IllegalArgumentException("无效专栏统计");
        mapper.updateContentStats(snapshot);
        mq.send(ContentStatsDTO.EXCHANGE, ContentStatsDTO.ACK_ROUTE, snapshot);
    }
}
