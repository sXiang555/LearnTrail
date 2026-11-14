package com.learntrail.learning.service;

import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.learntrail.api.client.course.CourseClient;
import com.learntrail.api.dto.course.CatalogueDTO;
import com.learntrail.api.dto.course.CourseFullInfoDTO;
import com.learntrail.api.dto.course.SectionInfoDTO;
import com.learntrail.api.dto.points.PointsTrade;
import com.learntrail.common.exceptions.*;
import com.learntrail.common.utils.UserContext;
import com.learntrail.learning.domain.po.LearningActivity;
import com.learntrail.learning.domain.vo.StudioPointsView;
import com.learntrail.learning.mapper.LearningActivityMapper;
import com.learntrail.learning.mapper.StudioPointsMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.*;
import java.util.List;

@Service
@RequiredArgsConstructor
public class LearningActivityService {
    private final LearningActivityMapper mapper;
    private final StudioPointsMapper points;
    private final StudioPointsService wallet;
    private final CourseClient courses;
    private final ISignRecordService signs;
    private Clock clock = Clock.system(ZoneId.of("Asia/Shanghai"));

    @Transactional
    public LearningActivity.Progress start(String kind, Long target) {
        Long user = user();
        Policy policy = policy(kind, target, user);
        LearningActivity activity = new LearningActivity();
        activity.setId(IdWorker.getId()); activity.setUserId(user); activity.setKind(kind); activity.setTargetId(target);
        activity.setLastSeen(LocalDateTime.now(clock)); activity.setCreditedSeconds(0); activity.setLastPosition(0.0);
        mapper.insert(activity);
        return progress(activity, policy, 0);
    }

    @Transactional
    public LearningActivity.Progress heartbeat(Long id, double position) {
        Long user = user();
        if (!Double.isFinite(position) || position < 0) throw new BadRequestException("学习进度无效");
        LearningActivity activity = mapper.lock(id, user);
        if (activity == null) throw new BadRequestException("学习记录不存在");
        Policy policy = policy(activity.getKind(), activity.getTargetId(), user);
        LocalDateTime now = LocalDateTime.now(clock);
        long elapsed = Duration.between(activity.getLastSeen(), now).getSeconds();
        if (elapsed < 5) return progress(activity, policy, 0);
        int credit = elapsed <= 25 ? (int) Math.min(15, elapsed) : 0;
        if ("READ".equals(activity.getKind())) {
            if (position > 1) throw new BadRequestException("阅读进度无效");
        } else {
            double moved = position - activity.getLastPosition();
            if (position > policy.duration + 2 || moved <= 0 || moved > elapsed * 2 + 3) credit = 0;
            credit = Math.min(credit, Math.max(0, (int) Math.ceil(moved)));
        }
        activity.setCreditedSeconds(Math.min(7200, activity.getCreditedSeconds() + credit));
        activity.setLastSeen(now); activity.setLastPosition(position); mapper.update(activity);
        boolean eligible = activity.getCreditedSeconds() >= policy.seconds
                && (!"READ".equals(activity.getKind()) || position >= .8);
        int awarded = 0;
        if (eligible) {
            if ("READ".equals(activity.getKind())) awarded = wallet.read(policy.article.getSlug()).getAwarded();
            else awarded = wallet.award("WATCH", activity.getTargetId(), policy.title);
        }
        return progress(activity, policy, awarded);
    }

    public StudioPointsView.Result sign() {
        user();
        try { signs.addSignRecords(); }
        catch (BizIllegalException e) {
            List<Integer> records = signs.querySignRecords();
            if (records == null || records.isEmpty() || records.get(records.size() - 1) != 1) throw e;
        }
        long date = Long.parseLong(LocalDate.now(clock).toString().replace("-", ""));
        int awarded = wallet.award("SIGN", date, "每日签到");
        StudioPointsView.Result result = new StudioPointsView.Result();
        result.setAwarded(awarded); result.setBalance(wallet.wallet().getBalance()); return result;
    }

    private LearningActivity.Progress progress(LearningActivity activity, Policy policy, int awarded) {
        LearningActivity.Progress result = new LearningActivity.Progress();
        result.setActivityId(activity.getId()); result.setSeconds(activity.getCreditedSeconds());
        result.setRequiredSeconds(policy.seconds); result.setAwarded(awarded);
        result.setEligible(activity.getCreditedSeconds() >= policy.seconds
                && (!"READ".equals(activity.getKind()) || activity.getLastPosition() >= .8));
        result.setRewarded(points.hasEntry(activity.getUserId(), activity.getKind(), activity.getTargetId()) > 0);
        if (awarded > 0) result.setBalance(wallet.wallet().getBalance());
        return result;
    }
    private Policy policy(String kind, Long target, Long user) {
        if (!"READ".equals(kind) && !"WATCH".equals(kind)) throw new BadRequestException("学习类型无效");
        PointsTrade.Rule rule = wallet.rule(kind);
        Policy policy = new Policy(); policy.seconds = rule.getMinSeconds();
        if ("READ".equals(kind)) {
            // 从 ID 查询仍走已发布文章约束；不从客户端接受正文或奖励数值。
            StudioPointsView.Article article = points.articleById(user, target);
            if (article == null || "link".equals(article.getSourceType())) throw new BadRequestException("该文章不参与站内阅读奖励");
            policy.article = article; policy.title = article.getTitle(); return policy;
        }
        SectionInfoDTO section = courses.sectionInfo(target);
        if (section == null || section.getMediaId() == null || section.getMediaId() <= 0
                || !Boolean.TRUE.equals(courses.studioCanDiscuss(section.getCourseId()))) {
            throw new ForbiddenException("请先取得视频完整观看权限");
        }
        CourseFullInfoDTO course = courses.getCourseInfoById(section.getCourseId(), true, false);
        CatalogueDTO episode = course == null ? null : find(course.getChapters(), target);
        if (episode == null || episode.getMediaDuration() == null || episode.getMediaDuration() <= 0) {
            throw new BadRequestException("视频时长暂不可用");
        }
        policy.duration = episode.getMediaDuration(); policy.title = episode.getName();
        policy.seconds = Math.max(1, Math.min(rule.getMinSeconds(), (int) Math.ceil(policy.duration * .8)));
        return policy;
    }
    private CatalogueDTO find(List<CatalogueDTO> rows, Long id) {
        if (rows == null) return null;
        for (CatalogueDTO row : rows) {
            if (id.equals(row.getId())) return row;
            CatalogueDTO found = find(row.getSections(), id); if (found != null) return found;
        }
        return null;
    }
    private Long user() {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录");
        return user;
    }
    private static class Policy {
        int seconds; int duration; String title; StudioPointsView.Article article;
    }
}
