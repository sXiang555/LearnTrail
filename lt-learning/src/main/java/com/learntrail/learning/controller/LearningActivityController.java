package com.learntrail.learning.controller;

import com.learntrail.api.dto.points.PointsTrade;
import com.learntrail.learning.domain.po.LearningActivity;
import com.learntrail.learning.domain.vo.StudioPointsView;
import com.learntrail.learning.service.LearningActivityService;
import com.learntrail.learning.service.StudioPointsService;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import javax.validation.Valid;
import javax.validation.constraints.*;
import java.util.List;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/studio-activities")
@RequiredArgsConstructor
@Validated
public class LearningActivityController {
    private final LearningActivityService service;
    private final StudioPointsService wallet;
    @GetMapping("/rules")
    public List<PointsTrade.Rule> rules() { return List.of("READ", "WATCH", "SIGN", "COMMENT").stream().map(wallet::rule).collect(Collectors.toList()); }
    @PostMapping("/{kind}/{target}/start")
    public LearningActivity.Progress start(@PathVariable @Pattern(regexp = "READ|WATCH") String kind,
            @PathVariable @Positive Long target) { return service.start(kind, target); }
    @PostMapping("/{id}/heartbeat")
    public LearningActivity.Progress heartbeat(@PathVariable @Positive Long id, @Valid @RequestBody LearningActivity.Heartbeat body) {
        return service.heartbeat(id, body.getPosition());
    }
    @PostMapping("/sign")
    public StudioPointsView.Result sign() { return service.sign(); }
}
