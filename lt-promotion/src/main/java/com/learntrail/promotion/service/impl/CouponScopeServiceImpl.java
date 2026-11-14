package com.learntrail.promotion.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.learntrail.promotion.domain.po.CouponScope;
import com.learntrail.promotion.mapper.CouponScopeMapper;
import com.learntrail.promotion.service.ICouponScopeService;
import org.springframework.stereotype.Service;

/**
 * <p>
 * 优惠券作用范围信息 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Service
public class CouponScopeServiceImpl extends ServiceImpl<CouponScopeMapper, CouponScope> implements ICouponScopeService {
}
