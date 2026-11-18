package com.learntrail.search.controller;

import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.search.service.PublicContentIndex;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import javax.validation.constraints.Max;
import javax.validation.constraints.Min;
import javax.validation.constraints.NotBlank;
import javax.validation.constraints.Pattern;
import javax.validation.constraints.Size;
import java.io.IOException;

@RestController
@Validated
@RequiredArgsConstructor
@ConditionalOnProperty(name = "learntrail.search.content.enabled", havingValue = "true")
public class PublicContentSearchController {
    private final PublicContentIndex index;

    @GetMapping("/content-search")
    public PageDTO<PublicContentIndex.Result> search(
            @RequestParam @NotBlank @Size(max = 80) String q,
            @RequestParam(defaultValue = "all") @Pattern(regexp = "all|article|series") String kind,
            @RequestParam(defaultValue = "1") @Min(1) @Max(500) int pageNo,
            @RequestParam(defaultValue = "12") @Min(1) @Max(20) int pageSize) throws IOException {
        return index.search(q.strip(), kind, pageNo, pageSize);
    }
}
