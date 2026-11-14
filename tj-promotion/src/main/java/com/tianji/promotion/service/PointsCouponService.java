package com.tianji.promotion.service;

import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.tianji.api.client.course.CourseClient;
import com.tianji.api.client.user.UserClient;
import com.tianji.api.client.trade.PointsOrderClient;
import com.tianji.api.client.learning.PointsSettlementClient;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.ForbiddenException;
import com.tianji.common.exceptions.UnauthorizedException;
import com.tianji.common.utils.UserContext;
import com.tianji.promotion.domain.po.Coupon;
import com.tianji.promotion.domain.po.CouponScope;
import com.tianji.promotion.domain.po.UserCoupon;
import com.tianji.promotion.enums.*;
import com.tianji.promotion.mapper.*;
import com.tianji.promotion.strategy.discount.Discount;
import com.tianji.promotion.strategy.discount.DiscountStrategy;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;
import java.util.*;

@Service
@RequiredArgsConstructor
public class PointsCouponService {
    private final PointsCouponMapper mapper;
    private final CouponMapper coupons;
    private final UserCouponMapper userCoupons;
    private final CouponScopeMapper scopes;
    private final PointsOrderClient orders;
    private final PointsSettlementClient wallet;
    private final CourseClient courses;
    private final UserClient users;

    public PointsTrade.Coupon template(Long id) {
        user();
        PointsTrade.Coupon value = mapper.template(id);
        if (value == null) throw new BadRequestException("积分优惠券不存在");
        return value;
    }
    public PageDTO<PointsTrade.Coupon> catalog(int page, int size) {
        user(); long total = mapper.countCatalog();
        return new PageDTO<>(total, (total + size - 1) / size, mapper.catalog((page - 1) * size, size));
    }
    public PageDTO<PointsTrade.Coupon> mine(Integer status, int page, int size) {
        Long user = user(); long total = mapper.countMine(user, status);
        return new PageDTO<>(total, (total + size - 1) / size, mapper.mine(user, status, (page - 1) * size, size));
    }

    @Transactional
    public Long create(PointsTrade.CouponEdit edit) {
        if (!Integer.valueOf(1).equals(users.queryUserType(user()))) throw new ForbiddenException("需要运营人员权限");
        DiscountType type = DiscountType.of(edit.getDiscountType());
        if (type == null || type == DiscountType.PER_PRICE_DISCOUNT
                || (type == DiscountType.RATE_DISCOUNT && edit.getDiscountValue() >= 100)) {
            throw new BadRequestException("请选择固定积分券、满积分券或 1–99% 折扣券");
        }
        List<Long> ids = edit.getCourseIds() == null ? List.of() : edit.getCourseIds();
        if (new HashSet<>(ids).size() != ids.size()) throw new BadRequestException("适用专栏不能重复");
        for (Long id : ids) if (courses.studioUnlockCost(id) == null) throw new BadRequestException("适用专栏不存在或不可兑换");
        LocalDateTime now = LocalDateTime.now();
        Coupon coupon = new Coupon().setId(IdWorker.getId()).setName(edit.getName().strip()).setType(1)
                .setDiscountType(type).setSpecific(!ids.isEmpty()).setDiscountValue(edit.getDiscountValue())
                .setThresholdAmount(type == DiscountType.NO_THRESHOLD ? 0 : edit.getThresholdPoints())
                .setMaxDiscountAmount(edit.getMaxDiscountPoints()).setObtainWay(ObtainType.PUBLIC)
                .setIssueBeginTime(now).setIssueEndTime(now.plusDays(365)).setTermDays(edit.getTermDays())
                .setStatus(CouponStatus.ISSUING).setTotalNum(edit.getTotalNum()).setIssueNum(0)
                .setUsedNum(0).setUserLimit(edit.getUserLimit());
        if (coupons.insert(coupon) != 1) throw new IllegalStateException("优惠券创建失败");
        mapper.enable(coupon.getId(), edit.getExchangePoints());
        for (Long id : ids) mapper.addScope(coupon.getId(), id);
        return coupon.getId();
    }

    @Transactional
    public Long receive(Long id) {
        return receive(id, "legacy");
    }

    @Transactional
    public Long receive(Long id, String requestKey) {
        Long user = user();
        Coupon coupon = lockTemplate(id);
        PointsTrade.Coupon view = template(id);
        if (view.getExchangePoints() != 0) throw new BadRequestException("请通过积分订单兑换此优惠券");
        String key = "free:" + user + ":" + id + ":" + requestKey;
        PointsTrade.Claim existing = mapper.claimByKey(key);
        if (existing != null) return existing.getUserCouponId();
        checkIssuable(coupon, user);
        UserCoupon issued = issue(coupon, user, false);
        mapper.insertClaim(claim(IdWorker.getId(), user, id, issued.getId(), key, "COMPLETED"));
        return issued.getId();
    }

    @Transactional
    public int quote(PointsTrade.Quote quote) {
        UserCoupon owned = owned(quote.getUserCouponId());
        Coupon coupon = lockTemplate(owned.getCouponId());
        owned = mapper.lockUserCoupon(owned.getId(), user());
        if (mapper.reservedBy(owned.getId()) != null) throw new BadRequestException("优惠券已被其他订单占用");
        return discount(owned, coupon, quote.getCourseId(), quote.getPoints());
    }

    @Transactional
    public void reserve(Long id) {
        PointsTrade.Order order = order(id);
        if ("PAID".equals(order.getPhase())) return;
        if (!"PAYING".equals(order.getPhase())) throw new BadRequestException("订单不在支付阶段");
        if ("COUPON".equals(order.getKind())) {
            Coupon coupon = lockTemplate(order.getTargetId());
            PointsTrade.Claim existing = mapper.claim(id, user());
            if (existing != null) {
                if ("CANCELLED".equals(existing.getStatus())) throw new BadRequestException("兑换已取消");
                return;
            }
            if (!Objects.equals(template(coupon.getId()).getExchangePoints(), order.getTotalPoints())
                    || order.getUserCouponId() != null || order.getDiscountPoints() != 0) throw new BadRequestException("兑换价格发生变化");
            checkIssuable(coupon, user());
            UserCoupon issued = issue(coupon, user(), true);
            mapper.insertClaim(claim(id, user(), coupon.getId(), issued.getId(), "order:" + id, "RESERVED"));
            return;
        }
        if (order.getUserCouponId() == null) return;
        UserCoupon owned = owned(order.getUserCouponId());
        Coupon coupon = lockTemplate(owned.getCouponId());
        owned = mapper.lockUserCoupon(owned.getId(), user());
        PointsTrade.Reservation existing = mapper.reservation(id, user());
        if (existing != null) {
            if ("RELEASED".equals(existing.getStatus())) throw new BadRequestException("订单优惠已释放");
            return;
        }
        Long reservedBy = mapper.reservedBy(owned.getId());
        if (reservedBy != null) throw new BadRequestException("优惠券已被其他订单占用");
        int discount = discount(owned, coupon, order.getTargetId(), order.getTotalPoints());
        if (discount != order.getDiscountPoints()) throw new BadRequestException("优惠规则发生变化，请取消订单重新选择");
        mapper.insertReservation(reservationFor(order, "RESERVED"));
    }

    public PointsTrade.Reservation reservation(Long id) {
        PointsTrade.Order order = order(id);
        if ("COUPON".equals(order.getKind())) {
            PointsTrade.Claim claim = mapper.claim(id, user());
            if (claim == null) return null;
            return reservationFor(order, "COMPLETED".equals(claim.getStatus()) ? "USED" : claim.getStatus());
        }
        PointsTrade.Reservation reservation = mapper.reservation(id, user());
        if (reservation != null && "RESERVED".equals(reservation.getStatus())) {
            UserCoupon owned = owned(order.getUserCouponId());
            LocalDateTime now = LocalDateTime.now();
            if (owned.getStatus() != UserCouponStatus.UNUSED || owned.getTermBeginTime().isAfter(now)
                    || owned.getTermEndTime().isBefore(now)) throw new BadRequestException("预留优惠券已失效，请取消订单");
        }
        return reservation;
    }

    @Transactional
    public void complete(Long id) {
        PointsTrade.Order order = order(id);
        requireReceipt(order, "CAPTURED");
        if ("COUPON".equals(order.getKind())) {
            Coupon template = lockTemplate(order.getTargetId());
            PointsTrade.Claim claim = mapper.claim(id, user());
            if (claim == null || "CANCELLED".equals(claim.getStatus())) throw new BadRequestException("优惠券发放凭证无效");
            if ("COMPLETED".equals(claim.getStatus())) return;
            UserCoupon issued = new UserCoupon().setId(claim.getUserCouponId()).setStatus(UserCouponStatus.UNUSED);
            if (template.getTermBeginTime() == null) {
                LocalDateTime now = LocalDateTime.now();
                issued.setTermBeginTime(now).setTermEndTime(now.plusDays(template.getTermDays()));
            }
            if (userCoupons.updateById(issued) != 1) throw new IllegalStateException("优惠券发放失败");
            mapper.claimStatus(id, "COMPLETED");
            return;
        }
        if (order.getUserCouponId() == null) return;
        UserCoupon owned = owned(order.getUserCouponId());
        lockTemplate(owned.getCouponId());
        PointsTrade.Reservation reservation = mapper.reservation(id, user());
        if (reservation == null || "RELEASED".equals(reservation.getStatus())) throw new BadRequestException("优惠券预留无效");
        if ("USED".equals(reservation.getStatus())) return;
        if (userCoupons.updateById(new UserCoupon().setId(owned.getId()).setStatus(UserCouponStatus.USED)
                .setUsedTime(LocalDateTime.now())) != 1) throw new IllegalStateException("优惠券核销失败");
        mapper.used(owned.getCouponId()); mapper.reservationStatus(id, "USED");
    }

    @Transactional
    public void release(Long id) {
        PointsTrade.Order order = order(id);
        requireReceipt(order, "CANCELLED");
        if ("COUPON".equals(order.getKind())) {
            lockTemplate(order.getTargetId());
            PointsTrade.Claim claim = mapper.claim(id, user());
            if (claim == null) {
                mapper.insertClaim(claim(id, user(), order.getTargetId(), 0L, "order:" + id, "CANCELLED"));
            } else if ("RESERVED".equals(claim.getStatus())) {
                mapper.restoreStock(claim.getCouponId()); mapper.claimStatus(id, "CANCELLED");
            } else if (!"CANCELLED".equals(claim.getStatus())) throw new BadRequestException("已发放的优惠券不能释放");
            return;
        }
        if (order.getUserCouponId() == null) return;
        UserCoupon owned = owned(order.getUserCouponId());
        lockTemplate(owned.getCouponId());
        PointsTrade.Reservation reservation = mapper.reservation(id, user());
        if (reservation == null) mapper.insertReservation(reservationFor(order, "RELEASED"));
        else if ("RESERVED".equals(reservation.getStatus())) mapper.reservationStatus(id, "RELEASED");
        else if (!"RELEASED".equals(reservation.getStatus())) throw new BadRequestException("已核销优惠券不能释放");
    }

    private int discount(UserCoupon owned, Coupon coupon, Long course, int points) {
        LocalDateTime now = LocalDateTime.now();
        if (owned == null || owned.getStatus() != UserCouponStatus.UNUSED
                || owned.getTermBeginTime().isAfter(now) || owned.getTermEndTime().isBefore(now)) {
            throw new BadRequestException("优惠券已使用、尚未生效或已过期");
        }
        // 兑换库存尚未支付的用户券状态为 EXPIRED，不能提前使用。
        if (Boolean.TRUE.equals(coupon.getSpecific())) {
            List<CouponScope> rules = scopes.selectList(Wrappers.<CouponScope>lambdaQuery().eq(CouponScope::getCouponId, coupon.getId()));
            List<Long> categories = List.of();
            if (rules.stream().anyMatch(scope -> scope.getType() == 1)) {
                com.tianji.api.dto.course.CourseFullInfoDTO info = courses.getCourseInfoById(course, false, false);
                if (info == null) throw new BadRequestException("适用课程信息暂不可用");
                categories = Arrays.asList(info.getFirstCateId(), info.getSecondCateId(), info.getThirdCateId());
            }
            final List<Long> applicableCategories = categories;
            if (rules.stream().noneMatch(scope -> (scope.getType() == 2 && course.equals(scope.getBizId()))
                    || (scope.getType() == 1 && applicableCategories.contains(scope.getBizId())))) throw new BadRequestException("优惠券不适用于此专栏");
        }
        Discount strategy = DiscountStrategy.getDiscount(coupon.getDiscountType());
        if (strategy == null || points < 1 || (coupon.getDiscountType() != DiscountType.NO_THRESHOLD && !strategy.canUse(points, coupon))) {
            throw new BadRequestException("未满足优惠券使用门槛");
        }
        return Math.min(points, Math.max(0, strategy.calculateDiscount(points, coupon)));
    }
    private Coupon lockTemplate(Long id) {
        Coupon coupon = mapper.lockTemplate(id);
        if (coupon == null || mapper.template(id) == null) throw new BadRequestException("积分优惠券不存在");
        return coupon;
    }
    private void checkIssuable(Coupon coupon, Long user) {
        LocalDateTime now = LocalDateTime.now();
        if (coupon.getStatus() != CouponStatus.ISSUING || coupon.getIssueBeginTime().isAfter(now) || coupon.getIssueEndTime().isBefore(now)
                || coupon.getIssueNum() >= coupon.getTotalNum()) throw new BadRequestException("优惠券已结束、暂停或库存不足");
        if (mapper.received(coupon.getId(), user) >= coupon.getUserLimit()) throw new BadRequestException("已达到每人领取上限");
    }
    private UserCoupon issue(Coupon coupon, Long user, boolean pending) {
        if (coupons.incrIssueNum(coupon.getId()) != 1) throw new BadRequestException("优惠券库存不足");
        LocalDateTime start = coupon.getTermBeginTime() == null ? LocalDateTime.now() : coupon.getTermBeginTime();
        LocalDateTime end = coupon.getTermBeginTime() == null ? start.plusDays(coupon.getTermDays()) : coupon.getTermEndTime();
        if (!end.isAfter(LocalDateTime.now())) throw new BadRequestException("优惠券有效期已结束");
        UserCoupon issued = new UserCoupon().setId(IdWorker.getId()).setCouponId(coupon.getId()).setUserId(user)
                .setTermBeginTime(start).setTermEndTime(end).setStatus(pending ? UserCouponStatus.EXPIRED : UserCouponStatus.UNUSED);
        if (userCoupons.insert(issued) != 1) throw new IllegalStateException("优惠券发放失败");
        return issued;
    }
    private UserCoupon owned(Long id) {
        UserCoupon coupon = id == null ? null : userCoupons.selectById(id);
        if (coupon == null || !user().equals(coupon.getUserId())) throw new BadRequestException("优惠券不属于当前用户");
        return coupon;
    }
    private PointsTrade.Order order(Long id) {
        PointsTrade.Order order = orders.snapshot(id);
        if (order == null || !user().equals(order.getUserId())) throw new BadRequestException("订单不存在");
        return order;
    }
    private void requireReceipt(PointsTrade.Order order, String status) {
        PointsTrade.Receipt receipt = wallet.receipt(order.getOrderId());
        if (receipt == null || !status.equals(receipt.getStatus()) || !Objects.equals(receipt.getUserId(), order.getUserId())
                || ("CAPTURED".equals(status) && !Objects.equals(receipt.getAmount(), order.getPayablePoints()))) {
            throw new BadRequestException("积分结算凭证不匹配");
        }
    }
    private PointsTrade.Reservation reservationFor(PointsTrade.Order order, String status) {
        PointsTrade.Reservation reservation = new PointsTrade.Reservation();
        reservation.setOrderId(order.getOrderId()); reservation.setUserId(order.getUserId());
        reservation.setUserCouponId(order.getUserCouponId()); reservation.setDiscountPoints(order.getDiscountPoints());
        reservation.setStatus(status); return reservation;
    }
    private PointsTrade.Claim claim(Long id, Long user, Long coupon, Long userCoupon, String key, String status) {
        PointsTrade.Claim claim = new PointsTrade.Claim();
        claim.setOrderId(id); claim.setUserId(user); claim.setCouponId(coupon); claim.setUserCouponId(userCoupon);
        claim.setClaimKey(key); claim.setStatus(status); return claim;
    }
    private Long user() {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录");
        return user;
    }
}
