package com.learntrail.api.client.learning;

import com.learntrail.api.dto.points.PointsTrade;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;

@FeignClient(contextId = "pointsSettlement", value = "lt-learning")
public interface PointsSettlementClient {
    @PostMapping("/point-settlements/{id}/pay")
    PointsTrade.Receipt pay(@PathVariable("id") Long id);
    @PostMapping("/point-settlements/{id}/cancel")
    PointsTrade.Receipt cancel(@PathVariable("id") Long id);
    @GetMapping("/point-settlements/{id}")
    PointsTrade.Receipt receipt(@PathVariable("id") Long id);
}
