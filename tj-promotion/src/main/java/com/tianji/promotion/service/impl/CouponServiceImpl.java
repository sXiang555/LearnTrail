package com.tianji.promotion.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.BizIllegalException;
import com.tianji.common.utils.BeanUtils;
import com.tianji.common.utils.CollUtils;
import com.tianji.common.utils.DateUtils;
import com.tianji.common.utils.StringUtils;
import com.tianji.common.utils.UserContext;
import com.tianji.promotion.constants.PromotionConstants;
import com.tianji.promotion.domain.dto.CouponFormDTO;
import com.tianji.promotion.domain.dto.CouponIssueFormDTO;
import com.tianji.promotion.domain.po.Coupon;
import com.tianji.promotion.domain.po.CouponScope;
import com.tianji.promotion.domain.po.UserCoupon;
import com.tianji.promotion.domain.query.CouponQuery;
import com.tianji.promotion.domain.vo.CouponDetailVO;
import com.tianji.promotion.domain.vo.CouponPageVO;
import com.tianji.promotion.domain.vo.CouponVO;
import com.tianji.promotion.enums.CouponStatus;
import com.tianji.promotion.enums.DiscountType;
import com.tianji.promotion.enums.ObtainType;
import com.tianji.promotion.enums.UserCouponStatus;
import com.tianji.promotion.mapper.CouponMapper;
import com.tianji.promotion.mapper.PointsCouponMapper;
import com.tianji.api.client.user.UserClient;
import com.tianji.common.exceptions.ForbiddenException;
import com.tianji.promotion.service.ICouponScopeService;
import com.tianji.promotion.service.ICouponService;
import com.tianji.promotion.service.IExchangeCodeService;
import com.tianji.promotion.service.IUserCouponService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * <p>
 * 优惠券的规则信息 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class CouponServiceImpl extends ServiceImpl<CouponMapper, Coupon> implements ICouponService {

    private final ICouponScopeService scopeService;
    private final IExchangeCodeService exchangeCodeService;
    private final IUserCouponService userCouponService;
    private final StringRedisTemplate redisTemplate;
    private final PointsCouponMapper pointsCoupons;
    private final UserClient users;

    private void protectPoints(Long id) {
        if (pointsCoupons.template(id) != null && (UserContext.getUser() == null
                || !Integer.valueOf(1).equals(users.queryUserType(UserContext.getUser())))) {
            throw new ForbiddenException("需要运营人员权限");
        }
    }

    @Override
    @Transactional
    public void saveCoupon(CouponFormDTO dto) {
        // 1.保存优惠券
        // 1.1.转PO
        Coupon coupon = BeanUtils.copyBean(dto, Coupon.class);
        // 1.2.设置初始状态为待发放
        coupon.setStatus(CouponStatus.DRAFT);
        // 1.3.保存
        boolean saved = save(coupon);
        if (!saved) {
            log.error("新增优惠券保存失败，coupon: {}", coupon);
            throw new BadRequestException("新增优惠券保存失败");
        }

        if (!dto.getSpecific()) {
            // 没有范围限定
            log.debug("优惠券没有限定使用范围，couponId: {}", coupon.getId());
            return;
        }
        Long couponId = coupon.getId();
        // 2.保存限定范围
        List<Long> scopes = dto.getScopes();
        if (CollUtils.isEmpty(scopes)) {
            log.error("优惠券已限定使用范围，但限定范围列表为空，couponId: {}", couponId);
            throw new BadRequestException("限定范围不能为空");
        }
        // 2.1.转换PO
        List<CouponScope> list = scopes.stream()
                .map(bizId -> new CouponScope().setBizId(bizId).setCouponId(couponId))
                .collect(Collectors.toList());
        // 2.2.保存
        scopeService.saveBatch(list);
        log.debug("新增优惠券限定范围成功，couponId: {}, scopes: {}", couponId, scopes);
    }

    @Override
    @Transactional
    public void updateCoupon(Long id, CouponFormDTO dto) {
        protectPoints(id);
        // 1.查询优惠券
        Coupon coupon = getById(id);
        if (coupon == null) {
            throw new BadRequestException("优惠券不存在");
        }

        // 2.校验状态：只有待发放状态的优惠券才能修改
        if (coupon.getStatus() != CouponStatus.DRAFT) {
            throw new BizIllegalException("只能修改待发放状态的优惠券");
        }

        // 3.拷贝属性到PO
        Coupon couponUpdate = BeanUtils.copyBean(dto, Coupon.class);
        couponUpdate.setId(id);

        // 4.更新数据库
        boolean updated = updateById(couponUpdate);
        if (!updated) {
            throw new BadRequestException("修改优惠券失败");
        }

        // 5.更新限定范围
        // 5.1.删除旧的限定范围
        scopeService.lambdaUpdate().eq(CouponScope::getCouponId, id).remove();
        // 5.2.保存新的限定范围
        if (dto.getSpecific() && CollUtils.isNotEmpty(dto.getScopes())) {
            List<CouponScope> scopeList = dto.getScopes().stream()
                    .map(bizId -> new CouponScope().setBizId(bizId).setCouponId(id))
                    .collect(Collectors.toList());
            scopeService.saveBatch(scopeList);
        }
        log.debug("修改优惠券成功，couponId: {}", id);
    }

    @Override
    public PageDTO<CouponPageVO> queryCouponByPage(CouponQuery query) {
        // 1.分页查询
        Page<Coupon> page = lambdaQuery()
                .eq(query.getType() != null, Coupon::getDiscountType, DiscountType.of(query.getType()))
                .eq(query.getStatus() != null, Coupon::getStatus, CouponStatus.of(query.getStatus()))
                .like(StringUtils.isNotBlank(query.getName()), Coupon::getName, query.getName())
                .page(query.toMpPageDefaultSortByCreateTimeDesc());
        // 2.处理VO
        List<Coupon> records = page.getRecords();
        if (CollUtils.isEmpty(records)) {
            return PageDTO.empty(page);
        }
        // 3.返回
        return PageDTO.of(page, CouponPageVO.class);
    }

    @Transactional
    @Override
    public void beginIssue(CouponIssueFormDTO dto) {
        // 1.查询优惠券
        Long couponId = dto.getId();
        protectPoints(couponId);
        Coupon coupon = getById(couponId);
        if (coupon == null) {
            throw new BadRequestException("优惠券不存在");
        }

        // 2.判断优惠券状态，必须是待发放或暂停状态才能进行发放
        CouponStatus status = coupon.getStatus();
        if (status != CouponStatus.DRAFT && status != CouponStatus.PAUSE) {
            throw new BizIllegalException("优惠券状态不允许进行发放操作");
        }

        // 3.判断是否是立刻发放
        LocalDateTime issueBeginTime = dto.getIssueBeginTime();
        LocalDateTime now = LocalDateTime.now();
        boolean isImmediate = issueBeginTime == null || !issueBeginTime.isAfter(now);

        // 4.更新优惠券
        // 4.1.拷贝属性到PO
        Coupon couponUpdate = BeanUtils.copyBean(dto, Coupon.class);
        couponUpdate.setId(couponId);
        // 4.2.更新状态
        if (isImmediate) {
            couponUpdate.setStatus(CouponStatus.ISSUING);
            couponUpdate.setIssueBeginTime(now);
        } else {
            couponUpdate.setStatus(CouponStatus.UN_ISSUE);
        }
        // 4.3.写入数据库
        boolean updated = updateById(couponUpdate);
        if (!updated) {
            throw new BadRequestException("发放优惠券失败");
        }

        // 5.添加缓存（只有立即发放时才添加缓存）
        if (isImmediate) {
            coupon.setIssueBeginTime(couponUpdate.getIssueBeginTime());
            coupon.setIssueEndTime(couponUpdate.getIssueEndTime());
            cacheCouponInfo(coupon);
        }

        // 6.判断是否需要生成兑换码，优惠券类型必须是兑换码，优惠券状态必须是待发放
        if (coupon.getObtainWay() == ObtainType.ISSUE && status == CouponStatus.DRAFT) {
            // 异步生成兑换码
            coupon.setIssueEndTime(couponUpdate.getIssueEndTime());
            exchangeCodeService.asyncGenerateCode(coupon);
        }
    }

    private void cacheCouponInfo(Coupon coupon) {
        // 1.组织数据
        Map<String, String> map = new HashMap<>(4);
        //优惠券剩余数量
        Integer totalNum = coupon.getTotalNum();
        Integer issueNum = coupon.getIssueNum() ==null ? 0 : coupon.getIssueNum();
        Integer remainNum = Math.max(totalNum - issueNum, 0);
        map.put("totalNum", String.valueOf(remainNum));
        map.put("issueEndTime", String.valueOf(DateUtils.toEpochMilli(coupon.getIssueEndTime())));
        map.put("userLimit", String.valueOf(coupon.getUserLimit()));
        map.put("issueBeginTime", String.valueOf(DateUtils.toEpochMilli(coupon.getIssueBeginTime())));
        // 2.写缓存
        String key = PromotionConstants.COUPON_CACHE_KEY_PREFIX + coupon.getId();
        redisTemplate.opsForHash().putAll(key, map);
    }

    @Override
    public List<CouponVO> queryIssuingCoupons() {
        // 1.查询发放中的优惠券列表
        LocalDateTime now = LocalDateTime.now();
        List<Coupon> coupons = lambdaQuery()
                .eq(Coupon::getStatus, CouponStatus.ISSUING)
                .le(Coupon::getIssueBeginTime, now)
                .ge(Coupon::getIssueEndTime, now)
                .list();
        if (CollUtils.isEmpty(coupons)) {
            return CollUtils.emptyList();
        }

        // 2.统计当前用户已经领取的优惠券的信息
        Long userId = UserContext.getUser();
        List<Long> couponIds = coupons.stream().map(Coupon::getId).collect(Collectors.toList());

        // 当前用户对每个优惠券的已领取数量
        Map<Long, Long> userClaimMap = new HashMap<>();
        // 当前用户对每个优惠券的未使用数量
        Map<Long, Long> userUnusedMap = new HashMap<>();
        if (userId != null) {
            // 2.1.查询当前用户已经领取的优惠券数据
            List<UserCoupon> userCoupons = userCouponService.lambdaQuery()
                    .eq(UserCoupon::getUserId, userId)
                    .in(UserCoupon::getCouponId, couponIds)
                    .list();
            if (CollUtils.isNotEmpty(userCoupons)) {
                // 2.2.统计已领取数量
                userClaimMap = userCoupons.stream()
                        .collect(Collectors.groupingBy(UserCoupon::getCouponId, Collectors.counting()));
                // 2.3.统计未使用数量
                userUnusedMap = userCoupons.stream()
                        .filter(uc -> uc.getStatus() == UserCouponStatus.UNUSED)
                        .collect(Collectors.groupingBy(UserCoupon::getCouponId, Collectors.counting()));
            }
        }

        // 3.封装VO结果
        Map<Long, Long> finalUserClaimMap = userClaimMap;
        Map<Long, Long> finalUserUnusedMap = userUnusedMap;
        return coupons.stream().map(coupon -> {
            CouponVO vo = BeanUtils.copyBean(coupon, CouponVO.class);
            // 3.1.是否可以领取：已发行数量 < 总数量 && 当前用户已领取数量 < 每人限领数量
            long userClaimed = finalUserClaimMap.getOrDefault(coupon.getId(), 0L);
            boolean available = coupon.getIssueNum() < coupon.getTotalNum()
                    && userClaimed < coupon.getUserLimit();
            vo.setAvailable(available);
            // 3.2.是否可以使用：当前用户有未使用的该优惠券
            long userUnused = finalUserUnusedMap.getOrDefault(coupon.getId(), 0L);
            vo.setReceived(userUnused > 0);
            return vo;
        }).collect(Collectors.toList());
    }

    @Override
    @Transactional
    public void pauseIssue(Long id) {
        protectPoints(id);
        // 1.查询优惠券
        Coupon coupon = getById(id);
        if (coupon == null) {
            throw new BadRequestException("优惠券不存在");
        }

        // 2.当前券状态必须是进行中
        if (coupon.getStatus() != CouponStatus.ISSUING) {
            throw new BizIllegalException("只有发放中的优惠券可以暂停");
        }

        // 3.更新状态为暂停
        boolean updated = lambdaUpdate()
                .eq(Coupon::getId, id)
                .set(Coupon::getStatus, CouponStatus.PAUSE)
                .update();
        if (!updated) {
            throw new BadRequestException("暂停优惠券失败");
        }

        // 4.删除缓存
        String key = PromotionConstants.COUPON_CACHE_KEY_PREFIX + id;
        redisTemplate.delete(key);
    }

    @Override
    @Transactional
    public void deleteById(Long id) {
        protectPoints(id);
        // 1.查询
        Coupon coupon = getById(id);
        if (coupon == null) {
            throw new BadRequestException("优惠券不存在");
        }
        // 只能删除待发放状态的优惠券
        if (coupon.getStatus() != CouponStatus.DRAFT) {
            throw new BizIllegalException("只能删除待发放状态的优惠券");
        }
        // 2.删除优惠券
        removeById(id);
        // 3.删除优惠券对应限定范围
        scopeService.lambdaUpdate().eq(CouponScope::getCouponId, id).remove();
    }

    @Override
    public CouponDetailVO queryCouponById(Long id) {
        // 1.查询优惠券
        // 2.转换VO
        // 3.查询限定范围
        return null;
    }

    @Override
    public void beginIssueBatch(List<Coupon> coupons) {
        // 1.更新券状态为发放中
        LocalDateTime now = LocalDateTime.now();
        for (Coupon coupon : coupons) {
            coupon.setStatus(CouponStatus.ISSUING);
            coupon.setIssueBeginTime(now);
        }
        updateBatchById(coupons);
        // 2.批量缓存
        for (Coupon coupon : coupons) {
            cacheCouponInfo(coupon);
        }
    }

    @Override
    public void endIssueBatch(List<Coupon> coupons) {
        // 1.更新券状态为发放结束
        for (Coupon coupon : coupons) {
            coupon.setStatus(CouponStatus.FINISHED);
        }
        updateBatchById(coupons);
        // 2.删除缓存
        String[] keys = coupons.stream()
                .map(c -> PromotionConstants.COUPON_CACHE_KEY_PREFIX + c.getId())
                .toArray(String[]::new);
        redisTemplate.delete(Arrays.asList(keys));
    }
}
