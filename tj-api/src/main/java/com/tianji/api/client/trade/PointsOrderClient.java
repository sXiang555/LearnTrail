package com.tianji.api.client.trade;

import com.tianji.api.dto.points.PointsTrade;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;

@FeignClient(contextId = "pointsOrder", value = "trade-service")
public interface PointsOrderClient {
    @PostMapping("/point-orders")
    PointsTrade.Order create(@RequestBody PointsTrade.Create form);
    @PostMapping("/point-orders/{id}/pay")
    PointsTrade.Order pay(@PathVariable("id") Long id);
    @GetMapping("/point-orders/{id}/snapshot")
    PointsTrade.Order snapshot(@PathVariable("id") Long id);
}
