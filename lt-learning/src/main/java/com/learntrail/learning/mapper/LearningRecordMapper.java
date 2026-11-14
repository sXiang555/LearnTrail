package com.learntrail.learning.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.learntrail.api.dto.IdAndNumDTO;
import com.learntrail.learning.domain.po.LearningRecord;

import java.time.LocalDateTime;
import java.util.List;

/**
 * <p>
 * 学习记录表 Mapper 接口
 * </p>
 *
 * @author 虎哥
 * @since 2022-12-10
 */
public interface LearningRecordMapper extends BaseMapper<LearningRecord> {

    List<IdAndNumDTO> countLearnedSections(Long userId, LocalDateTime begin, LocalDateTime end);
}
