package com.learntrail.api.client.promotion;

import com.learntrail.api.dto.points.PointsTrade;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;

@FeignClient(contextId = "pointsCoupon", value = "lt-promotion")
public interface PointsCouponClient {
    @GetMapping("/point-coupons/templates/{id}")
    PointsTrade.Coupon template(@PathVariable("id") Long id);
    @PostMapping("/point-coupons/quote")
    Integer quote(@RequestBody PointsTrade.Quote quote);
    @PostMapping("/point-coupons/settlements/{id}/reserve")
    void reserve(@PathVariable("id") Long id);
    @PostMapping("/point-coupons/settlements/{id}/complete")
    void complete(@PathVariable("id") Long id);
    @PostMapping("/point-coupons/settlements/{id}/release")
    void release(@PathVariable("id") Long id);
    @GetMapping("/point-coupons/settlements/{id}")
    PointsTrade.Reservation reservation(@PathVariable("id") Long id);
}
