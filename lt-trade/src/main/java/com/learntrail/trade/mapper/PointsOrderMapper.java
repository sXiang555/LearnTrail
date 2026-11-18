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
    @Update("UPDATE learntrail_point_order SET phase=#{phase},updated_at=NOW(),recovery_attempts=0," +
            "recovery_due_at=DATE_ADD(NOW(3),INTERVAL 30 SECOND),recovery_token=NULL," +
            "recovery_lease_until=NULL,recovery_error=NULL WHERE order_id=#{id}")
    void phase(@Param("id") Long id, @Param("phase") String phase);
    @Select("<script>SELECT COUNT(*) FROM learntrail_point_order WHERE user_id=#{user}" +
            "<if test='phase != null'> AND phase=#{phase}</if></script>")
    long count(@Param("user") Long user, @Param("phase") String phase);
    @Select("<script>SELECT * FROM learntrail_point_order WHERE user_id=#{user}" +
            "<if test='phase != null'> AND phase=#{phase}</if> ORDER BY created_at DESC,order_id DESC LIMIT #{size} OFFSET #{offset}</script>")
    List<PointsTrade.Order> page(@Param("user") Long user, @Param("phase") String phase,
            @Param("offset") int offset, @Param("size") int size);
    String DUE = "phase IN ('PAYING','CANCELLING') AND recovery_attempts<8 " +
            "AND (recovery_due_at<=NOW(3) OR (recovery_due_at IS NULL AND updated_at<=DATE_SUB(NOW(3),INTERVAL 30 SECOND))) " +
            "AND (recovery_lease_until IS NULL OR recovery_lease_until<=NOW(3))";

    @Select("SELECT * FROM learntrail_point_order WHERE " + DUE + " ORDER BY recovery_due_at,order_id LIMIT 20")
    List<PointsTrade.Order> pending();
    @Update("UPDATE learntrail_point_order SET recovery_token=#{token},recovery_attempts=recovery_attempts+1," +
            "recovery_lease_until=DATE_ADD(NOW(3),INTERVAL 120 SECOND),recovery_error='INTERRUPTED' " +
            "WHERE order_id=#{id} AND phase=#{phase} AND " + DUE)
    int claimRecovery(@Param("id") Long id, @Param("phase") String phase, @Param("token") String token);
    @Update("UPDATE learntrail_point_order SET recovery_due_at=DATE_ADD(NOW(3), " +
            "INTERVAL LEAST(1800,30*(1 << (recovery_attempts-1))) SECOND),recovery_error=#{error}," +
            "recovery_lease_until=NULL,recovery_token=NULL WHERE order_id=#{id} AND phase=#{phase} AND recovery_token=#{token}")
    int retryRecovery(@Param("id") Long id, @Param("phase") String phase,
                      @Param("token") String token, @Param("error") String error);
    @Update("UPDATE `order` SET status=#{status},message=#{message},pay_channel='POINTS'," +
            "pay_time=IF(#{status}=2,NOW(),pay_time),close_time=IF(#{status}=3,NOW(),close_time) WHERE id=#{id} AND pay_channel='POINTS'")
    void orderStatus(@Param("id") Long id, @Param("status") int status, @Param("message") String message);
    @Update("UPDATE order_detail SET status=#{status},pay_channel='POINTS' WHERE order_id=#{id}")
    void detailStatus(@Param("id") Long id, @Param("status") int status);
}
