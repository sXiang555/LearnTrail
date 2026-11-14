package com.learntrail.learning.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.learntrail.learning.domain.po.PointsRecord;
import com.learntrail.learning.domain.vo.PointsStatisticsVO;
import com.learntrail.learning.enums.PointsRecordType;

import java.time.LocalDateTime;
import java.util.List;

/**
 * <p>
 * 学习积分记录，每个月底清零 服务类
 * </p>
 *
 * @author 虎哥
 */
public interface IPointsRecordService extends IService<PointsRecord> {

    void createPointsRecordTableBySeason(Integer season);

    void migratePointsRecordToHistory(Integer season, LocalDateTime beginTime, LocalDateTime endTime);

    void addPointsRecord(Long userId, int points, PointsRecordType type);

    List<PointsStatisticsVO> queryMyPointsToday();

}
