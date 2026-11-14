package com.tianji.learning.domain.vo;

import lombok.Data;
import java.time.LocalDateTime;
import java.util.List;

/** 可消费积分，与签到积分、排行榜分开核算。 */
public final class StudioPointsView {
    private StudioPointsView() { }

    @Data
    public static class Article {
        private Long id;
        private String slug;
        private String title;
        private String summary;
        private String body;
        private String sourceType = "original";
        private boolean rewarded;
    }

    @Data
    public static class Entry {
        private String kind;
        private Long targetId;
        private String title;
        private Integer amount;
        private LocalDateTime createdAt;
    }

    @Data
    public static class Wallet {
        private Integer balance;
        private int initialPoints;
        private int readingReward;
        private List<Entry> entries;
    }

    @Data
    public static class Result {
        private Integer balance;
        private int awarded;
        private int spent;
        private Long lessonId;
    }
}
