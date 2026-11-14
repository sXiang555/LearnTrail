package com.learntrail.promotion.mapper;

import com.learntrail.api.dto.points.PointsTrade;
import com.learntrail.promotion.domain.po.Coupon;
import com.learntrail.promotion.domain.po.UserCoupon;
import org.apache.ibatis.annotations.*;
import java.util.List;

public interface PointsCouponMapper {
    String COLUMNS = "c.id,c.id AS template_id,c.name,c.discount_type,c.discount_value,c.threshold_amount," +
            "c.max_discount_amount,p.exchange_points,c.total_num,c.issue_num,c.user_limit,c.status,c.specific," +
            "c.term_days,c.term_begin_time,c.term_end_time,c.issue_begin_time,c.issue_end_time";
    @Select("SELECT " + COLUMNS + " FROM coupon c JOIN learntrail_coupon_points p ON p.coupon_id=c.id WHERE c.id=#{id}")
    PointsTrade.Coupon template(Long id);
    @Select("SELECT COUNT(*) FROM coupon c JOIN learntrail_coupon_points p ON p.coupon_id=c.id " +
            "WHERE c.status=3 AND NOW() BETWEEN c.issue_begin_time AND c.issue_end_time")
    long countCatalog();
    @Select("SELECT " + COLUMNS + " FROM coupon c JOIN learntrail_coupon_points p ON p.coupon_id=c.id " +
            "WHERE c.status=3 AND NOW() BETWEEN c.issue_begin_time AND c.issue_end_time ORDER BY c.id DESC LIMIT #{size} OFFSET #{offset}")
    List<PointsTrade.Coupon> catalog(@Param("offset") int offset, @Param("size") int size);

    String MINE_JOIN = " FROM user_coupon u JOIN coupon c ON c.id=u.coupon_id JOIN learntrail_coupon_points p ON p.coupon_id=c.id " +
            "WHERE u.user_id=#{user} AND NOT EXISTS(SELECT 1 FROM learntrail_coupon_claim q WHERE q.user_coupon_id=u.id AND q.status IN ('CANCELLED','RESERVED')) ";
    String MINE_STATUS = "<if test='status != null'>AND (CASE WHEN u.status=1 AND u.term_end_time&lt;NOW() THEN 3 ELSE u.status END)=#{status}</if>";
    @Select("<script>SELECT COUNT(*)" + MINE_JOIN + MINE_STATUS + "</script>")
    long countMine(@Param("user") Long user, @Param("status") Integer status);
    @Select("<script>SELECT c.id AS template_id,u.id,c.name,c.discount_type,c.discount_value,c.threshold_amount," +
            "c.max_discount_amount,p.exchange_points,c.specific,u.term_begin_time,u.term_end_time," +
            "CASE WHEN u.status=1 AND u.term_end_time&lt;NOW() THEN 3 ELSE u.status END AS status," +
            "EXISTS(SELECT 1 FROM learntrail_coupon_reservation r WHERE r.active_coupon_id=u.id AND r.status='RESERVED') AS reserved" +
            MINE_JOIN + MINE_STATUS + " ORDER BY u.create_time DESC,u.id DESC LIMIT #{size} OFFSET #{offset}</script>")
    List<PointsTrade.Coupon> mine(@Param("user") Long user, @Param("status") Integer status,
            @Param("offset") int offset, @Param("size") int size);
    @Select("SELECT * FROM coupon WHERE id=#{id} FOR UPDATE")
    Coupon lockTemplate(Long id);
    @Select("SELECT * FROM user_coupon WHERE id=#{id} AND user_id=#{user} FOR UPDATE")
    UserCoupon lockUserCoupon(@Param("id") Long id, @Param("user") Long user);
    @Select("SELECT COUNT(*) FROM user_coupon u WHERE u.coupon_id=#{coupon} AND u.user_id=#{user} " +
            "AND NOT EXISTS(SELECT 1 FROM learntrail_coupon_claim q WHERE q.user_coupon_id=u.id AND q.status='CANCELLED')")
    int received(@Param("coupon") Long coupon, @Param("user") Long user);
    @Insert("INSERT INTO learntrail_coupon_points(coupon_id,exchange_points) VALUES(#{id},#{cost})")
    void enable(@Param("id") Long id, @Param("cost") int cost);
    @Insert("INSERT INTO coupon_scope(type,coupon_id,biz_id) VALUES(2,#{id},#{course})")
    void addScope(@Param("id") Long id, @Param("course") Long course);
    @Update("UPDATE coupon SET issue_num=issue_num-1 WHERE id=#{id} AND issue_num>0")
    void restoreStock(Long id);
    @Update("UPDATE coupon SET used_num=used_num+1 WHERE id=#{id}")
    void used(Long id);

    @Select("SELECT * FROM learntrail_coupon_reservation WHERE order_id=#{id} AND user_id=#{user}")
    PointsTrade.Reservation reservation(@Param("id") Long id, @Param("user") Long user);
    @Select("SELECT order_id FROM learntrail_coupon_reservation WHERE active_coupon_id=#{id} AND status='RESERVED'")
    Long reservedBy(Long id);
    @Insert("INSERT INTO learntrail_coupon_reservation(order_id,user_id,user_coupon_id,active_coupon_id,discount_points,status) " +
            "VALUES(#{orderId},#{userId},#{userCouponId},IF(#{status}='RESERVED',#{userCouponId},NULL),#{discountPoints},#{status})")
    void insertReservation(PointsTrade.Reservation reservation);
    @Update("UPDATE learntrail_coupon_reservation SET status=#{status},active_coupon_id=IF(#{status}='RELEASED',NULL,active_coupon_id) WHERE order_id=#{id}")
    void reservationStatus(@Param("id") Long id, @Param("status") String status);
    @Select("SELECT * FROM learntrail_coupon_claim WHERE order_id=#{id} AND user_id=#{user}")
    PointsTrade.Claim claim(@Param("id") Long id, @Param("user") Long user);
    @Select("SELECT * FROM learntrail_coupon_claim WHERE claim_key=#{key}")
    PointsTrade.Claim claimByKey(String key);
    @Insert("INSERT INTO learntrail_coupon_claim(order_id,user_id,coupon_id,user_coupon_id,claim_key,status) " +
            "VALUES(#{orderId},#{userId},#{couponId},#{userCouponId},#{claimKey},#{status})")
    void insertClaim(PointsTrade.Claim claim);
    @Update("UPDATE learntrail_coupon_claim SET status=#{status} WHERE order_id=#{id}")
    void claimStatus(@Param("id") Long id, @Param("status") String status);
}
