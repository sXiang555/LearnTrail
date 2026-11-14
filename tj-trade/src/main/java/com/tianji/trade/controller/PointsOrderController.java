package com.tianji.trade.controller;

import com.tianji.api.dto.points.PointsTrade;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.trade.service.PointsOrderService;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import javax.validation.Valid;
import javax.validation.constraints.*;

@RestController
@RequestMapping("/point-orders")
@RequiredArgsConstructor
@Validated
public class PointsOrderController {
    private final PointsOrderService service;
    @PostMapping
    public PointsTrade.Order create(@Valid @RequestBody PointsTrade.Create form) { return service.create(form); }
    @GetMapping("/{id}")
    public PointsTrade.Order detail(@PathVariable @Positive Long id) { return service.snapshot(id); }
    @GetMapping("/{id}/snapshot")
    public PointsTrade.Order snapshot(@PathVariable @Positive Long id) { return service.snapshot(id); }
    @GetMapping
    public PageDTO<PointsTrade.Order> page(@RequestParam(required = false) @Pattern(regexp = "CREATED|PAYING|PAID|CANCELLING|CANCELLED") String phase,
            @RequestParam(defaultValue = "1") @Min(1) @Max(100000) int pageNo,
            @RequestParam(defaultValue = "10") @Min(1) @Max(30) int pageSize) { return service.page(phase, pageNo, pageSize); }
    @PostMapping("/{id}/pay")
    public PointsTrade.Order pay(@PathVariable @Positive Long id) { return service.pay(id); }
    @PostMapping("/{id}/cancel")
    public PointsTrade.Order cancel(@PathVariable @Positive Long id) { return service.cancel(id); }
}
