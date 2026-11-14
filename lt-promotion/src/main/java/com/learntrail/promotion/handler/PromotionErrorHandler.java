package com.learntrail.promotion.handler;

import com.learntrail.promotion.domain.dto.UserCouponDTO;
import com.learntrail.promotion.service.IUserCouponService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
@Slf4j
public class PromotionErrorHandler {

    private final IUserCouponService userCouponService;


    @RabbitListener(queues = "error.promotion-service.queue")
    public void listenCouponReceiveError(UserCouponDTO uc){
        //普通领取要回滚redis缓存
        if (uc.getSerialNum()==null) {
            userCouponService.rollbackReceiveCoupon(uc.getCouponId() , uc.getUserId());
            log.error("优惠券领取消息重试全部失败，已补偿redis");
            return;
        }
        //兑换码领取应该回滚Bitmap
        log.error("兑换码领取消息重复全部失败，目前暂未实现Bitmap回滚");
    }
}
