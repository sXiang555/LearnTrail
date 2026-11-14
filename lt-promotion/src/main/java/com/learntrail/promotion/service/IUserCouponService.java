package com.learntrail.promotion.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.promotion.domain.dto.UserCouponDTO;
import com.learntrail.promotion.domain.po.Coupon;
import com.learntrail.promotion.domain.po.UserCoupon;
import com.learntrail.promotion.domain.query.UserCouponQuery;
import com.learntrail.promotion.domain.vo.CouponVO;

import java.util.List;

/**
 * <p>
 * 用户领取优惠券的记录，是真正使用的优惠券信息 服务类
 * </p>
 *
 * @author 虎哥
 */
public interface IUserCouponService extends IService<UserCoupon> {
    void receiveCoupon(Long couponId);

    void checkAndCreateUserCoupon(UserCouponDTO uc);

    void checkAndCreateUserCoupon(Coupon coupon, Long userId, Integer serialNum);

    void exchangeCoupon(String code);

    PageDTO<CouponVO> queryMyCouponPage(UserCouponQuery query);

    void writeOffCoupon(List<Long> userCouponIds);

    void refundCoupon(List<Long> userCouponIds);

    List<String> queryDiscountRules(List<Long> userCouponIds);

    void rollbackReceiveCoupon(Long couponId, Long userId);
}
