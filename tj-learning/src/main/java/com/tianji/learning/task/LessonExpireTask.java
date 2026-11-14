package com.tianji.learning.task;


import com.tianji.common.exceptions.DbException;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.domain.po.LearningLesson;
import com.tianji.learning.enums.LessonStatus;
import com.tianji.learning.service.ILearningLessonService;
import lombok.RequiredArgsConstructor;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.List;

@Component
@RequiredArgsConstructor
public class LessonExpireTask {
    private final ILearningLessonService learningService;

    @Scheduled(cron = "0 0 2 * * ?")
    public void lessonExpireTask(){
        boolean update = learningService.lambdaUpdate()
                .ne(LearningLesson::getStatus, LessonStatus.EXPIRED)
                .lt(LearningLesson::getExpireTime, LocalDateTime.now())
                .set(LearningLesson::getStatus, LessonStatus.EXPIRED)
                .update();
        if (!update) throw new DbException("课程过期任务执行失败");

    }
}
