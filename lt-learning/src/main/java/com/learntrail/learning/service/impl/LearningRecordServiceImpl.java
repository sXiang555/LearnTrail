package com.learntrail.learning.service.impl;


import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.learntrail.api.client.course.CourseClient;
import com.learntrail.api.dto.course.CourseFullInfoDTO;
import com.learntrail.api.dto.course.SectionInfoDTO;
import com.learntrail.api.dto.leanring.LearningLessonDTO;
import com.learntrail.api.dto.leanring.LearningRecordDTO;
import com.learntrail.api.dto.leanring.LearningRecordFormDTO;
import com.learntrail.common.autoconfigure.mq.RabbitMqHelper;
import com.learntrail.common.constants.MqConstants;
import com.learntrail.common.exceptions.DbException;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.exceptions.ForbiddenException;
import com.learntrail.common.utils.BeanUtils;
import com.learntrail.common.utils.UserContext;
import com.learntrail.learning.domain.po.LearningLesson;
import com.learntrail.learning.domain.po.LearningRecord;
import com.learntrail.learning.enums.LessonStatus;
import com.learntrail.learning.enums.SectionType;
import com.learntrail.learning.mapper.LearningRecordMapper;
import com.learntrail.learning.service.ILearningLessonService;
import com.learntrail.learning.service.ILearningRecordService;
import com.learntrail.learning.utils.LearningRecordDelayTaskHandler;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import java.time.LocalDateTime;
import java.util.List;

/**
 * <p>
 * 学习记录表 服务实现类
 * </p>
 *
 * @author 虎哥
 * @since 2022-12-10
 */
@Service
@RequiredArgsConstructor
public class LearningRecordServiceImpl extends ServiceImpl<LearningRecordMapper, LearningRecord> implements ILearningRecordService {
    private final ILearningLessonService lessonService;
    private final CourseClient courseClient;
    private final LearningRecordDelayTaskHandler taskHandler;
    private final RabbitMqHelper mqHelper;

    @Override
    public LearningLessonDTO queryLearningRecordByCourse(Long courseId) {
        //获取登陆用户
        Long userId = UserContext.getUser();
        LearningLesson learningLesson = lessonService.queryByUserAndCourseId(userId, courseId);
        if (learningLesson == null || learningLesson.getStatus() == LessonStatus.EXPIRED ||
                (learningLesson.getExpireTime() != null && !learningLesson.getExpireTime().isAfter(LocalDateTime.now())))
            return null;
        //查询学习记录
        List<LearningRecord> records = lambdaQuery()
                .eq(LearningRecord::getLessonId, learningLesson.getId())
                .list();
        LearningLessonDTO learningLessonDTO = new LearningLessonDTO();
        learningLessonDTO.setLatestSectionId(learningLesson.getLatestSectionId());
        learningLessonDTO.setId(learningLesson.getId());
        learningLessonDTO.setRecords(BeanUtils.copyList(records, LearningRecordDTO.class));
        return learningLessonDTO;
    }

    @Override
    public void addLearningRecord(LearningRecordFormDTO dto) {
        validateAccess(dto);
        //判断该小节类型，如果为考试（考试只能参加一次），则添加学习记录，并设置学习进度（课表）
        // 如果为考试
        boolean isFinished;
        if (!dto.getSectionType().equals(SectionType.VIDEO.getValue())) {
            isFinished = handleExamRecord(dto);

        } else {
            //如果为视频
            isFinished = handleVideoRecord(dto);
        }

        if (!isFinished)
            //如果未完成，则不更新课表
            return;
        //更新课表
        updateLesson(dto);
        //第一次完成可以获得积分
        mqHelper.send(
                MqConstants.Exchange.LEARNING_EXCHANGE,
                MqConstants.Key.LEARN_SECTION,
                UserContext.getUser()
        );
    }

    private void validateAccess(LearningRecordFormDTO dto) {
        if (dto.getLessonId() == null || dto.getSectionId() == null || dto.getSectionType() == null ||
                (dto.getSectionType() != 1 && dto.getSectionType() != 2) || dto.getCommitTime() == null) {
            throw new BadRequestException("学习记录参数不完整");
        }
        LearningLesson lesson = lessonService.getById(dto.getLessonId());
        if (lesson == null || !lesson.getUserId().equals(UserContext.getUser()) ||
                lesson.getStatus() == LessonStatus.EXPIRED || (lesson.getExpireTime() != null &&
                !lesson.getExpireTime().isAfter(LocalDateTime.now()))) {
            throw new ForbiddenException("当前账号没有有效的专栏权益");
        }
        if (dto.getSectionType() == 1) {
            SectionInfoDTO section = courseClient.sectionInfo(dto.getSectionId());
            if (section == null || !lesson.getCourseId().equals(section.getCourseId())) {
                throw new ForbiddenException("该小节不属于当前专栏");
            }
            if (section.getMediaId() == null || section.getMediaId() <= 0 ||
                    dto.getDuration() == null || dto.getDuration() <= 0 || dto.getMoment() == null ||
                    dto.getMoment() < 0 || dto.getMoment() > dto.getDuration()) {
                throw new BadRequestException("视频观看进度不合法");
            }
        }
    }

    private boolean handleVideoRecord(LearningRecordFormDTO dto) {
        LearningRecord learningRecord = queryOldRecord(dto);
        // 判断该小节是否是第一次完成
        boolean isFinished = false;
        // 如果用户没有学习过该小节
        if (learningRecord == null) {
            //学习记录的提交是在观看视频的时候多次提交，所以如果没有该学习记录的话，用户就是才刚开始看该学习视频，因此小节肯定还没完成
            learningRecord = BeanUtils.copyBean(dto, LearningRecord.class);
            learningRecord.setUserId(UserContext.getUser());
            // 保存学习记录
            boolean save = save(learningRecord);
            if (!save) throw new DbException("保存学习记录失败");
            return isFinished;
        }
        // 用户学习过该小节，判断是否是第一次完成
        isFinished = learningRecord.getFinished() == false && dto.getMoment() > dto.getDuration() / 2;
        //如果不是第一次完成，缓存到redis
        if (!isFinished) {
            LearningRecord record = new LearningRecord();
            record.setMoment(dto.getMoment());
            record.setFinished(learningRecord.getFinished());
            record.setLessonId(dto.getLessonId());
            record.setSectionId(dto.getSectionId());
            record.setId(learningRecord.getId());
            taskHandler.addLearningRecordTask(record);
            return isFinished;
        }
        //是第一次完成,更新记录
        boolean update = lambdaUpdate()
                .eq(LearningRecord::getLessonId, dto.getLessonId())
                .eq(LearningRecord::getSectionId, dto.getSectionId())
                .set(LearningRecord::getMoment, dto.getMoment())
                .set(LearningRecord::getFinished, true)
                .set(LearningRecord::getFinishTime, dto.getCommitTime())
                .update();
        if (!update) throw new DbException("更新学习记录失败");
        taskHandler.cleanRecordCache(dto.getLessonId(), dto.getSectionId());
        return true;
    }

    private LearningRecord queryOldRecord(LearningRecordFormDTO dto) {
        // 如果为视频，判断用户是否已经学习过该小节
        LearningRecord learningRecord = taskHandler.readRecordCache(dto.getLessonId(), dto.getSectionId());
        if (learningRecord == null) {
            //缓存中没有，查询数据库
            learningRecord = getOne(buildLessonIdAndSectionIdWrapper(dto.getLessonId(), dto.getSectionId()));
        }
        if (learningRecord != null)
            taskHandler.writeRecordCache(learningRecord);
        return learningRecord;
    }

    private boolean handleExamRecord(LearningRecordFormDTO dto) {
        LearningRecord record = BeanUtils.copyBean(dto, LearningRecord.class);
        record.setUserId(UserContext.getUser());
        record.setFinished(true);
        record.setFinishTime(dto.getCommitTime());
        boolean save = save(record);
        if (!save) throw new DbException("保存学习记录失败");
        return true;
    }

    private void updateLesson(LearningRecordFormDTO dto) {
        LearningLesson ls = lessonService.getById(dto.getLessonId());
        if (ls == null) {
            throw new DbException("课表不存在");
        }
        //课表数据存在，若该小节已完成，需查询课程信息，判断是否课程全部完成
        boolean allLearned;

        CourseFullInfoDTO courseInfo = courseClient.getCourseInfoById(ls.getCourseId(), false, false);
        if (courseInfo == null) throw new DbException("课程不存在");
        allLearned = ls.getLearnedSections() + 1 >= courseInfo.getSectionNum();

        boolean update = lessonService.lambdaUpdate()
                .eq(LearningLesson::getId, dto.getLessonId())
                .set(ls.getLearnedSections() == 0, LearningLesson::getStatus, LessonStatus.LEARNING.getValue())
                .set(LearningLesson::getLatestSectionId, dto.getSectionId())
                .set(LearningLesson::getLatestLearnTime, dto.getCommitTime())
                .set(allLearned, LearningLesson::getStatus, LessonStatus.FINISHED.getValue())
                //用setSql，来保证并发安全
                .setSql("learned_sections = learned_sections + 1")
                .update();
        if (!update) {
            throw new DbException("更新课表失败");
        }

    }


    private LambdaQueryWrapper<LearningRecord> buildLessonIdAndSectionIdWrapper(Long lessonId, Long sectionId) {

        return new QueryWrapper<LearningRecord>()
                .lambda()
                .eq(LearningRecord::getLessonId, lessonId)
                .eq(LearningRecord::getSectionId, sectionId);
    }
}
