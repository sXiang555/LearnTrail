package com.learntrail.api.dto.points;

import lombok.Data;
import javax.validation.constraints.*;
import java.time.LocalDateTime;
import java.util.List;

/** 积分交易契约；不复用以人民币分为单位的金额字段。 */
public final class PointsTrade {
    private PointsTrade() { }
    @Data
    public static class Create {
        @NotBlank @Pattern(regexp = "[A-Za-z0-9_-]{8,80}") private String requestKey;
        @NotBlank @Pattern(regexp = "COURSE|COUPON") private String kind;
        @NotNull @Positive private Long targetId;
        @Positive private Long userCouponId;
    }
    @Data
    public static class Order {
        private Long orderId;
        private Long userId;
        private String requestKey;
        private String kind;
        private Long targetId;
        private String title;
        private String coverUrl;
        private String slug;
        private Integer totalPoints;
        private Integer discountPoints;
        private Integer payablePoints;
        private Long userCouponId;
        private String phase;
        private LocalDateTime createdAt;
        private LocalDateTime updatedAt;
        public String getUnit() { return "POINTS"; }
    }
    @Data
    public static class Receipt {
        private Long orderId;
        private Long userId;
        private String kind;
        private Long targetId;
        private Integer amount;
        private String status;
    }
    @Data
    public static class Reservation {
        private Long orderId;
        private Long userId;
        private Long userCouponId;
        private Integer discountPoints;
        private String status;
    }
    @Data
    public static class Quote {
        @NotNull @Positive private Long userCouponId;
        @NotNull @Positive private Long courseId;
        @NotNull @Min(1) @Max(100000) private Integer points;
    }
    @Data
    public static class Coupon {
        private Long id;
        private Long templateId;
        private String name;
        private Integer discountType;
        private Integer discountValue;
        private Integer thresholdAmount;
        private Integer maxDiscountAmount;
        private Integer exchangePoints;
        private Integer totalNum;
        private Integer issueNum;
        private Integer userLimit;
        private Integer status;
        private Integer termDays;
        private LocalDateTime termBeginTime;
        private LocalDateTime termEndTime;
        private LocalDateTime issueBeginTime;
        private LocalDateTime issueEndTime;
        private boolean reserved;
        private boolean specific;
        public String getUnit() { return "POINTS"; }
        public boolean isStackable() { return false; }
    }
    @Data
    public static class CouponEdit {
        @NotBlank @Size(max = 50) private String name;
        @NotNull @Min(2) @Max(4) private Integer discountType;
        @NotNull @Min(1) @Max(100000) private Integer discountValue;
        @NotNull @Min(0) @Max(100000) private Integer thresholdPoints;
        @NotNull @Min(0) @Max(100000) private Integer maxDiscountPoints;
        @NotNull @Min(0) @Max(100000) private Integer exchangePoints;
        @NotNull @Min(1) @Max(5000) private Integer totalNum;
        @NotNull @Min(1) @Max(10) private Integer userLimit;
        @NotNull @Min(1) @Max(365) private Integer termDays;
        @Size(max = 50) private List<@NotNull @Positive Long> courseIds;
    }
    @Data
    public static class Claim {
        private Long orderId;
        private Long userId;
        private Long couponId;
        private Long userCouponId;
        private String claimKey;
        private String status;
    }
    @Data
    public static class Rule {
        private String kind;
        private Integer amount;
        private Integer dailyCap;
        private Integer minSeconds;
    }
}
