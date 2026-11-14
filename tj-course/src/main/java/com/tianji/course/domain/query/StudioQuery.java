package com.tianji.course.domain.query;

import lombok.Data;
import javax.validation.constraints.*;

@Data
public class StudioQuery {
    @Min(1) @Max(100000)
    private int pageNo = 1;
    @Min(1) @Max(30)
    private int pageSize = 12;
    @Pattern(regexp = "discover|saved|owned|operations")
    private String scope = "discover";
    @Pattern(regexp = "recommended|price|duration|newest|likes|views")
    private String sort = "recommended";
    @Size(max = 80)
    private String q;
    @Size(max = 40)
    private String topic;
    @Size(max = 80)
    private String creator;
    public int getOffset() { return (pageNo - 1) * pageSize; }
}
