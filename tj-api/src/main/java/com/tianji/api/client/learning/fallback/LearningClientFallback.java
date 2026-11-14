package com.tianji.api.client.learning.fallback;

import com.tianji.api.client.learning.LearningClient;
import com.tianji.api.dto.leanring.LearningLessonDTO;
import com.tianji.api.dto.learning.StudioArticleLinkDTO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.cloud.openfeign.FallbackFactory;
import java.util.Collections;
import java.util.List;
import java.util.Map;

@Slf4j
public class LearningClientFallback implements FallbackFactory<LearningClient> {

    @Override
    public LearningClient create(Throwable cause) {
        log.error("查询学习服务异常", cause);
        return new LearningClient() {
            @Override
            public List<StudioArticleLinkDTO> studioArticleLinks(List<Long> ids, boolean owned) { return null; }

            @Override
            public Map<Long, Long> studioPublishedArticleCounts() {
                // 调用方据此拒绝生成不完整榜单，不将故障当作零篇文章。
                return null;
            }

            @Override
            public List<Long> studioOwnedCourseIds() { return Collections.emptyList(); }

            @Override
            public Integer countLearningLessonByCourse(Long courseId) {
                return 0;
            }

            @Override
            public Long isLessonValid(Long courseId) {
                return null;
            }

            @Override
            public LearningLessonDTO queryLearningRecordByCourse(Long courseId) {
                return null;
            }
        };
    }
}
