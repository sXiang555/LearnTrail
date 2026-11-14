package com.tianji.learning.controller;

import com.tianji.common.domain.dto.PageDTO;
import com.tianji.learning.domain.po.StudioArticle;
import com.tianji.learning.service.StudioArticleService;
import com.tianji.api.dto.learning.StudioArticleLinkDTO;
import io.swagger.annotations.Api;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import javax.validation.Valid;
import javax.validation.constraints.*;
import java.util.Map;
import java.util.List;

@RestController
@RequestMapping("/studio-articles")
@Api(tags = "LearnTrail 文章创作与运营")
@RequiredArgsConstructor
@Validated
public class StudioArticleController {
    private final StudioArticleService service;

    @GetMapping("/published/links")
    public List<StudioArticleLinkDTO> links(@RequestParam @Size(min = 1, max = 20) List<@Positive Long> ids,
            @RequestParam(defaultValue = "false") boolean owned) { return service.links(ids, owned); }

    @GetMapping("/access")
    public Map<String, Boolean> access() { return service.access(); }

    @GetMapping("/published")
    public PageDTO<StudioArticle> published(@RequestParam(required = false) @Size(max = 80) String q,
            @RequestParam(required = false) @Positive Long author,
            @RequestParam(defaultValue = "1") @Min(1) @Max(100000) int pageNo,
            @RequestParam(defaultValue = "10") @Min(1) @Max(30) int pageSize,
            @RequestParam(defaultValue = "newest") @Pattern(regexp = "newest|likes|views") String sort) {
        return service.page("published", q, null, pageNo, pageSize, author, sort);
    }

    @GetMapping("/published-counts")
    public Map<Long, Long> publishedCounts() { return service.publishedCounts(); }

    @GetMapping("/published/{slug}")
    public StudioArticle publishedArticle(@PathVariable @Pattern(regexp = "[a-z0-9-]{1,80}") String slug) {
        return service.published(slug);
    }

    @GetMapping("/manage")
    public PageDTO<StudioArticle> manage(@RequestParam(defaultValue = "mine") @Pattern(regexp = "mine|admin") String scope,
            @RequestParam(required = false) @Size(max = 80) String q,
            @RequestParam(required = false) @Pattern(regexp = "|draft|published|archived|blocked") String status,
            @RequestParam(defaultValue = "1") @Min(1) @Max(100000) int pageNo,
            @RequestParam(defaultValue = "10") @Min(1) @Max(30) int pageSize) {
        return service.page(scope, q, status, pageNo, pageSize);
    }

    @GetMapping("/{id}")
    public StudioArticle editable(@PathVariable @Positive Long id) { return service.editable(id); }

    @PostMapping
    public Long create(@Valid @RequestBody StudioArticle.Edit edit) { return service.create(edit); }

    @PutMapping("/{id}")
    public void edit(@PathVariable @Positive Long id, @Valid @RequestBody StudioArticle.Edit edit) { service.edit(id, edit); }

    @PutMapping("/{id}/state/{state}")
    public void state(@PathVariable @Positive Long id,
            @PathVariable @Pattern(regexp = "draft|published|archived|blocked") String state) { service.state(id, state); }
}
