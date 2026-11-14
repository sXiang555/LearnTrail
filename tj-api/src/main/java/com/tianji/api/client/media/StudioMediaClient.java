package com.tianji.api.client.media;

import lombok.Data;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.List;

@FeignClient(contextId = "studioMedia", value = "media-service")
public interface StudioMediaClient {
    @GetMapping("/medias/studio")
    List<Asset> ownedAssets(@RequestParam("ids") List<Long> ids,
            @RequestParam(value = "courseId", required = false) Long courseId);

    @Data
    class Asset {
        private Long id;
        private String filename;
        private int duration;
        private boolean ready;
    }
}
