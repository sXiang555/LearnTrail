package com.learntrail.promotion.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.common.utils.CollUtils;
import com.learntrail.common.utils.DateUtils;
import com.learntrail.promotion.constants.PromotionConstants;
import com.learntrail.promotion.domain.po.Coupon;
import com.learntrail.promotion.domain.po.ExchangeCode;
import com.learntrail.promotion.domain.query.CodeQuery;
import com.learntrail.promotion.domain.vo.ExchangeCodeVO;
import com.learntrail.promotion.enums.ExchangeCodeStatus;
import com.learntrail.promotion.mapper.ExchangeCodeMapper;
import com.learntrail.promotion.service.IExchangeCodeService;
import com.learntrail.promotion.utils.CodeUtil;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * <p>
 * 兑换码 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ExchangeCodeServiceImpl extends ServiceImpl<ExchangeCodeMapper, ExchangeCode> implements IExchangeCodeService {

    private final StringRedisTemplate redisTemplate;

    @Override
    @Async("generateExchangeCodeExecutor")
    public void asyncGenerateCode(Coupon coupon) {
        // 发放数量
        Integer totalNum = coupon.getTotalNum();
        if (totalNum == null || totalNum <= 0) {
            log.warn("优惠券 {} 发放数量无效，跳过兑换码生成", coupon.getId());
            return;
        }
        Long couponId = coupon.getId();
        // 新鲜值：取优惠券id的最后4位
        long fresh = couponId & 0xF;

        // 1.获取Redis自增序列号（一次性获取totalNum个，即INCRBY获取起始值）
        Long serialNum = redisTemplate.opsForValue().increment(
                PromotionConstants.COUPON_CODE_SERIAL_KEY, totalNum);
        if (serialNum == null) {
            log.error("从Redis获取序列号失败，优惠券id: {}", couponId);
            return;
        }
        long startSerial = serialNum - totalNum + 1;

        // 2.批量生成兑换码并保存数据库
        List<ExchangeCode> list = new ArrayList<>(totalNum);
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime expiredTime = coupon.getIssueEndTime();

        for (long i = 0; i < totalNum; i++) {
            long curSerial = startSerial + i;
            // 2.生成兑换码
            String code = CodeUtil.generateCode(curSerial, fresh);

            ExchangeCode ec = new ExchangeCode();
            ec.setId((int) curSerial);
            ec.setCode(code);
            ec.setStatus(ExchangeCodeStatus.UNUSED);
            ec.setType(1); // 优惠券类型
            ec.setExchangeTargetId(couponId);
            ec.setExpiredTime(expiredTime);
            ec.setCreateTime(now);
            ec.setUpdateTime(now);
            list.add(ec);
        }

        // 3.批量保存数据库
        boolean saved = saveBatch(list);
        if (!saved) {
            log.error("兑换码批量保存失败，优惠券id: {}", couponId);
            return;
        }
        log.info("兑换码生成成功，优惠券id: {}, 数量: {}, 序列号范围: {}-{}",
                couponId, totalNum, startSerial, serialNum);

        // 4.写入Redis缓存，member：couponId，score：兑换码的最大序列号
        redisTemplate.opsForZSet().add(
                PromotionConstants.COUPON_CODE_MAP_KEY, couponId.toString(), serialNum);
    }

    @Override
    public boolean updateExchangeMark(long serialNum, boolean mark) {
        Boolean result = redisTemplate.opsForValue().setBit(
                PromotionConstants.COUPON_CODE_USED_KEY, serialNum, mark);
        return result != null && result;
    }

    @Override
    public PageDTO<ExchangeCodeVO> queryCodePage(CodeQuery query) {
        // 1.分页查询兑换码
        Page<ExchangeCode> page = lambdaQuery()
                .eq(ExchangeCode::getExchangeTargetId, query.getCouponId())
                .eq(ExchangeCode::getStatus, ExchangeCodeStatus.of(query.getStatus()))
                .page(query.toMpPageDefaultSortByCreateTimeDesc());
        // 2.转换VO
        List<ExchangeCode> records = page.getRecords();
        if (CollUtils.isEmpty(records)) {
            return PageDTO.empty(page);
        }
        List<ExchangeCodeVO> voList = records.stream()
                .map(ec -> new ExchangeCodeVO(ec.getId(), ec.getCode()))
                .collect(Collectors.toList());
        // 3.返回
        return PageDTO.of(page, voList);
    }

    @Override
    public Long exchangeTargetId(long serialNum) {
        // 1.查询score值比当前序列号大的第一个优惠券
        // 2.数据转换
        return null;
    }
}
