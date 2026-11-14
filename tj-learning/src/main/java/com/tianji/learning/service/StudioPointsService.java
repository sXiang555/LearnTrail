package com.tianji.learning.service;

import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.tianji.api.client.course.CourseClient;
import com.tianji.api.dto.course.CourseFullInfoDTO;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.UnauthorizedException;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.domain.vo.StudioPointsView;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.learning.mapper.StudioPointsMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.List;
import java.time.LocalDate;
import java.time.ZoneId;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.learning.domain.po.InteractionReply;
import org.springframework.transaction.annotation.Propagation;

@Service
@RequiredArgsConstructor
public class StudioPointsService {
    public static final int INITIAL_POINTS = 1000;
    public static final int READING_REWARD = 20;
    private final StudioPointsMapper mapper;
    private final CourseClient courses;

    public List<Long> entitlements() { return mapper.entitlements(requireUser()); }

    public PageDTO<StudioPointsView.Entry> entries(int pageNo, int pageSize) {
        return entries(pageNo, pageSize, null, null);
    }

    public PageDTO<StudioPointsView.Entry> entries(int pageNo, int pageSize, String kind, String direction) {
        Long user = requireUser();
        long total = mapper.countFilteredEntries(user, kind, direction);
        return new PageDTO<>(total, (total + pageSize - 1) / pageSize,
                mapper.filteredEntries(user, kind, direction, (pageNo - 1) * pageSize, pageSize));
    }

    @Transactional
    public StudioPointsView.Wallet wallet() {
        Long user = requireUser();
        int balance = lockWallet(user);
        StudioPointsView.Wallet wallet = new StudioPointsView.Wallet();
        wallet.setBalance(balance);
        wallet.setInitialPoints(INITIAL_POINTS);
        wallet.setReadingReward(rule("READ").getAmount());
        wallet.setEntries(mapper.entries(user));
        return wallet;
    }

    public List<StudioPointsView.Article> articles() {
        return mapper.articles(requireUser());
    }

    public StudioPointsView.Article article(String slug) {
        return requireArticle(requireUser(), slug);
    }

    @Transactional
    public StudioPointsView.Result read(String slug) {
        Long user = requireUser();
        int balance = lockWallet(user);
        StudioPointsView.Article article = requireArticle(user, slug);
        if ("link".equals(article.getSourceType())) throw new BadRequestException("外链资料不发放站内全文阅读奖励");
        StudioPointsView.Result result = new StudioPointsView.Result();
        if (mapper.hasEntry(user, "READ", article.getId()) == 0) {
            PointsTrade.Rule rule = rule("READ");
            if (mapper.effectiveRead(user, article.getId(), rule.getMinSeconds()) == 0) {
                throw new BadRequestException("请有效阅读至少 " + rule.getMinSeconds() + " 秒并阅读至正文末段");
            }
            int awarded = awardLocked(user, "READ", article.getId(), article.getTitle());
            balance += awarded;
            result.setAwarded(awarded);
        }
        result.setBalance(balance);
        return result;
    }

    /** 扣分、流水与真实学习权益在同一个 tj_learning 本地事务内提交。 */
    @Transactional
    public StudioPointsView.Result unlock(Long courseId) {
        Long user = requireUser();
        Integer cost = courses.studioUnlockCost(courseId);
        if (cost == null || cost <= 0 || cost > 100000) {
            throw new BadRequestException("专栏不存在、尚未发布或无需积分解锁");
        }
        CourseFullInfoDTO course = courses.getCourseInfoById(courseId, false, false);
        if (course == null) throw new BadRequestException("专栏信息暂不可用");
        int balance = lockWallet(user);
        StudioPointsView.Result result = new StudioPointsView.Result();
        Long lessonId = mapper.validLesson(user, courseId);
        if (lessonId == null) {
            if (mapper.hasEntry(user, "UNLOCK", courseId) == 0) {
                if (balance < cost) throw new BadRequestException("积分不足，请先阅读文章获取积分");
                changeBalance(user, -cost);
                mapper.addEntry(user, "UNLOCK", courseId, course.getName(), -cost);
                balance -= cost;
                result.setSpent(cost);
            }
            // 已扣过积分的权益如果被移出课表，重新加入时不重复扣分。
            mapper.grantLesson(IdWorker.getId(), user, courseId);
            lessonId = mapper.validLesson(user, courseId);
            if (lessonId == null) throw new IllegalStateException("学习权益创建失败");
        }
        result.setLessonId(lessonId);
        result.setBalance(balance);
        return result;
    }

    public PointsTrade.Rule rule(String kind) {
        PointsTrade.Rule rule = mapper.rule(kind);
        if (rule == null || rule.getAmount() == null || rule.getAmount() < 1 || rule.getAmount() > 10000
                || rule.getDailyCap() == null || rule.getDailyCap() < 1 || rule.getDailyCap() > 100000
                || rule.getMinSeconds() == null || rule.getMinSeconds() < 0 || rule.getMinSeconds() > 3600) {
            throw new BadRequestException("积分规则暂不可用");
        }
        return rule;
    }

    @Transactional
    public int award(String kind, Long target, String title) {
        Long user = requireUser();
        lockWallet(user);
        return awardLocked(user, kind, target, title);
    }

    private int awardLocked(Long user, String kind, Long target, String title) {
        if (mapper.hasEntry(user, kind, target) != 0) return 0;
        PointsTrade.Rule rule = rule(kind);
        int remaining = rule.getDailyCap() - mapper.earnedToday(user, kind,
                LocalDate.now(ZoneId.of("Asia/Shanghai")).atStartOfDay());
        if (remaining < rule.getAmount()) return 0;
        mapper.addEntry(user, kind, target, title, rule.getAmount());
        changeBalance(user, rule.getAmount());
        return rule.getAmount();
    }

    @Transactional
    public void rewardComment(InteractionReply reply) {
        Long user = requireUser();
        if (!user.equals(reply.getUserId())) throw new UnauthorizedException("评论身份不一致");
        String content = reply.getContent().replaceAll("\\s+", "").toLowerCase(java.util.Locale.ROOT);
        // 过滤短句、纯标点及重复字符；相同正文跨问题也不重复奖励。
        if (content.codePointCount(0, content.length()) < 15
                || content.codePoints().filter(Character::isLetterOrDigit).distinct().count() < 8) return;
        lockWallet(user);
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256").digest(content.getBytes(StandardCharsets.UTF_8));
            StringBuilder fingerprint = new StringBuilder();
            for (byte value : bytes) fingerprint.append(String.format("%02x", value & 255));
            if (mapper.rememberComment(user, fingerprint.toString(), reply.getId()) == 1) {
                awardLocked(user, "COMMENT", reply.getId(), "有效评论奖励");
            }
        } catch (java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public int lockWallet(Long user) {
        mapper.createWallet(user, INITIAL_POINTS);
        int balance = mapper.lockBalance(user);
        if (mapper.hasEntry(user, "INITIAL", 0L) == 0) {
            mapper.addEntry(user, "INITIAL", 0L, "初始积分", INITIAL_POINTS);
        }
        return balance;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void changeBalance(Long user, int amount) {
        if (mapper.changeBalance(user, amount) != 1) throw new BadRequestException("积分变更失败，请刷新后重试");
    }

    private StudioPointsView.Article requireArticle(Long user, String slug) {
        StudioPointsView.Article article = mapper.article(user, slug);
        if (article == null) throw new BadRequestException("文章不存在");
        return article;
    }

    private Long requireUser() {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录 LearnTrail");
        return user;
    }
}
