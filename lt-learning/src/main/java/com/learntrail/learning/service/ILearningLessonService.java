package com.learntrail.learning.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.common.domain.query.PageQuery;
import com.learntrail.learning.domain.po.LearningLesson;
import com.learntrail.learning.domain.vo.LearningLessonVO;
import com.learntrail.learning.domain.vo.LearningPlanPageVO;
import org.hibernate.validator.constraints.Range;

import javax.validation.constraints.Min;
import javax.validation.constraints.NotNull;
import java.util.List;

/**
 * <p>
 * 学生课程表 服务类
 * </p>
 *
 * @author 虎哥
 * @since 2022-12-02
 */
public interface ILearningLessonService extends IService<LearningLesson> {

    /**
     * 添加用户课程
     * @param userId 用户id
     * @param courseIds 课程id
     */
    void addUserLessons(Long userId, List<Long> courseIds);

    /**
     * 查询用户课表
     * @param query 分页查询条件
     * @return 课程信息
     */
    PageDTO<LearningLessonVO> queryMyLessons(PageQuery query);

    /**
     * 查询用户当前课程
     * @return 课程信息
     */
    LearningLessonVO queryMyCurrentLesson();

    /**
     * 删除用户课程
     * @param userId 用户id
     * @param courseIds 课程id
     */
    void removeUserLessons(Long userId, List<Long> courseIds);

    /**
     * 查询课程是否有效
     * @param courseId 课程id
     * @return 课程id
     */
    Long isLessonValid(Long courseId);

    /**
     * 查询用户课表中指定课程的状态
     * @param courseId 课程id
     * @return 课程信息
     */
    LearningLessonVO getUserLesson(Long courseId);

    /**
     * 查询课程学习人数
     * @param courseId 课程id
     * @return 学习人数
     */
    Integer countLearningLessonByCourse(Long courseId);


    /**
     * 根据用户和课程id查询学习课程
     * @param userId 用户id
     * @param courseId 课程id
     * @return 学习课程
     */
    LearningLesson queryByUserAndCourseId(Long userId, Long courseId);

    /**
     * 创建学习计划
     * @param courseId 课程id
     * @param freq 频率
     */
    void createLearningPlan(@NotNull @Min(1) Long courseId, @NotNull @Range(min = 1, max = 50) Integer freq);

    /**
     * 查询我的学习计划
     * @param query 分页查询条件
     * @return 学习计划
     */
    LearningPlanPageVO queryMyPlans(PageQuery query);
}
