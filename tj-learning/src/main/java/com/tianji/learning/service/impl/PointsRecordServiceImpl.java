package com.tianji.learning.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.tianji.common.utils.CollUtils;
import com.tianji.common.utils.DateUtils;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.constants.RedisConstants;
import com.tianji.learning.domain.po.PointsRecord;
import com.tianji.learning.domain.vo.PointsStatisticsVO;
import com.tianji.learning.enums.PointsRecordType;
import com.tianji.learning.mapper.PointsRecordMapper;
import com.tianji.learning.service.IPointsRecordService;
import com.tianji.learning.utils.TableInfoContext;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.stream.Collectors;

import static com.tianji.learning.constants.LearningConstants.POINTS_RECORD_TABLE_PREFIX;

/**
 * <p>
 * 学习积分记录，每个月底清零 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PointsRecordServiceImpl extends ServiceImpl<PointsRecordMapper, PointsRecord> implements IPointsRecordService {

    private final StringRedisTemplate redisTemplate;
    private final StringRedisTemplate stringRedisTemplate;

    @Override
    public void createPointsRecordTableBySeason(Integer season) {
        baseMapper.createPointsRecordTableBySeason(season);
    }

    @Override
    @Transactional
    public void migratePointsRecordToHistory(Integer season, LocalDateTime beginTime, LocalDateTime endTime) {
        // 1.创建历史积分记录表（DDL，使用${season}直接拼接表名）
        createPointsRecordTableBySeason(season);
        String tableName = POINTS_RECORD_TABLE_PREFIX + season;

        // 2.分页迁移数据
        int pageNo = 1;
        int pageSize = 1000;
        while (true) {
            // 2.1.从 points_record 查询源数据（无 TableInfoContext，使用默认表名）
            Page<PointsRecord> page = lambdaQuery()
                    .ge(PointsRecord::getCreateTime, beginTime)
                    .lt(PointsRecord::getCreateTime, endTime)
                    .page(new Page<>(pageNo, pageSize));
            List<PointsRecord> records = page.getRecords();
            if (CollUtils.isEmpty(records)) {
                break;
            }

            // 2.2.切换到历史表，批量写入（拦截器将 points_record 替换为 points_record_xx）
            TableInfoContext.setInfo(tableName);
            saveBatch(records);
            TableInfoContext.remove();

            pageNo++;
        }

        // 3.删除已迁移的源数据（无 TableInfoContext，操作默认的 points_record 表）
        lambdaUpdate()
                .ge(PointsRecord::getCreateTime, beginTime)
                .lt(PointsRecord::getCreateTime, endTime)
                .remove();

        log.info("积分记录迁移完成，赛季：{}，时间范围：{} ~ {}，表名：{}", season, beginTime, endTime, tableName);
    }

    @Override
    public void addPointsRecord(Long userId, int points, PointsRecordType type) {
        // 1.判断当前方式有没有积分上限
        int maxPoints = type.getMaxPoints();
        int realPoints = points;
        LocalDateTime now = LocalDateTime.now();
            // 2.有，则需要判断是否超过上限
        if (maxPoints > 0) {
            // 2.1.查询今日已得积分
            LocalDateTime begin = DateUtils.getDayStartTime(now);
            LocalDateTime end = DateUtils.getDayEndTime(now);
            int todayPoints = queryUserPointsByTypeAndDate(userId, type, begin, end);
            // 2.2.判断是否超过上限
            if (todayPoints > maxPoints) {
                // 2.3.超过，直接结束
                return;
            }
                // 2.4.没超过，保存积分记录
            if (todayPoints + points > maxPoints){
                realPoints = maxPoints-todayPoints;
            }
        }
        // 3.没有，直接保存积分记录
        PointsRecord pointsRecord = new PointsRecord();
        pointsRecord.setUserId(userId)
                .setPoints(realPoints)
                .setType(type);
        save(pointsRecord);
        // 4.更新总积分到Redis
        String key = RedisConstants.POINTS_BOARD_KEY_PREFIX + now.format(DateUtils.POINTS_BOARD_SUFFIX_FORMATTER);
        stringRedisTemplate.opsForZSet().incrementScore(key, userId.toString(), realPoints);
    }

    @Override
    public List<PointsStatisticsVO> queryMyPointsToday() {
        // 1.获取用户
        Long userId = UserContext.getUser();
        // 2.获取日期
        LocalDateTime begin = DateUtils.getDayStartTime(LocalDateTime.now());
        LocalDateTime end = DateUtils.getDayEndTime(LocalDateTime.now());
        // 3.构建查询条件
        QueryWrapper<PointsRecord> queryWrapper = new QueryWrapper<PointsRecord>();
        queryWrapper.lambda()
                .eq(PointsRecord::getUserId, userId)
                .ge(PointsRecord::getCreateTime, begin)
                .le(PointsRecord::getCreateTime, end);
        // 4.查询
        List<PointsRecord> records = getBaseMapper().queryMyPointsToday(queryWrapper);
        // 5.封装返回
        if (CollUtils.isEmpty(records)){
            return CollUtils.emptyList();
        }
        return records.stream().map(record -> {
            PointsStatisticsVO vo = new PointsStatisticsVO();
            vo.setType(record.getType().getDesc());
            vo.setPoints(record.getPoints());
            vo.setMaxPoints(record.getType().getMaxPoints());
            return vo;
        }).collect(Collectors.toList());
    }

    private int queryUserPointsByTypeAndDate(
            Long userId, PointsRecordType type, LocalDateTime begin, LocalDateTime end) {
        // 1.查询条件
        QueryWrapper<PointsRecord> queryWrapper = new QueryWrapper<PointsRecord>();
        queryWrapper.lambda()
                .eq(PointsRecord::getUserId, userId)
                .eq(type != null, PointsRecord::getType, type)
                .ge(begin != null, PointsRecord::getCreateTime, begin)
                .le(end != null, PointsRecord::getCreateTime, end);
        // 2.调用mapper，查询结果
        Integer points = getBaseMapper().queryUserPointsByTypeAndDate(queryWrapper);
        // 3.判断并返回
        return points != null ? points : 0;
    }
}
