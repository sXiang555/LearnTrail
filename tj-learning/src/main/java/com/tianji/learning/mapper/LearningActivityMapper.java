package com.tianji.learning.mapper;

import com.tianji.learning.domain.po.LearningActivity;
import org.apache.ibatis.annotations.*;

public interface LearningActivityMapper {
    @Insert("INSERT INTO learntrail_learning_activity(id,user_id,kind,target_id,last_seen) VALUES(#{id},#{userId},#{kind},#{targetId},#{lastSeen})")
    void insert(LearningActivity activity);
    @Select("SELECT * FROM learntrail_learning_activity WHERE id=#{id} AND user_id=#{user} FOR UPDATE")
    LearningActivity lock(@Param("id") Long id, @Param("user") Long user);
    @Update("UPDATE learntrail_learning_activity SET credited_seconds=#{creditedSeconds},last_position=#{lastPosition},last_seen=#{lastSeen} WHERE id=#{id}")
    void update(LearningActivity activity);
}
