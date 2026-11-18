package com.learntrail.media.controller;

import com.learntrail.media.domain.dto.MediaUploadModels.*;
import com.learntrail.media.service.MediaUploadService;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import javax.validation.Valid;
import javax.validation.constraints.*;
import java.util.Map;

@RestController
@RequestMapping("/medias/studio/uploads")
@RequiredArgsConstructor
@Validated
public class MediaUploadController {
    private final MediaUploadService service;
    @GetMapping("/capabilities")
    public Map<String, Object> capabilities() { return service.capabilities(); }
    @PostMapping
    public View initiate(@Valid @RequestBody Init form) { return service.initiate(form); }
    @GetMapping("/{id}")
    public View status(@PathVariable @Pattern(regexp = "[a-f0-9]{32}") String id) { return service.status(id); }
    @PostMapping("/{id}/parts/{number}/sign")
    public SignedPart sign(@PathVariable @Pattern(regexp = "[a-f0-9]{32}") String id,
                           @PathVariable @Min(1) @Max(10000) int number) { return service.sign(id, number); }
    @PutMapping("/{id}/parts")
    public void remember(@PathVariable @Pattern(regexp = "[a-f0-9]{32}") String id,
                         @Valid @RequestBody PartReceipt receipt) { service.remember(id, receipt); }
    @PostMapping("/{id}/complete")
    public View complete(@PathVariable @Pattern(regexp = "[a-f0-9]{32}") String id) { return service.complete(id); }
    @PostMapping("/{id}/abort")
    public void abort(@PathVariable @Pattern(regexp = "[a-f0-9]{32}") String id) { service.abort(id); }
}
