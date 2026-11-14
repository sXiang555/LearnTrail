package com.learntrail.trade.mapper;

import com.learntrail.api.dto.points.PointsTrade;
import org.apache.ibatis.annotations.*;
import java.util.List;

public interface PointsOrderMapper {
    @Select("SELECT * FROM learntrail_point_order WHERE order_id=#{id} AND user_id=#{user}")
    PointsTrade.Order find(@Param("id") Long id, @Param("user") Long user);
    @Select("SELECT * FROM learntrail_point_order WHERE user_id=#{user} AND request_key=#{key}")
    PointsTrade.Order request(@Param("user") Long user, @Param("key") String key);
    @Select("SELECT * FROM learntrail_point_order WHERE order_id=#{id} AND user_id=#{user} FOR UPDATE")
    PointsTrade.Order lock(@Param("id") Long id, @Param("user") Long user);
    @Insert("INSERT IGNORE INTO learntrail_point_order(order_id,user_id,request_key,kind,target_id,title,slug," +
            "total_points,discount_points,payable_points,user_coupon_id) VALUES(#{orderId},#{userId},#{requestKey},#{kind}," +
            "#{targetId},#{title},#{slug},#{totalPoints},#{discountPoints},#{payablePoints},#{userCouponId})")
    int insert(PointsTrade.Order order);
    @Update("UPDATE learntrail_point_order SET phase=#{phase},updated_at=NOW() WHERE order_id=#{id}")
    void phase(@Param("id") Long id, @Param("phase") String phase);
    @Select("<script>SELECT COUNT(*) FROM learntrail_point_order WHERE user_id=#{user}" +
            "<if test='phase != null'> AND phase=#{phase}</if></script>")
    long count(@Param("user") Long user, @Param("phase") String phase);
    @Select("<script>SELECT * FROM learntrail_point_order WHERE user_id=#{user}" +
            "<if test='phase != null'> AND phase=#{phase}</if> ORDER BY created_at DESC,order_id DESC LIMIT #{size} OFFSET #{offset}</script>")
    List<PointsTrade.Order> page(@Param("user") Long user, @Param("phase") String phase,
            @Param("offset") int offset, @Param("size") int size);
    @Select("SELECT * FROM learntrail_point_order WHERE phase IN ('PAYING','CANCELLING') " +
            "AND updated_at < DATE_SUB(NOW(),INTERVAL 30 SECOND) ORDER BY updated_at LIMIT 30")
    List<PointsTrade.Order> pending();
    @Update("UPDATE learntrail_point_order SET updated_at=NOW() WHERE order_id=#{id} AND phase=#{phase}")
    void touch(@Param("id") Long id, @Param("phase") String phase);
    @Update("UPDATE `order` SET status=#{status},message=#{message},pay_channel='POINTS'," +
            "pay_time=IF(#{status}=2,NOW(),pay_time),close_time=IF(#{status}=3,NOW(),close_time) WHERE id=#{id} AND pay_channel='POINTS'")
    void orderStatus(@Param("id") Long id, @Param("status") int status, @Param("message") String message);
    @Update("UPDATE order_detail SET status=#{status},pay_channel='POINTS' WHERE order_id=#{id}")
    void detailStatus(@Param("id") Long id, @Param("status") int status);
}
