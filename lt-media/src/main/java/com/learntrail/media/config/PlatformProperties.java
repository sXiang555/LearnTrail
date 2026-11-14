package com.learntrail.media.config;

import com.learntrail.media.enums.Platform;
import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "tj.platform")
public class PlatformProperties {
    private Platform file;
    private Platform media;
}
