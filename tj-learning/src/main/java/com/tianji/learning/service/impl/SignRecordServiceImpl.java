package com.tianji.learning.service.impl;

import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import com.tianji.common.constants.MqConstants;
import com.tianji.common.exceptions.BizIllegalException;
import com.tianji.common.utils.BooleanUtils;
import com.tianji.common.utils.CollUtils;
import com.tianji.common.utils.DateUtils;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.constants.RedisConstants;
import com.tianji.learning.domain.po.SignRecord;
import com.tianji.learning.domain.vo.SignResultVO;
import com.tianji.learning.mapper.SignRecordMapper;
import com.tianji.learning.mq.message.SignInMessage;
import com.tianji.learning.service.ISignRecordService;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.connection.BitFieldSubCommands;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.sql.ResultSet;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * <p>
 * 签到记录表 服务实现类
 * </p>
 *
 * @author sxiang
 */
@Service
@RequiredArgsConstructor
public class SignRecordServiceImpl extends ServiceImpl<SignRecordMapper, SignRecord> implements ISignRecordService {

    private final RabbitMqHelper mqHelper;

    private final StringRedisTemplate stringRedisTemplate;
    @Override
    public SignResultVO addSignRecords() {
        // 获取当前用户
        Long userId = UserContext.getUser();
        //获取日期
        LocalDateTime now = LocalDateTime.now();
        //拼接key
        String key = RedisConstants.SIGN_RECORD_KEY_PREFIX
                +userId
                +now.format(DateUtils.SIGN_DATE_SUFFIX_FORMATTER);
        //计算偏移量
        int offset = now.getDayOfMonth() - 1;
        //保存签到信息
        Boolean exists = stringRedisTemplate.opsForValue().setBit(key,offset,true);
        if (BooleanUtils.isTrue(exists)){
            throw new BizIllegalException("不允许重复签到");
        }
        //计算连续签到天数
        int signDays = countSignDays(key, now.getDayOfMonth());
        //计算连续签到得分
        int rewardPoints = 0;
        switch (signDays){
            case 7:
                rewardPoints = 10;
                break;
            case 14:
                rewardPoints = 20;
                break;
            case 28:
                rewardPoints = 40;
                break;
        }

        SignInMessage signInMessage = SignInMessage.of(userId, rewardPoints+1);//签到有固定积分1
        mqHelper.send(
                MqConstants.Exchange.LEARNING_EXCHANGE,
                MqConstants.Key.SIGN_IN,
                signInMessage
        );

        //封装返回
        SignResultVO signResultVO = new SignResultVO();
        signResultVO.setSignDays(signDays);
        signResultVO.setRewardPoints(rewardPoints);
        return signResultVO;
    }

    @Override
    public List<Integer> querySignRecords() {
        Long userId = UserContext.getUser();
        //查询到今天的签到记录
        LocalDateTime now = LocalDateTime.now();
        int dayOfMonth = now.getDayOfMonth();
        String key = RedisConstants.SIGN_RECORD_KEY_PREFIX
                + userId
                + now.format(DateUtils.SIGN_DATE_SUFFIX_FORMATTER);
        List<Long> result = stringRedisTemplate.opsForValue().bitField(key,
                BitFieldSubCommands.create().get(BitFieldSubCommands.BitFieldType.unsigned(dayOfMonth)).valueAt(0));
        if (CollUtils.isEmpty(result)){
            return List.of();
        }
        int num = result.get(0).intValue();
        //将num转化成二进制字符串
        List<Integer> list = new ArrayList<>(dayOfMonth);
        for (int i = 0; i < dayOfMonth; i++) {
            list.add(num&1);
            num >>>= 1;
        }
        Collections.reverse(list);
        return list;
    }


    private int countSignDays(String key, int dayOfMonth) {
        //获取本月第一天起到今天为止的签到信息
        List<Long> result = stringRedisTemplate.opsForValue().bitField(key,
                BitFieldSubCommands.create().get(BitFieldSubCommands.BitFieldType.unsigned(dayOfMonth)).valueAt(0));
        if (CollUtils.isEmpty(result)){
            return 0;
        }
        //计算连续签到天数
        int num = result.get(0).intValue();
        //定义一个计数器
        int count = 0;
        //从右向左遍历,让num与1进行与操作,判断num的最低位是否为1,如果为1,则count加1,否则返回count
        while((num & 1) == 1){
            count++;
            num >>>= 1;
        }
        return count;
    }
}
