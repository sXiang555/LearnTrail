package com.learntrail.media.domain.dto;

import com.learntrail.api.client.media.StudioMediaClient;
import lombok.Data;
import javax.validation.constraints.*;
import java.time.LocalDateTime;
import java.util.Collections;
import java.util.List;

/** 独立上传协议；不改变既有媒资/课程 ID 和已发布视频引用。 */
public final class MediaUploadModels {
    private MediaUploadModels() { }

    @Data
    public static class Init {
        @NotBlank @Pattern(regexp = "[A-Za-z0-9_-]{8,80}") private String requestKey;
        @NotBlank @Size(max = 100) private String filename;
        @Positive private long size;
        @NotBlank @Pattern(regexp = "[a-f0-9]{64}") private String sha256;
    }

    @Data
    public static class PartReceipt {
        @Min(1) @Max(10000) private int number;
        @NotBlank @Size(max = 150) private String etag;
    }

    @Data
    public static class Session {
        private String id;
        private Long userId;
        private String requestKey;
        private String filename;
        private long fileSize;
        private String sha256;
        private int partSize;
        private int partCount;
        private String bucket;
        private String objectKey;
        private String storageUploadId;
        private String phase;
        private Long mediaId;
        private String leaseToken;
        private LocalDateTime leaseUntil;
        private LocalDateTime expiresAt;
        private String errorCode;
    }

    @Data
    public static class Part {
        private int number;
        private String etag;
        private long size;
        public Part() { }
        public Part(int number, String etag, long size) {
            this.number = number; this.etag = etag; this.size = size;
        }
    }

    @Data
    public static class Task {
        private Long id;
        private String uploadId;
        private Long mediaId;
        private int pipelineVersion;
        private String status;
        private int attempts;
        private String runToken;
        private LocalDateTime leaseUntil;
        private String errorCode;
    }

    @Data
    public static class View {
        private String id;
        private String filename;
        private String phase;
        private long size;
        private int partSize;
        private int partCount;
        private int parallelism;
        private LocalDateTime expiresAt;
        private String error;
        private List<Part> parts = Collections.emptyList();
        private StudioMediaClient.Asset asset;
    }

    @Data
    public static class SignedPart {
        private int number;
        private String url;
        private long size;
        private int expiresIn;
    }

    @Data
    public static class Output {
        private float duration;
        private long size;
        private String objectPrefix;
    }
}
