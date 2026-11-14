package com.learntrail.promotion.controller;

import com.learntrail.api.dto.points.PointsTrade;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.promotion.service.PointsCouponService;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import javax.validation.Valid;
import javax.validation.constraints.*;

@RestController
@RequestMapping("/point-coupons")
@RequiredArgsConstructor
@Validated
public class PointsCouponController {
    private final PointsCouponService service;
    @GetMapping("/catalog")
    public PageDTO<PointsTrade.Coupon> catalog(@RequestParam(defaultValue = "1") @Min(1) @Max(100000) int pageNo,
            @RequestParam(defaultValue = "10") @Min(1) @Max(30) int pageSize) { return service.catalog(pageNo, pageSize); }
    @GetMapping("/mine")
    public PageDTO<PointsTrade.Coupon> mine(@RequestParam(required = false) @Min(1) @Max(3) Integer status,
            @RequestParam(defaultValue = "1") @Min(1) @Max(100000) int pageNo,
            @RequestParam(defaultValue = "10") @Min(1) @Max(30) int pageSize) { return service.mine(status, pageNo, pageSize); }
    @GetMapping("/templates/{id}")
    public PointsTrade.Coupon template(@PathVariable @Positive Long id) { return service.template(id); }
    @PostMapping
    public Long create(@Valid @RequestBody PointsTrade.CouponEdit edit) { return service.create(edit); }
    @PostMapping("/templates/{id}/receive")
    public Long receive(@PathVariable @Positive Long id,
            @RequestHeader(value = "Idempotency-Key", required = false) @Pattern(regexp = "[A-Za-z0-9_-]{8,40}") String requestKey) {
        return requestKey == null ? service.receive(id) : service.receive(id, requestKey);
    }
    @PostMapping("/quote")
    public Integer quote(@Valid @RequestBody PointsTrade.Quote quote) { return service.quote(quote); }
    @GetMapping("/settlements/{id}")
    public PointsTrade.Reservation reservation(@PathVariable @Positive Long id) { return service.reservation(id); }
    @PostMapping("/settlements/{id}/reserve")
    public void reserve(@PathVariable @Positive Long id) { service.reserve(id); }
    @PostMapping("/settlements/{id}/complete")
    public void complete(@PathVariable @Positive Long id) { service.complete(id); }
    @PostMapping("/settlements/{id}/release")
    public void release(@PathVariable @Positive Long id) { service.release(id); }
}
