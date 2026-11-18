package com.learntrail.trade.service;

import com.learntrail.api.dto.points.PointsTrade;
import com.learntrail.common.utils.UserContext;
import com.learntrail.trade.mapper.PointsOrderMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.UUID;

@Component
@EnableScheduling
@RequiredArgsConstructor
@Slf4j
public class PointsOrderRecovery {
    private final PointsOrderMapper mapper;
    private final PointsOrderService service;

    @Scheduled(fixedDelay = 10000, initialDelay = 30000)
    public void recover() {
        for (PointsTrade.Order order : mapper.pending()) {
            Long id = order.getOrderId();
            String phase = order.getPhase();
            String token = UUID.randomUUID().toString().replace("-", "");
            if (mapper.claimRecovery(id, phase, token) == 0) continue;
            try {
                UserContext.setUser(order.getUserId());
                service.resume(order);
            } catch (RuntimeException e) {
                String error = e.getClass().getSimpleName();
                mapper.retryRecovery(id, phase, token, error.substring(0, Math.min(64, error.length())));
                log.warn("积分订单 {} 阶段 {} 恢复失败，类型 {}", id, phase, error);
            } finally {
                UserContext.removeUser();
            }
        }
    }
}
