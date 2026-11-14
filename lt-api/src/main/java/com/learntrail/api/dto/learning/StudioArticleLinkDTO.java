package com.learntrail.api.dto.learning;

import lombok.Data;

/** 配套文章引用；正文继续由原文章接口读取。 */
@Data
public class StudioArticleLinkDTO {
    private Long id;
    private String slug;
    private String title;
    private String summary;
}
