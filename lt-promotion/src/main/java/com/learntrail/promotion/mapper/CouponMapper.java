package com.learntrail.promotion.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.learntrail.promotion.domain.po.Coupon;

/**
 * <p>
 * 优惠券的规则信息 Mapper 接口
 * </p>
 *
 * @author 虎哥
 */
public interface CouponMapper extends BaseMapper<Coupon> {

    int incrIssueNum(Long id);
}
