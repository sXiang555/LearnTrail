package com.tianji.trade.service;

import com.tianji.api.dto.points.PointsTrade;
import com.tianji.common.utils.UserContext;
import com.tianji.trade.mapper.PointsOrderMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
@EnableScheduling
@RequiredArgsConstructor
@Slf4j
public class PointsOrderRecovery {
    private final PointsOrderMapper mapper;
    private final PointsOrderService service;

    @Scheduled(fixedDelay = 30000, initialDelay = 30000)
    public void recover() {
        for (PointsTrade.Order order : mapper.pending()) {
            try {
                UserContext.setUser(order.getUserId());
                if ("CANCELLING".equals(order.getPhase())) service.cancel(order.getOrderId());
                else service.pay(order.getOrderId());
            } catch (RuntimeException e) {
                log.debug("积分订单 {} 等待后续恢复", order.getOrderId());
            } finally {
                UserContext.removeUser();
                mapper.touch(order.getOrderId(), order.getPhase());
            }
        }
    }
}
