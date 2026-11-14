package com.tianji.learning.service;

import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.tianji.api.client.course.CourseClient;
import com.tianji.api.client.trade.PointsOrderClient;
import com.tianji.api.client.promotion.PointsCouponClient;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.UnauthorizedException;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.mapper.PointsSettlementMapper;
import com.tianji.learning.mapper.StudioPointsMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.Objects;

@Service
@RequiredArgsConstructor
public class PointsSettlementService {
    private final PointsSettlementMapper mapper;
    private final StudioPointsMapper points;
    private final StudioPointsService wallet;
    private final PointsOrderClient orders;
    private final PointsCouponClient coupons;
    private final CourseClient courses;

    public PointsTrade.Receipt receipt(Long id) { return mapper.receipt(id, user()); }

    @Transactional
    public PointsTrade.Receipt pay(Long id) {
        Long user = user();
        PointsTrade.Order order = order(id, user);
        PointsTrade.Receipt recorded = mapper.receipt(id, user);
        if (recorded != null) {
            if (!"CAPTURED".equals(recorded.getStatus())) throw new BadRequestException("订单已取消，不能扣分");
            return recorded;
        }
        // 远程核对在钱包锁之前；事务内再次读取唯一凭证防并发和取消竞争。
        PointsTrade.Reservation reservation = null;
        if ("PAYING".equals(order.getPhase()) && (order.getUserCouponId() != null || "COUPON".equals(order.getKind()))) {
            reservation = coupons.reservation(id);
        }
        int balance = wallet.lockWallet(user);
        PointsTrade.Receipt existing = mapper.receipt(id, user);
        if (existing != null) {
            if (!"CAPTURED".equals(existing.getStatus())) throw new BadRequestException("订单已取消，不能扣分");
            return existing;
        }
        if (!"PAYING".equals(order.getPhase()) || order.getPayablePoints() == null || order.getPayablePoints() < 0
                || order.getPayablePoints() > 100000 || order.getTotalPoints() - order.getDiscountPoints() != order.getPayablePoints()) {
            throw new BadRequestException("订单当前不可结算");
        }
        if (order.getUserCouponId() != null || "COUPON".equals(order.getKind())) {
            if (reservation == null || !Objects.equals(reservation.getUserId(), user)
                    || !Objects.equals(reservation.getDiscountPoints(), order.getDiscountPoints())
                    || !Objects.equals(reservation.getUserCouponId(), order.getUserCouponId())
                    || !"RESERVED".equals(reservation.getStatus())) throw new BadRequestException("优惠券尚未正确预留");
        }
        if ("COURSE".equals(order.getKind())) {
            if (!Objects.equals(courses.studioUnlockCost(order.getTargetId()), order.getTotalPoints())) {
                throw new BadRequestException("专栏已不可兑换，请取消订单后重新选择");
            }
            if (points.validLesson(user, order.getTargetId()) != null || points.hasEntry(user, "UNLOCK", order.getTargetId()) != 0) {
                throw new BadRequestException("该专栏已经解锁，请取消此订单，不会重复扣分");
            }
        } else if (!"COUPON".equals(order.getKind())) throw new BadRequestException("兑换类型无效");
        if (balance < order.getPayablePoints()) throw new BadRequestException("积分不足，可通过观看、阅读、签到或有效评论获取积分");
        if (order.getPayablePoints() > 0) wallet.changeBalance(user, -order.getPayablePoints());
        if ("COURSE".equals(order.getKind())) {
            points.addEntry(user, "UNLOCK", order.getTargetId(), order.getTitle(), -order.getPayablePoints());
            points.grantLesson(IdWorker.getId(), user, order.getTargetId());
            if (points.validLesson(user, order.getTargetId()) == null) throw new IllegalStateException("学习权益创建失败");
        } else points.addEntry(user, "COUPON", id, order.getTitle(), -order.getPayablePoints());
        PointsTrade.Receipt receipt = receiptFor(order, "CAPTURED", order.getPayablePoints());
        mapper.insert(receipt);
        return receipt;
    }

    @Transactional
    public PointsTrade.Receipt cancel(Long id) {
        Long user = user();
        PointsTrade.Order order = order(id, user);
        if (!"CANCELLING".equals(order.getPhase()) && !"CANCELLED".equals(order.getPhase())) {
            throw new BadRequestException("订单尚未进入取消流程");
        }
        wallet.lockWallet(user);
        PointsTrade.Receipt existing = mapper.receipt(id, user);
        if (existing != null) return existing;
        PointsTrade.Receipt cancelled = receiptFor(order, "CANCELLED", 0);
        mapper.insert(cancelled);
        return cancelled;
    }

    private PointsTrade.Order order(Long id, Long user) {
        PointsTrade.Order order = orders.snapshot(id);
        if (order == null || !user.equals(order.getUserId()) || !id.equals(order.getOrderId())) {
            throw new BadRequestException("订单不存在");
        }
        return order;
    }
    private PointsTrade.Receipt receiptFor(PointsTrade.Order order, String status, int amount) {
        PointsTrade.Receipt receipt = new PointsTrade.Receipt();
        receipt.setOrderId(order.getOrderId()); receipt.setUserId(order.getUserId());
        receipt.setKind(order.getKind()); receipt.setTargetId(order.getTargetId());
        receipt.setStatus(status); receipt.setAmount(amount); return receipt;
    }
    private Long user() {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录");
        return user;
    }
}
