package com.learntrail.learning.domain.po;

import lombok.Data;
import java.time.LocalDateTime;
import javax.validation.constraints.*;

@Data
public class LearningActivity {
    private Long id;
    private Long userId;
    private String kind;
    private Long targetId;
    private Integer creditedSeconds;
    private Double lastPosition;
    private LocalDateTime lastSeen;
    private LocalDateTime createdAt;

    @Data
    public static class Heartbeat {
        @NotNull @DecimalMin("0") @DecimalMax("1000000") private Double position;
    }
    @Data
    public static class Progress {
        private Long activityId;
        private int seconds;
        private int requiredSeconds;
        private int awarded;
        private boolean eligible;
        private boolean rewarded;
        private Integer balance;
    }
}
