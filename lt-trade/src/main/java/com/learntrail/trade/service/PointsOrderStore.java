package com.learntrail.trade.service;

import com.learntrail.api.dto.points.PointsTrade;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.trade.domain.po.Order;
import com.learntrail.trade.domain.po.OrderDetail;
import com.learntrail.trade.mapper.PointsOrderMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;

@Service
@RequiredArgsConstructor
public class PointsOrderStore {
    private final PointsOrderMapper mapper;
    private final IOrderService orders;

    @Transactional
    public PointsTrade.Order create(PointsTrade.Order value) {
        if (mapper.insert(value) == 0) {
            PointsTrade.Order existing = mapper.request(value.getUserId(), value.getRequestKey());
            if (existing == null || !existing.getKind().equals(value.getKind())
                    || !existing.getTargetId().equals(value.getTargetId())
                    || !Objects.equals(existing.getUserCouponId(), value.getUserCouponId())) {
                throw new BadRequestException("同一请求标识不能用于不同订单");
            }
            return existing;
        }
        Order order = new Order().setId(value.getOrderId()).setUserId(value.getUserId()).setStatus(1)
                .setTotalAmount(0).setRealAmount(0).setDiscountAmount(0).setPayChannel("POINTS")
                .setMessage("等待积分支付");
        if (value.getUserCouponId() != null) order.setCouponIds(List.of(value.getUserCouponId()));
        OrderDetail detail = new OrderDetail().setOrderId(value.getOrderId()).setUserId(value.getUserId())
                .setCourseId("COURSE".equals(value.getKind()) ? value.getTargetId() : 0L)
                .setName(value.getTitle()).setPrice(0).setRealPayAmount(0).setDiscountAmount(0)
                .setCoverUrl(value.getCoverUrl() == null ? "" : value.getCoverUrl())
                .setStatus(1).setPayChannel("POINTS").setValidDuration(0);
        orders.saveOrderAndDetails(order, List.of(detail));
        return mapper.find(value.getOrderId(), value.getUserId());
    }

    @Transactional
    public PointsTrade.Order begin(Long id, Long user, boolean cancel) {
        PointsTrade.Order order = required(mapper.lock(id, user));
        String phase = order.getPhase();
        if (cancel) {
            if ("PAID".equals(phase)) throw new BadRequestException("已支付订单不能取消");
            if (!"CANCELLED".equals(phase) && !"CANCELLING".equals(phase)) mapper.phase(id, "CANCELLING");
        } else {
            if ("CANCELLED".equals(phase) || "CANCELLING".equals(phase)) throw new BadRequestException("订单已取消或正在取消");
            if ("CREATED".equals(phase)) {
                if (order.getCreatedAt().plusMinutes(30).isBefore(LocalDateTime.now())) {
                    throw new BadRequestException("订单超过 30 分钟，请取消后重新下单");
                }
                mapper.phase(id, "PAYING");
            }
        }
        return mapper.find(id, user);
    }

    @Transactional
    public void finish(Long id, Long user, boolean paid) {
        PointsTrade.Order order = required(mapper.lock(id, user));
        if (paid) {
            if ("PAID".equals(order.getPhase())) return;
            if ("CANCELLED".equals(order.getPhase())) throw new IllegalStateException("已取消订单存在支付凭证");
            mapper.phase(id, "PAID");
            mapper.orderStatus(id, 2, "积分支付成功"); mapper.detailStatus(id, 2);
        } else {
            if (!"CANCELLING".equals(order.getPhase()) && !"CANCELLED".equals(order.getPhase())) {
                throw new BadRequestException("订单不在取消流程中");
            }
            mapper.phase(id, "CANCELLED");
            mapper.orderStatus(id, 3, "用户取消积分订单"); mapper.detailStatus(id, 3);
        }
    }
    private PointsTrade.Order required(PointsTrade.Order value) {
        if (value == null) throw new BadRequestException("订单不存在");
        return value;
    }
}
