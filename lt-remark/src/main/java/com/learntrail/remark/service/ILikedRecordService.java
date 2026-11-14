package com.learntrail.remark.service;

import com.learntrail.remark.domain.dto.LikeRecordFormDTO;
import com.learntrail.remark.domain.po.LikedRecord;
import com.baomidou.mybatisplus.extension.service.IService;

import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * <p>
 * 点赞记录表 服务类
 * </p>
 *
 * @author 虎哥
 */
public interface ILikedRecordService extends IService<LikedRecord> {

    void addLikeRecord(LikeRecordFormDTO recordDTO);

    Set<Long> isBizLiked(List<Long> bizIds);

    Map<String, Object> contentLikeState(String bizType, Long bizId);

    Map<String, Object> recordContentView(String bizType, Long bizId);

    void readLikedTimesAndSendMessage(String bizType, int maxBizSize);
}
