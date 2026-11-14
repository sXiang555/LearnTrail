package com.learntrail.learning.mapper;

import com.learntrail.learning.domain.po.InteractionReply;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Delete;

/**
 * <p>
 * 互动问题的回答或评论 Mapper 接口
 * </p>
 *
 * @author 虎哥
 */
public interface InteractionReplyMapper extends BaseMapper<InteractionReply> {

    /**
     * 根据互动问题id删除互动回答
     */
    @Delete("delete from interaction_reply where question_id = #{questionId}")
    void deleteByQuestionId(Long questionId);
}
