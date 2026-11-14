package com.tianji.learning.controller;

import com.tianji.api.dto.points.PointsTrade;
import com.tianji.learning.service.PointsSettlementService;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import javax.validation.constraints.Positive;

@RestController
@RequestMapping("/point-settlements")
@RequiredArgsConstructor
@Validated
public class PointsSettlementController {
    private final PointsSettlementService service;
    @GetMapping("/{id}")
    public PointsTrade.Receipt receipt(@PathVariable @Positive Long id) { return service.receipt(id); }
    @PostMapping("/{id}/pay")
    public PointsTrade.Receipt pay(@PathVariable @Positive Long id) { return service.pay(id); }
    @PostMapping("/{id}/cancel")
    public PointsTrade.Receipt cancel(@PathVariable @Positive Long id) { return service.cancel(id); }
}
