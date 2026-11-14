package com.learntrail.promotion.mapper;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.learntrail.promotion.domain.po.UserCoupon;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest
class UserCouponMapperTest {

    @Autowired
    private UserCouponMapper userCouponMapper;

    @Test
    void queryMyCoupons() {
        List<UserCoupon> coupons = userCouponMapper.selectList(
                Wrappers.<UserCoupon>lambdaQuery().eq(UserCoupon::getUserId, 2L));
        assertNotNull(coupons);
        coupons.forEach(coupon -> assertEquals(Long.valueOf(2L), coupon.getUserId()));
    }
}
