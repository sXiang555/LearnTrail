package com.tianji.trade.service;

import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.tianji.api.client.course.CourseClient;
import com.tianji.api.client.learning.PointsSettlementClient;
import com.tianji.api.client.learning.LearningClient;
import com.tianji.api.client.promotion.PointsCouponClient;
import com.tianji.api.dto.course.CourseFullInfoDTO;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.UnauthorizedException;
import com.tianji.common.utils.UserContext;
import com.tianji.trade.mapper.PointsOrderMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import java.time.LocalDateTime;
import java.util.Objects;

@Service
@RequiredArgsConstructor
public class PointsOrderService {
    private final PointsOrderMapper mapper;
    private final PointsOrderStore store;
    private final CourseClient courses;
    private final LearningClient learning;
    private final PointsSettlementClient wallet;
    private final PointsCouponClient coupons;

    public PointsTrade.Order create(PointsTrade.Create form) {
        Long user = user();
        PointsTrade.Order previous = mapper.request(user, form.getRequestKey());
        if (previous != null) {
            if (!Objects.equals(previous.getKind(), form.getKind()) || !Objects.equals(previous.getTargetId(), form.getTargetId())
                    || !Objects.equals(previous.getUserCouponId(), form.getUserCouponId())) {
                throw new BadRequestException("同一请求标识不能用于不同订单");
            }
            return previous;
        }
        PointsTrade.Order order = new PointsTrade.Order();
        order.setOrderId(IdWorker.getId()); order.setUserId(user); order.setRequestKey(form.getRequestKey());
        order.setKind(form.getKind()); order.setTargetId(form.getTargetId()); order.setUserCouponId(form.getUserCouponId());
        int total;
        if ("COURSE".equals(form.getKind())) {
            Integer cost = courses.studioUnlockCost(form.getTargetId());
            if (cost == null || cost < 1 || cost > 100000) throw new BadRequestException("专栏不可积分兑换");
            if (learning.isLessonValid(form.getTargetId()) != null) throw new BadRequestException("该专栏已经解锁");
            CourseFullInfoDTO course = courses.getCourseInfoById(form.getTargetId(), false, false);
            if (course == null) throw new BadRequestException("专栏暂不可用");
            total = cost; order.setTitle(course.getName()); order.setCoverUrl(course.getCoverUrl());
        } else if ("COUPON".equals(form.getKind())) {
            if (form.getUserCouponId() != null) throw new BadRequestException("优惠券兑换不能叠加使用优惠券");
            PointsTrade.Coupon coupon = coupons.template(form.getTargetId());
            LocalDateTime now = LocalDateTime.now();
            if (coupon == null || coupon.getExchangePoints() == null || coupon.getExchangePoints() < 1
                    || !Integer.valueOf(3).equals(coupon.getStatus()) || coupon.getIssueBeginTime().isAfter(now)
                    || coupon.getIssueEndTime().isBefore(now)) throw new BadRequestException("优惠券暂不可兑换");
            total = coupon.getExchangePoints(); order.setTitle(coupon.getName());
        } else throw new BadRequestException("兑换类型无效");
        int discount = 0;
        if (form.getUserCouponId() != null) {
            PointsTrade.Quote quote = new PointsTrade.Quote();
            quote.setUserCouponId(form.getUserCouponId()); quote.setCourseId(form.getTargetId()); quote.setPoints(total);
            Integer computed = coupons.quote(quote);
            if (computed == null || computed < 0 || computed > total) throw new BadRequestException("优惠计算失败");
            discount = computed;
        }
        order.setTotalPoints(total); order.setDiscountPoints(discount); order.setPayablePoints(total - discount);
        return store.create(order);
    }

    public PointsTrade.Order snapshot(Long id) {
        PointsTrade.Order order = mapper.find(id, user());
        if (order == null) throw new BadRequestException("订单不存在");
        return order;
    }
    public PageDTO<PointsTrade.Order> page(String phase, int page, int size) {
        Long user = user(); long total = mapper.count(user, phase);
        return new PageDTO<>(total, (total + size - 1) / size, mapper.page(user, phase, (page - 1) * size, size));
    }

    public PointsTrade.Order pay(Long id) {
        PointsTrade.Order order = store.begin(id, user(), false);
        if ("PAID".equals(order.getPhase())) return order;
        if (needsCoupon(order)) coupons.reserve(id);
        PointsTrade.Receipt receipt = wallet.pay(id);
        if (receipt == null || !"CAPTURED".equals(receipt.getStatus())
                || !Objects.equals(receipt.getAmount(), order.getPayablePoints())) throw new BadRequestException("积分结算结果待核对，请重试");
        if (needsCoupon(order)) coupons.complete(id);
        store.finish(id, user(), true);
        return snapshot(id);
    }

    public PointsTrade.Order cancel(Long id) {
        PointsTrade.Order order = store.begin(id, user(), true);
        if ("CANCELLED".equals(order.getPhase())) return order;
        PointsTrade.Receipt receipt = wallet.cancel(id);
        if (receipt == null) throw new BadRequestException("取消结果待核对，请重试");
        boolean paid = "CAPTURED".equals(receipt.getStatus());
        if (needsCoupon(order)) {
            if (paid) coupons.complete(id); else coupons.release(id);
        }
        store.finish(id, user(), paid);
        return snapshot(id);
    }

    private boolean needsCoupon(PointsTrade.Order order) {
        return order.getUserCouponId() != null || "COUPON".equals(order.getKind());
    }
    private Long user() {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录");
        return user;
    }
}
