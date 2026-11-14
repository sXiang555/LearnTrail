package com.tianji.promotion.service.impl;

import cn.hutool.core.date.LocalDateTimeUtil;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.BizIllegalException;
import com.tianji.common.exceptions.DbException;
import com.tianji.common.utils.CollUtils;
import com.tianji.common.utils.UserContext;
import com.tianji.promotion.constants.PromotionConstants;
import com.tianji.promotion.domain.dto.UserCouponDTO;
import com.tianji.promotion.domain.po.Coupon;
import com.tianji.promotion.domain.po.ExchangeCode;
import com.tianji.promotion.domain.po.UserCoupon;
import com.tianji.promotion.domain.query.UserCouponQuery;
import com.tianji.promotion.domain.vo.CouponVO;
import com.tianji.promotion.enums.DiscountType;
import com.tianji.promotion.enums.ExchangeCodeStatus;
import com.tianji.promotion.enums.UserCouponStatus;
import com.tianji.promotion.mapper.CouponMapper;
import com.tianji.promotion.mapper.UserCouponMapper;
import com.tianji.promotion.mapper.PointsCouponMapper;
import com.tianji.promotion.service.PointsCouponService;
import com.tianji.promotion.service.IDiscountService;
import com.tianji.promotion.service.IExchangeCodeService;
import com.tianji.promotion.service.IUserCouponService;
import com.tianji.promotion.strategy.discount.Discount;
import com.tianji.promotion.strategy.discount.DiscountStrategy;
import com.tianji.promotion.utils.CodeUtil;
import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import com.tianji.common.constants.MqConstants;
import lombok.RequiredArgsConstructor;
import org.springframework.aop.framework.AopContext;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * <p>
 * 用户领取优惠券的记录，是真正使用的优惠券信息 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Service
@RequiredArgsConstructor
public class UserCouponServiceImpl extends ServiceImpl<UserCouponMapper, UserCoupon> implements IUserCouponService {

    private final CouponMapper couponMapper;

    private final IExchangeCodeService codeService;

    private final StringRedisTemplate redisTemplate;

    private final RabbitMqHelper mqHelper;
    private final PointsCouponMapper pointsCoupons;
    private final PointsCouponService pointsCouponService;

    private static final RedisScript<Long> RECEIVE_COUPON_SCRIPT;
    private static final RedisScript<Long> ROLLBACK_COUPON_SCRIPT;


    static {
        RECEIVE_COUPON_SCRIPT = RedisScript.of(
                new ClassPathResource("lua/receive_coupon.lua"),
                Long.class
        );
    }

    static {
        ROLLBACK_COUPON_SCRIPT = RedisScript.of(
                new ClassPathResource("lua/rollback_receive_coupon.lua"),
                Long.class
        );
    }

    @Override
    public void receiveCoupon(Long couponId) {
        if (pointsCoupons.template(couponId) != null) {
            pointsCouponService.receive(couponId);
            return;
        }
        Long userId = UserContext.getUser();
        Long result = redisTemplate.execute(
                RECEIVE_COUPON_SCRIPT,
                List.of(PromotionConstants.COUPON_CACHE_KEY_PREFIX + couponId,
                        PromotionConstants.USER_COUPON_CACHE_KEY_PREFIX + couponId
                        ),
                userId.toString()
        );
        if(result !=0){
            int index = result.intValue()-1;
            if(index<0||index>=PromotionConstants.RECEIVE_COUPON_ERROR_MSG.length){
                throw new BizIllegalException("领取优惠券失败");
            }
            throw new BadRequestException(PromotionConstants.RECEIVE_COUPON_ERROR_MSG[index]);
        }
        // 6.发送MQ消息，异步保存数据库
        UserCouponDTO uc = new UserCouponDTO();
        uc.setUserId(userId);
        uc.setCouponId(couponId);
        try {
            mqHelper.send(MqConstants.Exchange.PROMOTION_EXCHANGE, MqConstants.Key.COUPON_RECEIVE, uc);
        } catch (Exception e) {
            rollbackReceiveCoupon(couponId, userId);
            throw new BadRequestException(e.getMessage());
        }
    }

    private Coupon queryCouponByCache(Long couponId) {
        // 1.准备KEY
        String key = PromotionConstants.COUPON_CACHE_KEY_PREFIX + couponId;
        // 2.查询
        Map<Object, Object> entries = redisTemplate.opsForHash().entries(key);
        if (CollUtils.isEmpty(entries)) {
            return null;
        }
        // 3.数据反序列化
        Coupon coupon = new Coupon();
        coupon.setId(couponId);
        Object totalNum = entries.get("totalNum");
        if (totalNum != null) {
            coupon.setTotalNum(Integer.valueOf(totalNum.toString()));
        }
        Object issueBeginTime = entries.get("issueBeginTime");
        if (issueBeginTime != null) {
            coupon.setIssueBeginTime(LocalDateTimeUtil.of(Long.parseLong(issueBeginTime.toString())));
        }
        Object issueEndTime = entries.get("issueEndTime");
        if (issueEndTime != null) {
            coupon.setIssueEndTime(LocalDateTimeUtil.of(Long.parseLong(issueEndTime.toString())));
        }
        Object userLimit = entries.get("userLimit");
        if (userLimit != null) {
            coupon.setUserLimit(Integer.valueOf(userLimit.toString()));
        }
        return coupon;
    }

    @Override
    @Transactional
    public void checkAndCreateUserCoupon(UserCouponDTO uc) {
        // 1.查询优惠券
        Coupon coupon = couponMapper.selectById(uc.getCouponId());
        if (coupon == null) {
            throw new BadRequestException("优惠券不存在");
        }
        // 2.调用原有方法
        checkAndCreateUserCoupon(coupon, uc.getUserId(), uc.getSerialNum());
    }


    public void checkAndCreateUserCoupon(Coupon coupon, Long userId, Integer serialNum) {
        // 1.校验每人限领数量
        // 1.1.统计当前用户对当前优惠券的已经领取的数量
        Integer count = lambdaQuery()
                .eq(UserCoupon::getUserId, userId)
                .eq(UserCoupon::getCouponId, coupon.getId())
                .count();
        // 1.2.校验限领数量
        if (count != null && count >= coupon.getUserLimit()) {
            throw new BadRequestException("超出领取数量");
        }
        // 2.更新优惠券的已经发放的数量 + 1
        int r = couponMapper.incrIssueNum(coupon.getId());
        if (r == 0) {
            throw new BizIllegalException("优惠券库存不足");
        }
        // 3.新增一个用户券
        saveUserCoupon(coupon, userId);
        // 4.更新兑换码状态
        if (serialNum != null) {
            boolean update = codeService.lambdaUpdate()
                    .set(ExchangeCode::getUserId, userId)
                    .set(ExchangeCode::getStatus, ExchangeCodeStatus.USED)
                    .eq(ExchangeCode::getId, serialNum)
                    .update();
            if (!update) {
                throw new BizIllegalException("兑换码状态更新失败");
            }
        }
    }

    @Override
    public void exchangeCoupon(String code) {
        // 1.校验并解析兑换码
        long serialNum = CodeUtil.parseCode(code);
        // 2.校验是否已经兑换 SETBIT KEY 4 1 ，这里直接执行setbit，通过返回值来判断是否兑换过
        boolean exchanged = codeService.updateExchangeMark(serialNum, true);
        if (exchanged) {
            throw new BizIllegalException("兑换码已经被兑换过了");
        }
        // 3.查询兑换码对应的优惠券id
        ExchangeCode exchangeCode = codeService.getById(serialNum);
        if (exchangeCode == null) {
            throw new BizIllegalException("兑换码不存在！");
        }
        // 4.是否过期
        LocalDateTime now = LocalDateTime.now();
        if (now.isAfter(exchangeCode.getExpiredTime())) {
            throw new BizIllegalException("兑换码已经过期");
        }
        // 5.校验并生成用户券
        // 5.1.查询优惠券
        Coupon coupon = couponMapper.selectById(exchangeCode.getExchangeTargetId());
        // 5.2.查询用户
        Long userId = UserContext.getUser();
        // 5.3.校验并生成用户券，更新兑换码状态（通过代理调用，checkAndCreateUserCoupon上的@MyLock分布式锁会拦截）
        try {
            UserCouponDTO uc = new UserCouponDTO();
            uc.setUserId(userId);
            uc.setCouponId(exchangeCode.getExchangeTargetId());
            uc.setSerialNum((int) serialNum);
            mqHelper.sendAsync(MqConstants.Exchange.PROMOTION_EXCHANGE, MqConstants.Key.COUPON_RECEIVE, uc);
        } catch (Exception e) {
            // 重置兑换的标记 0
            codeService.updateExchangeMark(serialNum, false);
            throw e;
        }
    }

    @Override
    public PageDTO<CouponVO> queryMyCouponPage(UserCouponQuery query) {
        // 1.获取当前用户
        Long userId = UserContext.getUser();
        // 2.分页查询用户券
        Page<UserCoupon> page = query.toMpPageDefaultSortByCreateTimeDesc();
        UserCouponStatus status = UserCouponStatus.of(query.getStatus());
        page = lambdaQuery()
                .eq(UserCoupon::getUserId, userId)
                .eq(status != null, UserCoupon::getStatus, status)
                .page(page);
        List<UserCoupon> records = page.getRecords();
        if (CollUtils.isEmpty(records)) {
            return PageDTO.empty(page);
        }
        // 3.获取优惠券详细信息
        Set<Long> couponIds = records.stream()
                .map(UserCoupon::getCouponId)
                .collect(Collectors.toSet());
        List<Coupon> coupons = couponMapper.selectBatchIds(couponIds);
        Map<Long, Coupon> couponMap = coupons.stream()
                .collect(Collectors.toMap(Coupon::getId, c -> c));
        // 4.封装VO
        List<CouponVO> voList = records.stream().map(uc -> {
            Coupon c = couponMap.get(uc.getCouponId());
            if (c == null) {
                return null;
            }
            CouponVO vo = new CouponVO();
            vo.setId(uc.getId());
            vo.setName(c.getName());
            vo.setSpecific(c.getSpecific());
            vo.setDiscountType(c.getDiscountType());
            vo.setThresholdAmount(c.getThresholdAmount());
            vo.setDiscountValue(c.getDiscountValue());
            vo.setMaxDiscountAmount(c.getMaxDiscountAmount());
            vo.setTermDays(c.getTermDays());
            vo.setTermEndTime(uc.getTermEndTime());
            return vo;
        }).collect(Collectors.toList());
        return PageDTO.of(page, voList);
    }

    @Override
    @Transactional
    public void writeOffCoupon(List<Long> userCouponIds) {
        // 1.查询优惠券
        // 2.处理数据


        // 4.核销，修改优惠券状态
        // 5.更新已使用数量
    }

    @Override
    @Transactional
    public void refundCoupon(List<Long> userCouponIds) {
        // 1.查询优惠券
        // 2.处理优惠券数据
                    // 3.判断有效期，是否已经过期，如果过期，则状态为 已过期，否则状态为 未使用

        // 4.修改优惠券状态
        // 5.更新已使用数量
    }

    @Override
    public List<String> queryDiscountRules(List<Long> userCouponIds) {
        // 1.查询优惠券信息
        if(CollUtils.isEmpty(userCouponIds)){
            return CollUtils.emptyList();
        }
        Long userId = UserContext.getUser();
        List<UserCoupon> userCoupons = lambdaQuery()
                .eq(UserCoupon::getUserId, userId)
                .eq(UserCoupon::getStatus, UserCouponStatus.USED)
                .in(UserCoupon::getId, userCouponIds)
                .list();
        Map<Long, UserCoupon> userCouponMap = userCoupons.stream()
                .collect(Collectors.toMap(UserCoupon::getId, uc -> uc));
        if(CollUtils.isEmpty(userCoupons)){
            return CollUtils.emptyList();
        }
        List<Long> couponIds = userCoupons.stream().map(UserCoupon::getCouponId).collect(Collectors.toList());
        //底层是in，返回的是无序的
        List<Coupon> coupons = couponMapper.selectBatchIds(couponIds);
        Map<Long, Coupon> couponMap = coupons.stream()
                .collect(Collectors.toMap(Coupon::getId, c -> c));
        // 2.转换规则
        List<String> res = new ArrayList<>(coupons.size());
        for (Long userCouponId : userCouponIds) {
            UserCoupon uc = userCouponMap.get(userCouponId);
            if(uc == null){
                continue;
            }
            Coupon c = couponMap.get(uc.getCouponId());
            if(c == null){
                continue;
            }
            Discount discount = DiscountStrategy.getDiscount(c.getDiscountType());
            String rule = discount.getRule(c);
            res.add(rule);
        }
        return res;
    }

    @Override
    public void rollbackReceiveCoupon(Long couponId , Long userId) {
        Long res = redisTemplate.execute(
                ROLLBACK_COUPON_SCRIPT,
                List.of(PromotionConstants.COUPON_CACHE_KEY_PREFIX + couponId,
                        PromotionConstants.USER_COUPON_CACHE_KEY_PREFIX + couponId
                ),
                userId.toString()
        );
        if(res !=0){
            throw new BizIllegalException("回滚优惠券领取失败，请联系管理员");
        }
    }

    private void saveUserCoupon(Coupon coupon, Long userId) {
        // 1.基本信息
        UserCoupon uc = new UserCoupon();
        uc.setUserId(userId);
        uc.setCouponId(coupon.getId());
        // 2.有效期信息
        LocalDateTime termBeginTime = coupon.getTermBeginTime();
        LocalDateTime termEndTime = coupon.getTermEndTime();
        if (termBeginTime == null) {
            termBeginTime = LocalDateTime.now();
            termEndTime = termBeginTime.plusDays(coupon.getTermDays());
        }
        uc.setTermBeginTime(termBeginTime);
        uc.setTermEndTime(termEndTime);
        // 3.保存
        if(!save(uc))
            throw new DbException("保存用户优惠券失败");
    }
}
