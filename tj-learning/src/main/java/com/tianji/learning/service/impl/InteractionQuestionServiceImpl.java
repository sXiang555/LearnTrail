package com.tianji.learning.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.tianji.api.cache.CategoryCache;
import com.tianji.api.client.course.CatalogueClient;
import com.tianji.api.client.course.CategoryClient;
import com.tianji.api.client.course.CourseClient;
import com.tianji.api.client.search.SearchClient;
import com.tianji.api.client.user.UserClient;
import com.tianji.api.dto.course.CataSimpleInfoDTO;
import com.tianji.api.dto.course.CategoryBasicDTO;
import com.tianji.api.dto.course.CourseFullInfoDTO;
import com.tianji.api.dto.course.CourseSimpleInfoDTO;
import com.tianji.api.dto.user.UserDTO;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.common.exceptions.DbException;
import com.tianji.common.utils.BeanUtils;
import com.tianji.common.utils.CollUtils;
import com.tianji.common.utils.StringUtils;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.domain.dto.QuestionFormDTO;
import com.tianji.learning.domain.po.InteractionQuestion;
import com.tianji.learning.domain.po.InteractionReply;
import com.tianji.learning.domain.query.QuestionAdminPageQuery;
import com.tianji.learning.domain.query.QuestionPageQuery;
import com.tianji.learning.domain.vo.QuestionAdminVO;
import com.tianji.learning.domain.vo.QuestionVO;
import com.tianji.learning.enums.QuestionStatus;
import com.tianji.learning.mapper.InteractionQuestionMapper;
import com.tianji.learning.mapper.InteractionReplyMapper;
import com.tianji.learning.service.IInteractionQuestionService;
import com.tianji.learning.service.DiscussionAccess;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.ForbiddenException;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import lombok.RequiredArgsConstructor;
import org.apache.catalina.mbeans.UserMBean;
import org.checkerframework.checker.units.qual.C;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * <p>
 * 互动提问的问题表 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Service
@RequiredArgsConstructor
public class InteractionQuestionServiceImpl extends ServiceImpl<InteractionQuestionMapper, InteractionQuestion> implements IInteractionQuestionService {

    private final InteractionReplyMapper replyMapper;
    private final UserClient userClient;
    private final CourseClient courseClient;
    private final SearchClient searchClient;
    private final CatalogueClient catalogueClient;
    private final CategoryCache categoryCache;
    private final CategoryClient categoryClient;
    private final DiscussionAccess discussion;

    @Override
    public void saveQuestion(QuestionFormDTO questionDTO) {
        // 1.获取当前登录的用户id
        Long userId = discussion.user();
        validateQuestion(questionDTO);
        // 2.数据封装
        InteractionQuestion question = BeanUtils.copyBean(questionDTO, InteractionQuestion.class);
        question.setUserId(userId);
        question.setAnonymity(Boolean.TRUE.equals(questionDTO.getAnonymity()));
        question.setHidden(false);
        // 3.写入数据库
        if (!save(question)) throw new DbException("提问保存失败");

    }

    @Override
    public PageDTO<QuestionVO> queryQuestionPage(QuestionPageQuery query) {
        Long userId = discussion.user();
        discussion.page(query);
        // 1.参数校验，课程id和小节id不能都为空
        Long sectionId = query.getSectionId();
        Long courseId = query.getCourseId();
        if (courseId == null && sectionId == null) {
            throw new BadRequestException("课程id和小节id不能都为空");
        }
        if (sectionId != null) {
            com.tianji.api.dto.course.SectionInfoDTO section = courseClient.sectionInfo(sectionId);
            if (section == null || (courseId != null && !courseId.equals(section.getCourseId()))) throw new BadRequestException("小节不属于此专栏");
            courseId = section.getCourseId();
        }
        discussion.course(courseId);
        // 2.分页查询

        Page<InteractionQuestion> questionPage = lambdaQuery()
                .eq(InteractionQuestion::getHidden, false)
                .in(sectionId != null, InteractionQuestion::getSectionId, sectionId)
                .eq(Boolean.TRUE.equals(query.getOnlyMine()), InteractionQuestion::getUserId, userId)
                .eq(courseId != null, InteractionQuestion::getCourseId, courseId)
                .page(query.toMpPage("id", false));

        // 3.根据id查询提问者和最近一次回答的信息
        List<InteractionQuestion> records = questionPage.getRecords();
        if (records == null || records.isEmpty()){
            return PageDTO.empty(questionPage);
        }
        // 3.1.得到问题当中的提问者id和最近一次回答的id
        // 先得到提问者id集合，因为一个用户可能有多个问题，但只需要一个用户信息，因此用set去重
        Set<Long> userIds = new HashSet<>();
        // 再得到最近一次回答id集合
        Set<Long> replyIds = new HashSet<>();
        records.forEach(question -> {
            if (!Boolean.TRUE.equals(question.getAnonymity())) {
                // 只有非匿名提问才需要获取用户信息
                userIds.add(question.getUserId());
            }
            replyIds.add(question.getLatestAnswerId());
        });
        // 3.2.根据id查询最近一次回答
        replyIds.remove(null);
        Map<Long, InteractionReply> replyMap = new HashMap<>(replyIds.size());
        if (!CollUtils.isEmpty(replyIds)) {
            List<InteractionReply> replyList = replyMapper.selectBatchIds(replyIds);
            for (InteractionReply reply : replyList){
                if (Boolean.TRUE.equals(reply.getHidden())) continue;
                replyMap.put(reply.getId(), reply);
                if (!reply.getAnonymity()) {
                    // 只有非匿名回答才需要获取用户信息
                    userIds.add(reply.getUserId());
                }
            }

        }

        // 3.3.根据id查询用户信息（提问者）
        userIds.remove(null);
        Map<Long, UserDTO> userMap = new HashMap<>(userIds.size());
        if (!CollUtils.isEmpty(userIds)) {
            userMap = userClient.queryUserByIds(userIds)
                    .stream().collect(Collectors.toMap(UserDTO::getId, v -> v));
        }
        // 4.封装VO
        List<QuestionVO> questionVOs = new ArrayList<>(records.size());
        for (InteractionQuestion question : records) {
            QuestionVO questionVO = BeanUtils.copyBean(question, QuestionVO.class);
            questionVO.setUserId(null);
            questionVO.setMine(Objects.equals(question.getUserId(), userId));
            questionVOs.add(questionVO);

            if (!question.getAnonymity()) {
                // 只有非匿名提问才需要获取用户信息
                questionVO.setUserId(question.getUserId());
                UserDTO userDTO = userMap.get(question.getUserId());
                questionVO.setUserName(userDTO == null ? "用户" : userDTO.getName());
                questionVO.setUserIcon(userDTO == null ? null : userDTO.getIcon());
            }

            InteractionReply latestReply = replyMap.get(question.getLatestAnswerId());
            if (latestReply != null) {
                questionVO.setLatestReplyContent(latestReply.getContent());
                if (!latestReply.getAnonymity()){
                    UserDTO userDTO = userMap.get(latestReply.getUserId());
                    questionVO.setLatestReplyUser(userDTO == null ? "用户" : userDTO.getName());
                }
            }

        }
        return PageDTO.of(questionPage, questionVOs);
    }

    @Override
    public QuestionVO queryQuestionById(Long id) {
        Long user = discussion.user();
        // 1.根据id查询数据
        InteractionQuestion question = getById(id);
        // 2.数据校验
        if (question == null || Boolean.TRUE.equals(question.getHidden())) {
            throw new BadRequestException("问题不存在或已隐藏");
        }
        discussion.course(question.getCourseId());
        // 3.查询提问者信息
        UserDTO userDTO = null;
        if (!question.getAnonymity()) {
            // 只有非匿名提问才需要获取用户信息
            userDTO = userClient.queryUserById(question.getUserId());
        }
        // 4.封装VO
        QuestionVO questionVO = BeanUtils.copyBean(question, QuestionVO.class);
        questionVO.setUserId(null);
        questionVO.setMine(Objects.equals(user, question.getUserId()));
        if (userDTO != null) {
            questionVO.setUserId(question.getUserId());
            questionVO.setUserName(userDTO.getName());
            questionVO.setUserIcon(userDTO.getIcon());
        }
        return questionVO;
    }

    @Override
    public PageDTO<QuestionAdminVO> queryQuestionPageAdmin(QuestionAdminPageQuery query) {
        discussion.staff();
        discussion.page(query);
        // 1.处理课程名称，得到课程id
        String courseName = query.getCourseName();
        List<Long> ids = null;
        if (StringUtils.isNotBlank(courseName)) {
            ids = searchClient.queryCoursesIdByName(courseName);
            if (CollUtils.isEmpty(ids)) {
                return PageDTO.empty(0L,0L);
            }
        }
        // 2.分页查询
        Integer status = query.getStatus();
        LocalDateTime endTime = query.getEndTime();
        LocalDateTime beginTime = query.getBeginTime();
        Page<InteractionQuestion> questionPage = lambdaQuery()
                .in(ids != null, InteractionQuestion::getCourseId, ids)
                .eq(status != null,InteractionQuestion::getStatus, status)
                .lt(endTime != null,InteractionQuestion::getCreateTime, endTime)
                .gt(beginTime != null,InteractionQuestion::getCreateTime, beginTime)
                .page(query.toMpPage("id", true));
        // 3.准备VO需要的数据：用户数据、课程数据、章节数据
        List<InteractionQuestion> records = questionPage.getRecords();
        if (records == null || records.isEmpty()) {
            return PageDTO.empty(questionPage);
        }
        // 3.1.获取各种数据的id集合
        Set<Long> userIds = new HashSet<>();
        Set<Long> courseIds = new HashSet<>();
        Set<Long> chapterIds = new HashSet<>();
        Set<Long> sectionIds = new HashSet<>();
        records.forEach(question -> {
            userIds.add(question.getUserId());
            courseIds.add(question.getCourseId());
            chapterIds.add(question.getChapterId());
            sectionIds.add(question.getSectionId());
        });
        // 3.2.根据id查询用户
        userIds.remove(null);
        List<UserDTO> userDTOS = userClient.queryUserByIds(userIds);
        Map<Long, UserDTO> userMap = new HashMap<>(userDTOS.size());
        if (!CollUtils.isEmpty(userDTOS)) {
            userMap = userDTOS.stream().collect(Collectors.toMap(UserDTO::getId, v -> v));
        }
        // 3.3.根据id查询课程
        courseIds.remove(null);
        List<CourseSimpleInfoDTO> courseInfos = courseClient.getSimpleInfoList(courseIds);
        Map<Long, CourseSimpleInfoDTO> courseMap = new HashMap<>(courseInfos.size());
        if (!CollUtils.isEmpty(courseInfos)) {
            courseMap = courseInfos.stream().collect(Collectors.toMap(CourseSimpleInfoDTO::getId, v -> v));
        }
        List<CategoryBasicDTO> allOfOneLevel = categoryClient.getAllOfOneLevel();

        // 3.4.根据id查询章节
        chapterIds.remove(null);
        List<CataSimpleInfoDTO> chapterDTOS = catalogueClient.batchQueryCatalogue(chapterIds);
        Map<Long, CataSimpleInfoDTO> chapterMap = new HashMap<>(chapterDTOS.size());
        if (!CollUtils.isEmpty(chapterDTOS)) {
            chapterMap = chapterDTOS.stream().collect(Collectors.toMap(CataSimpleInfoDTO::getId, v -> v));
        }

        sectionIds.remove(null);
        List<CataSimpleInfoDTO> sectionDTOS = catalogueClient.batchQueryCatalogue(sectionIds);
        Map<Long, CataSimpleInfoDTO> sectionMap = new HashMap<>(sectionDTOS.size());
        if (!CollUtils.isEmpty(sectionDTOS)) {
            sectionMap = sectionDTOS.stream().collect(Collectors.toMap(CataSimpleInfoDTO::getId, v -> v));

        }
        // 4.封装VO
        List<QuestionAdminVO> questionAdminVOs = new ArrayList<>(records.size());
        for (InteractionQuestion question : records) {
            QuestionAdminVO vo = BeanUtils.copyBean(question, QuestionAdminVO.class);

            // 用户
            UserDTO user = userMap.get(question.getUserId());
            if (user != null) {
                vo.setUserName(user.getName());
            }

            // 课程
            CourseSimpleInfoDTO course = courseMap.get(question.getCourseId());
            if (course != null) {
                vo.setCourseName(course.getName());

                // 分类
                List<Long> cIds = course.getCategoryIds();
                String cateName = categoryCache.getCategoryNames(cIds);
                if (cateName != null) {
                    vo.setCategoryName(cateName);
                }
            }

            // 章节
            CataSimpleInfoDTO chapter = chapterMap.get(question.getChapterId());
            if (chapter != null) {
                vo.setChapterName(chapter.getName());
            }

            // 小节
            CataSimpleInfoDTO section = sectionMap.get(question.getSectionId());
            if (section != null) {
                vo.setSectionName(section.getName());
            }

            questionAdminVOs.add(vo);
        }
        return PageDTO.of(questionPage, questionAdminVOs);

    }

    @Override
    public QuestionAdminVO queryQuestionByIdAdmin(Long id) {
        discussion.staff();
        // 1.根据id查询问题
        InteractionQuestion question = getById(id);
        if (question == null) {
            throw new RuntimeException("问题不存在");
        }
        // 2.转PO为VO
        QuestionAdminVO vo = BeanUtils.copyBean(question, QuestionAdminVO.class);
        // 3.查询提问者信息
        UserDTO user = userClient.queryUserById(question.getUserId());

        // 4.查询课程信息
        CourseFullInfoDTO course = courseClient.getCourseInfoById(question.getCourseId(), false, false);

        // 5.查询章节信息
        List<Long> cataIds = Stream.of(question.getChapterId(), question.getSectionId())
                .filter(Objects::nonNull).collect(Collectors.toList());
        List<CataSimpleInfoDTO> catas = catalogueClient.batchQueryCatalogue(cataIds);

        Map<Long, CataSimpleInfoDTO> map = new HashMap<>();
        if (catas != null) {
            map = catas.stream()
                    .collect(Collectors.toMap(CataSimpleInfoDTO::getId, v -> v));
        }

        CataSimpleInfoDTO chapter = map.get(question.getChapterId());
        CataSimpleInfoDTO section = map.get(question.getSectionId());

        // 6.封装VO
        // 用户数据
        if (user != null) {
            vo.setUserName(user.getName());
            vo.setUserIcon(user.getIcon());
        }

        // 课程数据
        if (course != null){
            vo.setCourseName(course.getName());
            List<Long> categoryIds = course.getCategoryIds();

            //分类名称
            if (CollUtils.isNotEmpty(categoryIds)) {
                String cateName = categoryCache.getCategoryNames(categoryIds);
                if (cateName != null) {
                    vo.setCategoryName(cateName);
                }
            }
            // 教师
            List<Long> teacherIds = course.getTeacherIds();
            if (CollUtils.isNotEmpty(teacherIds)) {
                List<UserDTO> teachers = userClient.queryUserByIds(teacherIds);
                if (CollUtils.isNotEmpty(teachers)) {
                    vo.setTeacherName(teachers.get(0).getName());
                }
            }

        }

        // 章节数据
        if (chapter != null) {
            vo.setChapterName(chapter.getName());
        }
        // 小节数据
        if (section != null) {
            vo.setSectionName(section.getName());
        }

        //设置问题状态为已查看
        lambdaUpdate()
                .eq(InteractionQuestion::getId, id)
                .set(InteractionQuestion::getStatus, QuestionStatus.CHECKED.getValue())
                .update();
        vo.setStatus(QuestionStatus.CHECKED.getValue());
        return vo;
    }

    @Override
    public void hiddenQuestion(Long id, Boolean hidden) {
        discussion.staff();
        if (hidden == null || id == null) {
            throw new RuntimeException("参数错误");
        }
        // 1.更新问题
        boolean update = lambdaUpdate()
                .eq(InteractionQuestion::getId, id)
                .set(InteractionQuestion::getHidden, hidden)
                .update();
        if (!update) {
            throw new DbException("更新问题隐藏状态失败");
        }
    }

    @Override
    public void updateQuestion(Long id, QuestionFormDTO questionDTO) {
        // 1.获取当前登录用户
        Long userId = discussion.user();
        // 2.查询当前问题
        InteractionQuestion question = getById(id);
        // 3.判断是否是当前用户的问题
        if (question == null || !question.getUserId().equals(userId)) {
            throw new ForbiddenException("只能修改自己的问题");
        }
        if (Boolean.TRUE.equals(question.getHidden())) throw new BadRequestException("问题已隐藏");
        discussion.course(question.getCourseId());
        discussion.text(questionDTO.getTitle(), 254);
        discussion.text(questionDTO.getDescription(), 10000);
        // 4.修改问题
        if (!lambdaUpdate().eq(InteractionQuestion::getId, id).eq(InteractionQuestion::getUserId, userId)
                .eq(InteractionQuestion::getHidden, false).set(InteractionQuestion::getTitle, questionDTO.getTitle())
                .set(InteractionQuestion::getAnonymity, Boolean.TRUE.equals(questionDTO.getAnonymity()))
                .set(InteractionQuestion::getDescription, questionDTO.getDescription()).update()) throw new DbException("问题更新失败");
    }

    @Override
    @Transactional
    public void deleteById(Long id) {
        // 1.获取当前登录用户
        Long userId = discussion.user();
        // 2.查询当前问题
        InteractionQuestion question = getById(id);
        if (question == null) {
            throw new BadRequestException("问题不存在");
        }
        // 3.判断是否是当前用户的问题
        if (!question.getUserId().equals(userId)) {
            throw new ForbiddenException("只能删除自己的问题");
        }
        // 4.删除问题
        lambdaUpdate().eq(InteractionQuestion::getId, id).set(InteractionQuestion::getHidden, true).update();
        // 5.删除答案
        replyMapper.update(null, Wrappers.<InteractionReply>lambdaUpdate().eq(InteractionReply::getQuestionId, id).set(InteractionReply::getHidden, true));
    }

    private void validateQuestion(QuestionFormDTO form) {
        discussion.course(form.getCourseId());
        discussion.text(form.getTitle(), 254);
        discussion.text(form.getDescription(), 10000);
        if (form.getSectionId() != null) {
            com.tianji.api.dto.course.SectionInfoDTO section = courseClient.sectionInfo(form.getSectionId());
            if (section == null || !Objects.equals(form.getCourseId(), section.getCourseId())) throw new BadRequestException("小节不属于此专栏");
        }
    }
}
