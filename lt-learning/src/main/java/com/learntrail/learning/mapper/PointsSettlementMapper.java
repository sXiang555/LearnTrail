package com.learntrail.learning.mapper;

import com.learntrail.api.dto.points.PointsTrade;
import org.apache.ibatis.annotations.*;

public interface PointsSettlementMapper {
    @Select("SELECT * FROM learntrail_point_payment WHERE order_id=#{id} AND user_id=#{user}")
    PointsTrade.Receipt receipt(@Param("id") Long id, @Param("user") Long user);

    @Insert("INSERT INTO learntrail_point_payment(order_id,user_id,kind,target_id,amount,status) " +
            "VALUES(#{orderId},#{userId},#{kind},#{targetId},#{amount},#{status})")
    void insert(PointsTrade.Receipt receipt);
}
