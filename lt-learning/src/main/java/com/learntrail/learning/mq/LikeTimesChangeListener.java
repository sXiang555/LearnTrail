package com.learntrail.learning.mq;

import com.learntrail.api.dto.remark.LikedTimesDTO;
import com.learntrail.common.utils.BeanUtils;
import com.learntrail.common.utils.CollUtils;
import com.learntrail.learning.domain.po.InteractionReply;
import com.learntrail.learning.service.IInteractionReplyService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.ExchangeTypes;
import org.springframework.amqp.rabbit.annotation.Exchange;
import org.springframework.amqp.rabbit.annotation.Queue;
import org.springframework.amqp.rabbit.annotation.QueueBinding;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.util.List;

import static com.learntrail.common.constants.MqConstants.Exchange.LIKE_RECORD_EXCHANGE;
import static com.learntrail.common.constants.MqConstants.Key.QA_LIKED_TIMES_KEY;

@Slf4j
@Component
@RequiredArgsConstructor
public class LikeTimesChangeListener {

    private final IInteractionReplyService replyService;

    @RabbitListener(bindings = @QueueBinding(
            value = @Queue(name = "qa.liked.times.queue", durable = "true"),
            exchange = @Exchange(name = LIKE_RECORD_EXCHANGE, type = ExchangeTypes.TOPIC),
            key = QA_LIKED_TIMES_KEY
    ))
    public void listenReplyLikedTimesChange(List<LikedTimesDTO> likedTimesDTOs){
        if (CollUtils.isEmpty(likedTimesDTOs))
            return;
        List<InteractionReply> replies = CollUtils.newArrayList();
        for (LikedTimesDTO likedTimesDTO : likedTimesDTOs) {
            InteractionReply reply = new InteractionReply();
            reply.setId(likedTimesDTO.getBizId());
            reply.setLikedTimes(likedTimesDTO.getLikedTimes());
            replies.add(reply);
        }

        replyService.updateBatchById(replies);
    }
}
