package com.learntrail.learning.domain.po;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.annotation.JsonIgnore;
import com.baomidou.mybatisplus.annotation.FieldStrategy;
import lombok.Data;
import javax.validation.constraints.NotBlank;
import javax.validation.constraints.Pattern;
import javax.validation.constraints.Size;
import java.time.LocalDateTime;

@Data
@TableName("learntrail_article")
public class StudioArticle {
    @TableId(type = IdType.ASSIGN_ID)
    private Long id;
    private String slug;
    private String title;
    private String summary;
    private String body;
    private String contentFormat = "text";
    private String sourceType = "original";
    private String sourceAuthor = "";
    private String sourceUrl = "";
    private String licenseNote = "";
    private Long authorUserId;
    private String status;
    @TableField(insertStrategy = FieldStrategy.NEVER, updateStrategy = FieldStrategy.NEVER)
    private Long likedTimes = 0L;
    @TableField(insertStrategy = FieldStrategy.NEVER, updateStrategy = FieldStrategy.NEVER)
    private Long viewTimes = 0L;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
    private LocalDateTime publishedAt;
    @TableField(exist = false)
    private String authorName;
    @TableField(exist = false)
    private boolean rewarded;
    @TableField(exist = false)
    @JsonIgnore
    private Long publicRevision;

    @Data
    public static class AuthorCount {
        private Long authorUserId;
        private Long articleCount;
    }

    @Data
    public static class Edit {
        @NotBlank @Size(max = 100)
        private String title;
        @NotBlank @Size(max = 240)
        private String summary;
        @NotBlank @Size(max = 200000)
        private String body;
        @NotBlank @Pattern(regexp = "text|markdown")
        private String contentFormat = "text";
        @NotBlank @Pattern(regexp = "original|reprint|link")
        private String sourceType = "original";
        @Size(max = 100)
        private String sourceAuthor;
        @Size(max = 2048)
        private String sourceUrl;
        @Size(max = 500)
        private String licenseNote;
    }
}
