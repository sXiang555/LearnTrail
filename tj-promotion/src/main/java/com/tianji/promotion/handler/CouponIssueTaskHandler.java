package com.tianji.promotion.handler;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.tianji.api.client.user.UserClient;
import com.tianji.api.dto.sms.SmsInfoDTO;
import com.tianji.api.dto.user.UserDTO;
import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import com.tianji.common.constants.MqConstants;
import com.tianji.common.utils.CollUtils;
import com.tianji.promotion.domain.po.Coupon;
import com.tianji.promotion.domain.po.UserCoupon;
import com.tianji.promotion.enums.CouponStatus;
import com.tianji.promotion.enums.UserCouponStatus;
import com.tianji.promotion.service.ICouponService;
import com.tianji.promotion.service.IUserCouponService;
import com.xxl.job.core.context.XxlJobHelper;
import com.xxl.job.core.handler.annotation.XxlJob;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;

@Slf4j
@Component
@RequiredArgsConstructor
public class CouponIssueTaskHandler {

    private final ICouponService couponService;
    private final IUserCouponService userCouponService;
    private final UserClient userClient;
    private final RabbitMqHelper mqHelper;

    @XxlJob("couponIssueJobHandler")
    public void handleCouponIssueJob() {
        // 1.获取分片信息
        int shardIndex = XxlJobHelper.getShardIndex();
        int shardTotal = XxlJobHelper.getShardTotal();

        int batchSize = 10;
        long lastId = 0L;

        // 本轮任务只处理启动时已经到发放时间的数据
        LocalDateTime now = LocalDateTime.now();

        while (true) {
            // 2.查询当前分片中ID大于lastId的一批数据
            List<Coupon> records = couponService.lambdaQuery()
                    .eq(Coupon::getStatus, CouponStatus.UN_ISSUE)
                    .le(Coupon::getIssueBeginTime, now)
                    .gt(Coupon::getId, lastId)

                    // 固定分片归属
                    .apply(
                            "MOD(id, {0}) = {1}",
                            shardTotal,
                            shardIndex
                    )

                    // ID游标必须有稳定排序
                    .orderByAsc(Coupon::getId)

                    // batchSize是代码中的固定整数，可以安全拼接
                    .last("LIMIT " + batchSize)
                    .list();

            if (CollUtils.isEmpty(records)) {
                break;
            }

            log.debug(
                    "定时发放优惠券，分片[{}]处理{}条，lastId={}",
                    shardIndex,
                    records.size(),
                    lastId
            );

            // 3.发放本批优惠券
            couponService.beginIssueBatch(records);

            // 4.处理成功后移动游标
            lastId = records.get(records.size() - 1).getId();
        }
    }

    @XxlJob("couponEndJobHandler")
    public void handleCouponEndJob(){
        // 1.获取分片信息
        int shardIndex = XxlJobHelper.getShardIndex();
        int shardTotal = XxlJobHelper.getShardTotal();

        int batchSize = 10;
        long lastId = 0L;

        // 本轮任务只处理启动时已经到结束的数据
        LocalDateTime now = LocalDateTime.now();

        while (true) {
            // 2.查询当前分片中ID大于lastId的一批数据
            List<Coupon> records = couponService.lambdaQuery()
                    .eq(Coupon::getStatus, CouponStatus.ISSUING)
                    .le(Coupon::getIssueEndTime, now)
                    .gt(Coupon::getId, lastId)

                    // 固定分片归属
                    .apply(
                            "MOD(id, {0}) = {1}",
                            shardTotal,
                            shardIndex
                    )

                    // ID游标必须有稳定排序
                    .orderByAsc(Coupon::getId)

                    // batchSize是代码中的固定整数，可以安全拼接
                    .last("LIMIT " + batchSize)
                    .list();

            if (CollUtils.isEmpty(records)) {
                break;
            }

            log.debug(
                    "定时结束发放优惠券，分片[{}]处理{}条，lastId={}",
                    shardIndex,
                    records.size(),
                    lastId
            );

            // 3.发放本批优惠券
            couponService.endIssueBatch(records);

            // 4.处理成功后移动游标
            lastId = records.get(records.size() - 1).getId();
        }
    }

    @XxlJob("couponExpireRemindJobHandler")
    public void handleCouponExpireRemindJob() {
        // 1.获取分片信息
        int shardIndex = XxlJobHelper.getShardIndex();
        int shardTotal = XxlJobHelper.getShardTotal();
        int pageSize = 10;
        int pageNo = shardIndex + 1;
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime end = now.plusDays(1);

        Set<Long> userIdSet = new HashSet<>();

        // 2.循环处理当前分片负责的页面，查询1天内过期的未使用用户券

        while (true) {
            Page<UserCoupon> page = userCouponService.lambdaQuery()
                    .eq(UserCoupon::getStatus, UserCouponStatus.UNUSED)
                    .ge(UserCoupon::getTermEndTime, now)
                    .lt(UserCoupon::getTermEndTime, end)
                    .page(new Page<>(pageNo, pageSize));

            List<UserCoupon> records = page.getRecords();
            if (CollUtils.isEmpty(records)) {
                break;
            }

            log.debug(
                    "优惠券过期提醒，分片[{}] 处理第{}页，共{}条数据",
                    shardIndex,
                    pageNo,
                    records.size()
            );

            records.forEach(uc -> userIdSet.add(uc.getUserId()));
            pageNo += shardTotal;
        }
        if (CollUtils.isEmpty(userIdSet)) {
            log.debug("优惠券过期提醒，分片[{}] 没有需要提醒的用户", shardIndex);
            return;
        }

        // 3.批量查询用户手机号
        List<UserDTO> users = userClient.queryUserByIds(userIdSet);
        if (CollUtils.isEmpty(users)) {
            return;
        }
        List<String> phones = users.stream()
                .map(UserDTO::getCellPhone)
                .filter(Objects::nonNull)
                .collect(Collectors.toList());
        if (CollUtils.isEmpty(phones)) {
            return;
        }

        // 4.发送短信提醒
        SmsInfoDTO smsInfo = new SmsInfoDTO();
        smsInfo.setTemplateCode("coupon_expire_remind");
        smsInfo.setPhones(phones);
        Map<String, String> params = new HashMap<>(1);
        params.put("date", now.toLocalDate().toString());
        smsInfo.setTemplateParams(params);

        mqHelper.send(MqConstants.Exchange.SMS_EXCHANGE, MqConstants.Key.SMS_MESSAGE, smsInfo);
        log.debug("优惠券过期提醒，分片[{}] 向{}个用户发送过期提醒短信", shardIndex, phones.size());
    }
}
