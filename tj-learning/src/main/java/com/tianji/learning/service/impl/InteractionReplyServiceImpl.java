package com.tianji.learning.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.tianji.api.client.remark.RemarkClient;
import com.tianji.api.client.user.UserClient;
import com.tianji.api.dto.user.UserDTO;
import com.tianji.common.autoconfigure.mq.RabbitMqHelper;
import com.tianji.common.constants.MqConstants;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.common.utils.BeanUtils;
import com.tianji.common.utils.CollUtils;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.domain.dto.ReplyDTO;
import com.tianji.learning.domain.po.InteractionQuestion;
import com.tianji.learning.domain.po.InteractionReply;
import com.tianji.learning.domain.query.ReplyPageQuery;
import com.tianji.learning.domain.vo.ReplyVO;
import com.tianji.learning.enums.QuestionStatus;
import com.tianji.learning.mapper.InteractionReplyMapper;
import com.tianji.learning.service.IInteractionQuestionService;
import com.tianji.learning.service.IInteractionReplyService;
import com.tianji.learning.service.DiscussionAccess;
import com.tianji.learning.service.StudioPointsService;
import com.tianji.common.exceptions.BadRequestException;
import java.util.Objects;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * <p>
 * 互动问题的回答或评论 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Service
@RequiredArgsConstructor
public class InteractionReplyServiceImpl extends ServiceImpl<InteractionReplyMapper, InteractionReply> implements IInteractionReplyService {

    private final IInteractionQuestionService questionService;
    private final UserClient userClient;
    private final RemarkClient remarkClient;
    private final RabbitMqHelper mqHelper;
    private final DiscussionAccess discussion;
    private final StudioPointsService pointsWallet;

    @Override
    @Transactional
    public void saveReply(ReplyDTO replyDTO) {
        // 1.获取登录用户
        Long userId = discussion.user();
        discussion.text(replyDTO.getContent(), 10000);
        visibleQuestion(replyDTO.getQuestionId(), true, false);
        if (replyDTO.getAnswerId() != null) {
            InteractionReply parent = getById(replyDTO.getAnswerId());
            if (parent == null || parent.getAnswerId() != null || Boolean.TRUE.equals(parent.getHidden()) ||
                    !Objects.equals(parent.getQuestionId(), replyDTO.getQuestionId())) throw new BadRequestException("上级回答不属于此问题");
            Long targetId = replyDTO.getTargetReplyId() == null ? parent.getId() : replyDTO.getTargetReplyId();
            InteractionReply target = getById(targetId);
            if (target == null || Boolean.TRUE.equals(target.getHidden()) || !Objects.equals(target.getQuestionId(), parent.getQuestionId()) ||
                    !(Objects.equals(target.getId(), parent.getId()) || Objects.equals(target.getAnswerId(), parent.getId()))) {
                throw new BadRequestException("回复目标不属于此回答");
            }
            replyDTO.setTargetReplyId(target.getId()); replyDTO.setTargetUserId(target.getUserId());
        } else {
            replyDTO.setTargetReplyId(null); replyDTO.setTargetUserId(null);
        }
        replyDTO.setIsStudent(discussion.student());
        // 2.新增回答
        InteractionReply reply = BeanUtils.copyBean(replyDTO, InteractionReply.class);
        reply.setUserId(userId);
        reply.setHidden(false);
        reply.setAnonymity(Boolean.TRUE.equals(replyDTO.getAnonymity()));
        save(reply);
        pointsWallet.rewardComment(reply);
        // 3.累加评论数或者累加回答数
        // 3.1.判断当前回复的类型是否是回答
            if (replyDTO.getAnswerId() != null) {
                // 3.2.是评论，则需要更新上级回答的评论数量
                if (replyDTO.getTargetReplyId() != null
                        && !replyDTO.getTargetReplyId().equals(replyDTO.getAnswerId())){
                    // 3.2.1.更新上级评论的评论数量
                    lambdaUpdate()
                            .eq(InteractionReply::getId, replyDTO.getTargetReplyId())
                            .setSql("reply_times = reply_times + 1")
                            .update();
                }
                lambdaUpdate()
                        .eq(InteractionReply::getId, replyDTO.getAnswerId())
                        .setSql("reply_times = reply_times + 1")
                        .update();
            }
        // 3.3.尝试更新问题表中的状态、 最近一次回答、回答数量
            if (replyDTO.getAnswerId() == null) {
                //回复的是回答
                // 3.3.1.更新问题表中的最近一次回答、回答数量
                questionService.lambdaUpdate()
                        .eq(InteractionQuestion::getId, replyDTO.getQuestionId())
                        .setSql("answer_times = answer_times + 1")
                        .set(InteractionQuestion::getLatestAnswerId,reply.getId())
                        .update();
            }

        // 4.尝试累加积分
            // 学生才需要累加积分
            if (replyDTO.getIsStudent()){
                mqHelper.send(
                        MqConstants.Exchange.LEARNING_EXCHANGE,
                        MqConstants.Key.WRITE_REPLY,
                        userId
                );
               //是学生，则修改问题表状态
                questionService.lambdaUpdate()
                        .eq(InteractionQuestion::getId, replyDTO.getQuestionId())
                        .set(InteractionQuestion::getStatus, QuestionStatus.UN_CHECK.getValue())
                        .update();
            }
    }

    @Override
    public PageDTO<ReplyVO> queryReplyPage(ReplyPageQuery query, boolean forAdmin) {
        discussion.user();
        discussion.page(query);
        if (forAdmin) discussion.staff();
        // 1.问题id和回答id至少要有一个，先做参数判断
        Long questionId = query.getQuestionId();
        if (questionId == null && query.getAnswerId() == null) {
            throw new BadRequestException("问题id和回答id至少要有一个");
        }
        if (query.getAnswerId() != null) {
            InteractionReply parent = getById(query.getAnswerId());
            if (parent == null || parent.getAnswerId() != null || (!forAdmin && Boolean.TRUE.equals(parent.getHidden())) ||
                    (questionId != null && !questionId.equals(parent.getQuestionId()))) throw new BadRequestException("回答不存在或不属于此问题");
            questionId = parent.getQuestionId();
        }
        InteractionQuestion question = visibleQuestion(questionId, false, forAdmin);
        // 标记当前是查询问题下的回答
        boolean isQueryAnswer = query.getAnswerId() ==  null;
        // 2.分页查询reply
        Page<InteractionReply> page = lambdaQuery()
                .eq(!forAdmin, InteractionReply::getHidden, false)
                .isNull(isQueryAnswer, InteractionReply::getAnswerId)
                .eq(questionId != null, InteractionReply::getQuestionId, questionId)
                .eq(!isQueryAnswer, InteractionReply::getAnswerId, query.getAnswerId())
                .page(query.toMpPageDefaultSortByCreateTimeDesc());
        List<InteractionReply> records = page.getRecords();
        if (CollUtils.isEmpty(records)){
            return PageDTO.empty(page);
        }
        // 3.数据处理，需要查询：提问者信息、回复目标信息、当前用户是否点赞
        // 3.1.获取提问者id 、回复的目标id、当前回答或评论id（统计点赞信息）
        Long questionUserId = question.getUserId();
        Set<Long> targetReplyIds = records.stream().map(InteractionReply::getTargetReplyId).collect(Collectors.toSet());
        Set<Long> replyIds = records.stream().map(InteractionReply::getId).collect(Collectors.toSet());
        // 3.2.查询目标回复，如果目标回复不是匿名，则需要查询出目标回复的用户信息
        targetReplyIds.remove(null);
        Map<Long, InteractionReply> targetReplyMap =  new HashMap<>();
        if (!CollUtils.isEmpty(targetReplyIds)) {
            List<InteractionReply> targetReplies = listByIds(targetReplyIds);
            targetReplyMap = targetReplies.stream().collect(Collectors.toMap(InteractionReply::getId, v -> v));

        }
        // 3.3.查询用户
        Set<Long> userIds = records.stream().map(InteractionReply::getUserId).collect(Collectors.toSet());
        userIds.add(questionUserId);
        for (InteractionReply record : records) {
            userIds.add(record.getTargetUserId());
        }
        userIds.remove(null);
        Map<Long, UserDTO> userMap = new HashMap<>(userIds.size());
        if (!CollUtils.isEmpty(userIds)) {
            userMap = userClient.queryUserByIds(userIds)
                    .stream().collect(Collectors.toMap(UserDTO::getId, v -> v));
        }
        // 3.4.查询用户点赞状态
        Set<Long> bizLiked = remarkClient.isBizLiked(replyIds);
        if (bizLiked == null) bizLiked = java.util.Collections.emptySet();
        // 4.处理VO
            // 4.1.拷贝基础属性
        List<ReplyVO> replyVOs = BeanUtils.copyList(records, ReplyVO.class);
            // 4.2.回复人信息
        for (ReplyVO replyVO : replyVOs) {
            if (!replyVO.getAnonymity()) {
                UserDTO author = userMap.get(replyVO.getUserId());
                replyVO.setUserName(author == null ? "用户" : author.getName());
                replyVO.setUserIcon(author == null ? null : author.getIcon());
            } else if (!forAdmin) {
                replyVO.setUserId(null);
            }
        }
            // 4.3.如果存在评论的目标，则需要设置目标用户信息
        for (int i = 0; i < replyVOs.size(); i++) {
            ReplyVO vo = replyVOs.get(i);
            InteractionReply r = records.get(i);

            //  核心判断
            if (r.getTargetReplyId() != null) {
                InteractionReply targetReply = targetReplyMap.get(r.getTargetReplyId());
                if (targetReply != null && !targetReply.getAnonymity()) {
                    UserDTO targetUser = userMap.get(targetReply.getUserId());
                    if (targetUser != null) {
                        vo.setTargetUserName(targetUser.getName());
                    }
                }
            }
        }
            // 4.4.点赞状态
        for (ReplyVO replyVO : replyVOs) {
            replyVO.setLiked(bizLiked.contains(replyVO.getId()));
        }
        return PageDTO.of(page, replyVOs);
    }

    @Override
    @Transactional
    public void hiddenReply(Long id, Boolean hidden) {
        discussion.staff();
        // 1.查询
        InteractionReply reply = getById(id);
        if (reply == null) {
            return;
        }

        // 2.隐藏回答
        reply.setHidden(hidden);
        updateById(reply);

        // 3.隐藏评论，先判断是否是回答，回答才需要隐藏下属评论
        // 3.2.没有answerId，说明自己是回答，需要隐藏回答下的评论
        if (reply.getAnswerId() == null) {
            lambdaUpdate()
                    .eq(InteractionReply::getAnswerId, reply.getId())
                    .set(InteractionReply::getHidden, hidden)
                    .update();
        }
    }

    @Override
    public ReplyVO queryReplyById(Long id) {
        discussion.staff();
        // 1.根据id查询
        InteractionReply reply = getById(id);
        if (reply == null) throw new BadRequestException("回复不存在");
        // 2.数据处理，需要查询用户信息、评论目标信息、当前用户是否点赞
        // 2.1.获取用户 id
        Long userId = reply.getUserId();
        // 2.2.查询评论目标，如果评论目标不是匿名，则需要查询出目标回复的用户id
        Long targetUserId = reply.getTargetUserId();
        // 2.3.查询用户详细
        UserDTO user = userClient.queryUserById(userId);
        // 2.4.查询用户点赞状态
        Set<Long> bizLiked = remarkClient.isBizLiked(List.of(id));
        // 4.处理VO
        // 4.1.拷贝基础属性
        ReplyVO replyVO = BeanUtils.copyBean(reply, ReplyVO.class);
        // 4.2.回复人信息
        replyVO.setUserName(user == null ? "用户" : user.getName());
        replyVO.setUserIcon(user == null ? null : user.getIcon());
        // 4.3.目标用户
        if (targetUserId != null) {
            UserDTO targetUser = userClient.queryUserById(targetUserId);
            replyVO.setTargetUserName(targetUser == null ? "用户" : targetUser.getName());
        }
        // 4.4.点赞状态
        replyVO.setLiked(bizLiked != null && bizLiked.contains(id));
        return replyVO;
    }

    private InteractionQuestion visibleQuestion(Long id, boolean lock, boolean forAdmin) {
        if (id == null) throw new BadRequestException("问题id不能为空");
        InteractionQuestion question = lock ? questionService.lambdaQuery().eq(InteractionQuestion::getId, id).last("FOR UPDATE").one()
                : questionService.getById(id);
        if (question == null || (!forAdmin && Boolean.TRUE.equals(question.getHidden()))) throw new BadRequestException("问题不存在或已隐藏");
        if (!forAdmin) discussion.course(question.getCourseId());
        return question;
    }
}
