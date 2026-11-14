package com.tianji.learning.controller;

import com.tianji.learning.domain.vo.StudioPointsView;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.learning.service.StudioPointsService;
import io.swagger.annotations.Api;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import javax.validation.constraints.Pattern;
import javax.validation.constraints.Positive;
import javax.validation.constraints.Min;
import javax.validation.constraints.Max;
import java.util.List;
import com.tianji.api.client.trade.PointsOrderClient;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.learning.mapper.StudioPointsMapper;
import com.tianji.common.utils.UserContext;
import com.tianji.common.exceptions.UnauthorizedException;

@RestController
@RequestMapping("/studio-points")
@Api(tags = "LearnTrail 阅读积分与专栏解锁")
@Validated
@RequiredArgsConstructor
public class StudioPointsController {
    private final StudioPointsService service;
    private final PointsOrderClient orders;
    private final StudioPointsMapper mapper;

    @GetMapping("/entitlements")
    public List<Long> entitlements() { return service.entitlements(); }

    @GetMapping("/wallet")
    public StudioPointsView.Wallet wallet() { return service.wallet(); }

    @GetMapping("/entries")
    public PageDTO<StudioPointsView.Entry> entries(@RequestParam(defaultValue = "1") @Min(1) @Max(100000) int pageNo,
            @RequestParam(defaultValue = "10") @Min(1) @Max(30) int pageSize,
            @RequestParam(required = false) @Pattern(regexp = "INITIAL|READ|WATCH|SIGN|COMMENT|UNLOCK|COUPON") String kind,
            @RequestParam(required = false) @Pattern(regexp = "income|expense") String direction) {
        return service.entries(pageNo, pageSize, kind, direction);
    }

    @GetMapping("/articles")
    public List<StudioPointsView.Article> articles() { return service.articles(); }

    @GetMapping("/articles/{slug}")
    public StudioPointsView.Article article(@PathVariable @Pattern(regexp = "[a-z0-9-]{1,80}") String slug) {
        return service.article(slug);
    }

    @PostMapping("/articles/{slug}/read")
    public StudioPointsView.Result read(@PathVariable @Pattern(regexp = "[a-z0-9-]{1,80}") String slug) {
        return service.read(slug);
    }

    @PostMapping("/unlocks/{courseId}")
    public StudioPointsView.Result unlock(@PathVariable @Positive Long courseId) {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录");
        if (mapper.validLesson(user, courseId) != null || mapper.hasEntry(user, "UNLOCK", courseId) > 0) {
            return service.unlock(courseId);
        }
        PointsTrade.Create form = new PointsTrade.Create();
        form.setKind("COURSE"); form.setTargetId(courseId); form.setRequestKey("legacy_" + courseId);
        PointsTrade.Order created = orders.create(form);
        PointsTrade.Order paid = orders.pay(created.getOrderId());
        StudioPointsView.Result result = new StudioPointsView.Result();
        result.setBalance(service.wallet().getBalance());
        result.setLessonId(mapper.validLesson(user, courseId));
        result.setSpent("PAID".equals(created.getPhase()) ? 0 : paid.getPayablePoints());
        return result;
    }
}
