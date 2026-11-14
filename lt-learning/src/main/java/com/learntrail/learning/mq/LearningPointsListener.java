package com.learntrail.learning.mq;

import com.learntrail.common.constants.MqConstants;
import com.learntrail.learning.constants.RedisConstants;
import com.learntrail.learning.enums.PointsRecordType;
import com.learntrail.learning.mq.message.SignInMessage;
import com.learntrail.learning.service.IPointsRecordService;
import lombok.RequiredArgsConstructor;
import org.springframework.amqp.core.ExchangeTypes;
import org.springframework.amqp.rabbit.annotation.Exchange;
import org.springframework.amqp.rabbit.annotation.Queue;
import org.springframework.amqp.rabbit.annotation.QueueBinding;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
public class LearningPointsListener {

    private final IPointsRecordService recordService;


    // 监听写回复消息，给用户加分
    @RabbitListener(bindings = @QueueBinding(
            exchange = @Exchange(
                    name = MqConstants.Exchange.LEARNING_EXCHANGE,
                    type = ExchangeTypes.TOPIC),
            value = @Queue(name = "qa.points.queue", durable = "true"),
            key = MqConstants.Key.WRITE_REPLY
    ))
    public void listenWriteReply(Long userId) {
        recordService.addPointsRecord(userId, 5, PointsRecordType.QA);
    }


    //监听用户签到消息，给用户加分
    @RabbitListener(bindings = @QueueBinding(
            exchange = @Exchange(
                    name = MqConstants.Exchange.LEARNING_EXCHANGE,
                    type = ExchangeTypes.TOPIC
            ),
            value = @Queue(name = "sign.points.queue", durable = "true"),
            key = MqConstants.Key.SIGN_IN
    ))
    public void listenUserSign(SignInMessage message) {
        recordService.addPointsRecord(message.getUserId(), message.getPoints(), PointsRecordType.SIGN);
    }

    //监听学习小节
    @RabbitListener(bindings = @QueueBinding(
            exchange = @Exchange(
                    name = MqConstants.Exchange.LEARNING_EXCHANGE,
                    type = ExchangeTypes.TOPIC
            ),
            value = @Queue(name = "learning.points.queue", durable = "true"),
            key = MqConstants.Key.LEARN_SECTION
    ))
    public void listenLearning(Long userId) {
        recordService.addPointsRecord(userId, 10, PointsRecordType.LEARNING);
    }

    @RabbitListener(bindings = @QueueBinding(
            exchange = @Exchange(
                    name = MqConstants.Exchange.LEARNING_EXCHANGE,
                    type = ExchangeTypes.TOPIC
            ),
            value = @Queue(name = "note.points.queue", durable = "true"),
            key = MqConstants.Key.WRITE_NOTE
    ))
    public void listenWriteNote(Long userId) {
        recordService.addPointsRecord(userId, 10, PointsRecordType.NOTE);
    }





}
