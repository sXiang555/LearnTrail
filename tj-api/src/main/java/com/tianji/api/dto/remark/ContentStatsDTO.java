package com.tianji.api.dto.remark;

import lombok.Data;

/** Versioned aggregate snapshot; never a client-supplied counter. */
@Data
public class ContentStatsDTO {
    public static final String EXCHANGE = "like.record.topic";
    public static final String ACK_ROUTE = "content.stats.ack";
    private String bizType;
    private Long bizId;
    private Long likedTimes;
    private Long viewTimes;
    private Long version;

    public boolean valid(String type) {
        return type.equals(bizType) && bizId != null && bizId > 0 && version != null && version > 0
                && likedTimes != null && likedTimes >= 0 && viewTimes != null && viewTimes >= 0;
    }
}
