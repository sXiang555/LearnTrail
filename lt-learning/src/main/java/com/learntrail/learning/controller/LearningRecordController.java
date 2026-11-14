package com.learntrail.learning.controller;


import com.learntrail.api.dto.leanring.LearningLessonDTO;
import com.learntrail.api.dto.leanring.LearningRecordFormDTO;
import com.learntrail.learning.service.ILearningRecordService;
import io.swagger.annotations.Api;
import io.swagger.annotations.ApiOperation;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * <p>
 * 学习记录表 前端控制器
 * </p>
 *
 * @author 虎哥
 * @since 2022-12-10
 */
@RestController
@RequestMapping("/learning-records")
@Api(tags = "学习记录的相关接口")
@RequiredArgsConstructor
public class LearningRecordController {

    private final ILearningRecordService recordService;

    /**
     * 查询当前用户指定课程的学习进度
     * @param courseId 课程id
     * @return 课表信息、学习记录及进度信息
     */
    @GetMapping("/course/{courseId}")
    @ApiOperation("查询当前用户指定课程的学习进度")
    public LearningLessonDTO queryLearningRecordByCourse(@PathVariable("courseId") Long courseId){
        return recordService.queryLearningRecordByCourse(courseId);
    }


    @PostMapping
    @ApiOperation("提交学习进度")
    public void addLearningRecord(@RequestBody LearningRecordFormDTO dto){
        recordService.addLearningRecord(dto);
    }

}
