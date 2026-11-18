package com.learntrail.media.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;
import javax.annotation.PostConstruct;
import java.net.URI;
import java.nio.file.Path;

@Getter
@Setter
@Component
@ConfigurationProperties(prefix = "learntrail.media.upload")
public class MediaUploadProperties {
    private boolean enabled;
    private String endpoint = "";
    private String publicEndpoint = "";
    private String accessKey = "";
    private String secretKey = "";
    private String bucket = "learntrail-media";
    private String region = "us-east-1";
    private long maxFileBytes = 1024L * 1024 * 1024;
    private int partBytes = 8 * 1024 * 1024;
    private int uploadParallelism = 3;
    private int urlSeconds = 900;
    private int sessionHours = 24;
    private int workers = 2;
    private int queueCapacity = 2;
    private int leaseSeconds = 120;
    private int maxAttempts = 3;
    private int processTimeoutSeconds = 1800;
    private static final String DEFAULT_TEMPORARY_DIRECTORY = System.getProperty("java.io.tmpdir") + "/learntrail-media";
    private String temporaryDirectory = DEFAULT_TEMPORARY_DIRECTORY;

    @PostConstruct
    public void validate() {
        if (!enabled) return;
        if (maxFileBytes < 1 || maxFileBytes > 10L * 1024 * 1024 * 1024
                || partBytes < 5 * 1024 * 1024 || partBytes > 64 * 1024 * 1024
                || (maxFileBytes + partBytes - 1) / partBytes > 10000
                || uploadParallelism < 1 || uploadParallelism > 6
                || urlSeconds < 60 || urlSeconds > 3600 || sessionHours < 1 || sessionHours > 72
                || workers < 1 || workers > 4 || queueCapacity < 1 || queueCapacity > 16
                || leaseSeconds < 60 || leaseSeconds > 600 || maxAttempts < 1 || maxAttempts > 5
                || processTimeoutSeconds < 60 || processTimeoutSeconds > 7200
                || accessKey.isBlank() || secretKey.isBlank()
                || !bucket.matches("[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")) {
            throw new IllegalStateException("MinIO 上传配置缺失或超过允许边界");
        }
        endpoint(endpoint);
        if (!publicEndpoint.isBlank()) endpoint(publicEndpoint);
        if (temporaryDirectory.isBlank()) temporaryDirectory = DEFAULT_TEMPORARY_DIRECTORY;
        Path.of(temporaryDirectory).toAbsolutePath().normalize();
    }

    private void endpoint(String value) {
        try {
            URI uri = URI.create(value);
            if (("http".equals(uri.getScheme()) || "https".equals(uri.getScheme()))
                    && uri.getHost() != null && uri.getUserInfo() == null
                    && (uri.getPath() == null || uri.getPath().isEmpty() || "/".equals(uri.getPath()))
                    && uri.getQuery() == null && uri.getFragment() == null) return;
        } catch (IllegalArgumentException ignored) { }
        throw new IllegalStateException("MinIO endpoint 必须为不包含凭据和路径的 HTTP(S) 地址");
    }
}
