package com.learntrail.course.controller;

import com.learntrail.course.domain.vo.StudioView;
import com.learntrail.course.domain.query.StudioQuery;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.course.service.StudioService;
import io.swagger.annotations.Api;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import javax.validation.Valid;
import javax.validation.constraints.Size;
import javax.validation.constraints.Pattern;
import javax.validation.constraints.Min;
import javax.validation.constraints.Max;
import javax.validation.constraints.Positive;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/studio")
@Api(tags = "LearnTrail 创作者与付费专栏")
@Validated
@RequiredArgsConstructor
public class StudioController {
    private final StudioService service;

    @GetMapping("/home")
    public Map<String, Object> home() { return service.home(); }

    @GetMapping("/series/page")
    public PageDTO<StudioView.Series> page(@Valid StudioQuery query) { return service.page(query); }

    @GetMapping("/creators/page")
    public PageDTO<StudioView.Creator> creatorsPage(@RequestParam(required = false) @Size(max = 80) String q,
            @RequestParam(defaultValue = "1") @Min(1) @Max(100000) int pageNo,
            @RequestParam(defaultValue = "12") @Min(1) @Max(30) int pageSize) {
        return service.creatorsPage(q, pageNo, pageSize);
    }

    @GetMapping("/series")
    public List<StudioView.Series> series(@RequestParam(required = false) @Size(max = 80) String q,
            @RequestParam(required = false) @Size(max = 40) String topic,
            @RequestParam(required = false) String creator,
            @RequestParam(defaultValue = "false") boolean saved) {
        return service.list(q, topic, creator, saved);
    }

    @GetMapping("/series/{slug}")
    public StudioView.Series detail(@PathVariable String slug) { return service.detail(slug); }

    @GetMapping("/series/{id}/likeable")
    public boolean seriesLikeable(@PathVariable @Positive Long id) { return service.seriesLikeable(id); }

    @GetMapping("/articles/{articleId}/series")
    public List<StudioView.Series> relatedSeries(@PathVariable @Positive Long articleId) {
        return service.relatedSeries(articleId);
    }

    @GetMapping("/unlock-cost/{courseId}")
    public Integer unlockCost(@PathVariable Long courseId) { return service.unlockCost(courseId); }

    @GetMapping("/discussion/{courseId}")
    public boolean canDiscuss(@PathVariable Long courseId) { return service.canDiscuss(courseId); }

    @GetMapping("/creators/{slug}")
    public Map<String, Object> creator(@PathVariable String slug) { return service.creator(slug); }

    @GetMapping("/library")
    public List<StudioView.Series> library() { return service.library(); }

    @PostMapping("/series/{id}/watchlist")
    public void save(@PathVariable Long id) { service.save(id, true); }

    @DeleteMapping("/series/{id}/watchlist")
    public void unsave(@PathVariable Long id) { service.save(id, false); }

    @PostMapping("/creators/{slug}/follow")
    public void follow(@PathVariable String slug) { service.follow(slug, true); }

    @DeleteMapping("/creators/{slug}/follow")
    public void unfollow(@PathVariable String slug) { service.follow(slug, false); }

    @GetMapping("/workspace")
    public Map<String, Object> workspace() { return service.studio(); }

    @GetMapping("/workspace/followers")
    public PageDTO<StudioView.Follower> followers(@RequestParam(defaultValue = "1") @Min(1) @Max(100000) int pageNo,
            @RequestParam(defaultValue = "10") @Min(1) @Max(30) int pageSize) { return service.followers(pageNo, pageSize); }

    @GetMapping("/workspace/access")
    public boolean studioAccess() { return service.studioAccess(); }

    @PostMapping("/workspace")
    public StudioView.Creator openStudio(@Valid @RequestBody StudioView.CreatorEdit profile) {
        return service.openStudio(profile);
    }

    @GetMapping("/workspace/categories")
    public List<Map<String, Object>> categories() { return service.categories(); }

    @GetMapping("/workspace/media-ids/{courseId}")
    public List<Long> boundMediaIds(@PathVariable Long courseId) { return service.boundMediaIds(courseId); }

    @PostMapping("/workspace/series/{id}/revision")
    public void beginRevision(@PathVariable Long id) { service.beginRevision(id); }

    @PutMapping("/workspace/series/{id}/state/{state}")
    public void changeState(@PathVariable Long id,
            @PathVariable @Pattern(regexp = "unlisted|published|archived") String state) { service.changeState(id, state); }

    @PutMapping("/operations/series/{id}/blocked/{blocked}")
    public void moderate(@PathVariable Long id, @PathVariable boolean blocked) { service.moderate(id, blocked); }

    @PostMapping("/workspace/series")
    public Long createDraft(@Valid @RequestBody StudioView.SeriesCreate edit) {
        return service.createDraft(edit);
    }

    @GetMapping("/workspace/series/{id}")
    public Map<String, Object> draft(@PathVariable Long id) { return service.draft(id); }

    @PutMapping("/workspace/series/{id}")
    public void editDraft(@PathVariable Long id, @Valid @RequestBody StudioView.SeriesCreate edit) {
        service.editDraft(id, edit);
    }

    @PutMapping("/workspace/series/{id}/episodes")
    public void saveEpisodes(@PathVariable Long id, @Valid @RequestBody StudioView.EpisodesEdit edit) {
        service.saveEpisodes(id, edit);
    }

    @PostMapping("/workspace/series/{id}/publish")
    public String publish(@PathVariable Long id) { return service.publish(id); }

    @PutMapping("/series/{id}")
    public void editSeries(@PathVariable Long id, @Valid @RequestBody StudioView.SeriesEdit edit) {
        service.editSeries(id, edit);
    }

    @PutMapping("/creators/me")
    public void editCreator(@Valid @RequestBody StudioView.CreatorEdit edit) { service.editCreator(edit); }
}
