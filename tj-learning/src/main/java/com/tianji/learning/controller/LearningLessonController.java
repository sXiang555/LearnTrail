package com.tianji.learning.controller;



import com.tianji.common.domain.dto.PageDTO;
import com.tianji.common.domain.query.PageQuery;
import com.tianji.common.utils.CollUtils;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.domain.dto.LearningPlanDTO;

import com.tianji.learning.domain.vo.LearningLessonVO;
import com.tianji.learning.domain.vo.LearningPlanPageVO;
import com.tianji.learning.service.ILearningLessonService;
import io.swagger.annotations.Api;
import io.swagger.annotations.ApiOperation;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import javax.validation.Valid;

/**
 * <p>
 * 学生课程表 前端控制器
 * </p>
 *
 * @author 虎哥
 * @since 2022-12-02
 */
@RestController
@RequestMapping("/lessons")
@Api(tags = "我的课表相关接口")
@RequiredArgsConstructor
public class LearningLessonController {

    private final ILearningLessonService lessonService;



    @ApiOperation("分页查询我的课表，排序字段：latest_learn_time:最近学习时间排序，create_time:购买时间排序")
    @RequestMapping("/page")
    public PageDTO<LearningLessonVO> queryMyLessons(PageQuery query) {
        return lessonService.queryMyLessons(query);
    }

    @ApiOperation("查询当前课程信息")
    @GetMapping("/now")
    public LearningLessonVO queryMyCurrentLesson() {
        return lessonService.queryMyCurrentLesson();
    }

    @ApiOperation("删除课程")
    @DeleteMapping("/{courseId}")
    public void removeUserLesson(@PathVariable("courseId") Long courseId) {
        Long userId = UserContext.getUser();
        lessonService.removeUserLessons(userId, CollUtils.singletonList(courseId));
    }


    @ApiOperation("查询课程是否有效")
    @GetMapping("/{courseId}/valid")
    public Long isLessonValid(@PathVariable("courseId") Long courseId){
        return lessonService.isLessonValid(courseId);
    }

    @ApiOperation("查询用户课表中指定课程的状态")
    @GetMapping("/{courseId}")
    public LearningLessonVO getUserLesson(@PathVariable("courseId") Long courseId){
        return lessonService.getUserLesson(courseId);
    }


    @ApiOperation("查询课程学习人数")
    @GetMapping("/{courseId}/count")
    Integer countLearningLessonByCourse(@PathVariable("courseId") Long courseId){
        return lessonService.countLearningLessonByCourse(courseId);
    }

    @ApiOperation("创建学习计划")
    @PostMapping("/plans")
    public void createLearningPlans(@Valid @RequestBody LearningPlanDTO planDTO) {
        lessonService.createLearningPlan(planDTO.getCourseId(), planDTO.getFreq());

    }

    @ApiOperation("查询我的学习计划")
    @GetMapping("/plans")
    public LearningPlanPageVO queryMyPlans(PageQuery query){
        return lessonService.queryMyPlans(query);
    }
}
