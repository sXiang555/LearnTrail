package com.tianji.course.service;

import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.tianji.api.client.learning.LearningClient;
import com.tianji.api.client.media.StudioMediaClient;
import com.tianji.api.client.user.UserClient;
import com.tianji.api.dto.leanring.LearningLessonDTO;
import com.tianji.api.dto.leanring.LearningRecordDTO;
import com.tianji.api.dto.user.UserDTO;
import com.tianji.api.dto.learning.StudioArticleLinkDTO;
import com.tianji.common.domain.dto.PageDTO;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.DbException;
import com.tianji.common.exceptions.ForbiddenException;
import com.tianji.common.exceptions.UnauthorizedException;
import com.tianji.common.enums.UserType;
import com.tianji.common.utils.JsonUtils;
import com.tianji.common.utils.UserContext;
import com.tianji.course.constants.CourseConstants.CataType;
import com.tianji.course.constants.CourseConstants.CourseStep;
import com.tianji.course.domain.dto.CourseBaseInfoSaveDTO;
import com.tianji.course.domain.po.Category;
import com.tianji.course.domain.po.CourseCatalogueDraft;
import com.tianji.course.domain.po.CourseCatalogue;
import com.tianji.course.domain.vo.StudioView;
import com.tianji.course.domain.query.StudioQuery;
import com.tianji.course.mapper.StudioMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class StudioService {
    private static final int DEFAULT_UNLOCK_POINTS = 100;
    private static final String DEFAULT_COVER = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 9'%3E%3Cpath fill='%23214336' d='M0 0h16v9H0z'/%3E%3Cpath fill='%23c4dd8c' d='M7 2v5l4-2.5z'/%3E%3C/svg%3E";

    private final StudioMapper mapper;
    private final LearningClient learningClient;
    private final StudioMediaClient mediaClient;
    private final ICategoryService categoryService;
    private final ICourseDraftService courseDraftService;
    private final ICourseCatalogueDraftService catalogueDraftService;
    private final ICourseCatalogueService catalogueService;
    private final UserClient userClient;

    public Map<String, Object> home() {
        List<StudioView.Series> series = page(new StudioQuery()).getList();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("series", series);
        result.put("creators", creatorsPage(null, 1, 8).getList());
        result.put("topics", mapper.topics());
        return result;
    }

    public List<StudioView.Series> list(String q, String topic, String creator, boolean saved) {
        Long userId = saved ? requireUser() : UserContext.getUser();
        List<StudioView.Series> list = mapper.series(q, topic, creator, saved, userId, false);
        list.forEach(this::access);
        return list;
    }

    public PageDTO<StudioView.Series> page(StudioQuery query) {
        Long user = "discover".equals(query.getScope()) ? UserContext.getUser() : requireUser();
        if ("operations".equals(query.getScope()) && !Integer.valueOf(UserType.STAFF.getValue()).equals(userClient.queryUserType(user))) {
            throw new ForbiddenException("仅运营人员可管理平台内容");
        }
        List<Long> ids = "owned".equals(query.getScope()) ? learningClient.studioOwnedCourseIds() : Collections.emptyList();
        long total = mapper.countPage(query, user, ids);
        List<StudioView.Series> list = mapper.seriesPage(query, user, ids);
        if (!"operations".equals(query.getScope())) list.forEach(this::access);
        return new PageDTO<>(total, (total + query.getPageSize() - 1) / query.getPageSize(), list);
    }

    public PageDTO<StudioView.Creator> creatorsPage(String q, int pageNo, int pageSize) {
        Map<Long, Long> counts = articleCounts();
        long total = mapper.countCreators(q, UserContext.getUser(), counts);
        int offset = (pageNo - 1) * pageSize;
        List<StudioView.Creator> creators = mapper.creatorsPage(q, UserContext.getUser(), offset, pageSize, counts);
        if (q == null || q.isBlank()) {
            for (int i = 0; i < creators.size(); i++) creators.get(i).setRank(offset + i + 1);
        }
        return new PageDTO<>(total, (total + pageSize - 1) / pageSize, creators);
    }

    private Map<Long, Long> articleCounts() {
        Map<Long, Long> counts = learningClient.studioPublishedArticleCounts();
        if (counts == null) throw new BadRequestException("知识内容统计暂不可用，请稍后重试");
        return counts;
    }

    public StudioView.Series detail(String slug) {
        StudioView.Series series = mapper.find(null, slug, UserContext.getUser());
        if (series == null) throw new BadRequestException("专栏不存在或尚未发布");
        if (series.isBlocked()) throw new ForbiddenException("此专栏已被运营屏蔽，暂时无法访问");
        series.setEpisodes(mapper.episodes(series.getCourseId()));
        access(series);
        if (!series.isAvailable() && !series.isOwned() && !series.isCreatorOwned()) {
            throw new ForbiddenException("专栏暂未公开，仅已有权益的读者可以访问");
        }
        List<Long> articleIds = mapper.articleIds(series.getId(), "published");
        if (!articleIds.isEmpty()) series.setArticles(articleLinks(articleIds, false));
        if (!series.isAvailable()) series.getEpisodes().forEach(episode -> episode.setPreview(false));
        if (series.isCreatorOwned()) series.getEpisodes().forEach(episode -> episode.setPreview(true));
        if (series.isOwned()) {
            LearningLessonDTO lesson = learningClient.queryLearningRecordByCourse(series.getCourseId());
            if (lesson != null && lesson.getRecords() != null) {
                Map<Long, LearningRecordDTO> records = lesson.getRecords().stream()
                        .collect(Collectors.toMap(LearningRecordDTO::getSectionId, r -> r, (a, b) -> b));
                for (StudioView.Episode episode : series.getEpisodes()) {
                    LearningRecordDTO record = records.get(episode.getId());
                    if (record != null) {
                        episode.setFinished(Boolean.TRUE.equals(record.getFinished()));
                        episode.setMoment(record.getMoment());
                    }
                }
            }
        }
        return series;
    }

    public List<StudioView.Series> relatedSeries(Long article) {
        if (articleLinks(List.of(article), false).isEmpty()) return Collections.emptyList();
        return mapper.relatedSeries(article);
    }

    private List<StudioArticleLinkDTO> articleLinks(List<Long> ids, boolean owned) {
        List<StudioArticleLinkDTO> found = learningClient.studioArticleLinks(ids, owned);
        if (found == null) throw new BadRequestException("配套文章暂不可用，请稍后重试");
        return found;
    }

    private void saveArticles(Long id, List<Long> ids) {
        if (ids == null) return; // 兼容未传该字段的原有客户端。
        if (ids.size() > 20 || new HashSet<>(ids).size() != ids.size()
                || ids.stream().anyMatch(value -> value == null || value <= 0)) {
            throw new BadRequestException("请选择最多 20 篇不重复的本人文章");
        }
        if (!ids.isEmpty() && articleLinks(ids, true).size() != ids.size()) {
            throw new ForbiddenException("只能关联本人且未被屏蔽的文章");
        }
        mapper.clearArticles(id, "draft");
        for (int position = 0; position < ids.size(); position++) mapper.addArticle(id, ids.get(position), position);
    }

    public boolean canDiscuss(Long courseId) {
        Long user = requireUser();
        StudioView.Series policy = mapper.discussionPolicy(courseId, user);
        return policy != null && !policy.isBlocked() && (policy.isCreatorOwned() ||
                (policy.isAvailable() && Integer.valueOf(1).equals(policy.getFree())) ||
                learningClient.isLessonValid(courseId) != null);
    }

    public Map<String, Object> creator(String slug) {
        StudioView.Creator creator = mapper.creator(slug, UserContext.getUser());
        if (creator == null) throw new BadRequestException("创作者不存在");
        creator.setArticleCount(creator.getOwnerUserId() == null ? 0L
                : articleCounts().getOrDefault(creator.getOwnerUserId(), 0L));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("creator", creator);
        result.put("series", list(null, null, slug, false));
        return result;
    }

    public List<StudioView.Series> library() {
        Long user = requireUser();
        List<Long> ids = learningClient.studioOwnedCourseIds();
        if (ids == null || ids.isEmpty()) return Collections.emptyList();
        List<StudioView.Series> list = mapper.ownedSeries(user, ids);
        list.forEach(this::access);
        return list;
    }

    public Map<String, Object> studio() {
        Long userId = requireUser();
        StudioView.Creator creator = mapper.owner(userId);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("creator", creator);
        List<StudioView.Series> series = new ArrayList<>();
        if (creator != null) {
            series.addAll(mapper.workspaceSeries(userId));
            series.forEach(this::applyDraftMetadata);
        }
        result.put("series", series);
        return result;
    }

    public boolean studioAccess() {
        Long userId = UserContext.getUser();
        return userId != null && mapper.owner(userId) != null;
    }

    public PageDTO<StudioView.Follower> followers(int pageNo, int pageSize) {
        StudioView.Creator creator = requireOwner();
        int size = Math.max(1, Math.min(30, pageSize));
        long total = creator.getFollowerCount() == null ? 0L : creator.getFollowerCount().longValue();
        List<StudioView.Follower> list = mapper.followers(creator.getId(), (Math.max(1, pageNo) - 1) * size, size);
        if (!list.isEmpty()) {
            Map<Long, UserDTO> users = userClient.queryUserByIds(list.stream().map(StudioView.Follower::getUserId).collect(Collectors.toList()))
                    .stream().collect(Collectors.toMap(UserDTO::getId, user -> user, (first, other) -> first));
            for (StudioView.Follower follower : list) {
                UserDTO profile = users.get(follower.getUserId());
                follower.setName(profile == null ? "用户" : profile.getName());
                follower.setIcon(profile == null ? null : profile.getIcon());
            }
        }
        return new PageDTO<>(total, (total + size - 1) / size, list);
    }

    @Transactional
    public StudioView.Creator openStudio(StudioView.CreatorEdit profile) {
        Long userId = requireUser();
        String name = profile.getDisplayName().strip();
        String initials = name.substring(0, name.offsetByCodePoints(0, 1));
        mapper.insertCreator(IdWorker.getId(), userId, initials, profile);
        return mapper.owner(userId);
    }

    public List<Map<String, Object>> categories() {
        requireOwner();
        List<Category> active = categoryService.lambdaQuery().eq(Category::getStatus, 1)
                .orderByAsc(Category::getPriority, Category::getId).list();
        Map<Long, Category> byId = active.stream().collect(Collectors.toMap(Category::getId, category -> category));
        List<Map<String, Object>> result = new ArrayList<>();
        for (Category leaf : active) {
            if (leaf.getLevel() != 3) continue;
            Category parent = byId.get(leaf.getParentId());
            Category root = parent == null ? null : byId.get(parent.getParentId());
            if (root != null && root.getLevel() == 1 && parent.getLevel() == 2) {
                result.add(Map.of("id", leaf.getId(), "name",
                        root.getName() + " / " + parent.getName() + " / " + leaf.getName()));
            }
        }
        return result;
    }

    @Transactional
    public Long createDraft(StudioView.SeriesCreate edit) {
        StudioView.Creator owner = requireOwner();
        int unlockPoints = resolveUnlockPoints(edit, null);
        Long courseId = saveCourseDraft(null, edit, unlockPoints, edit.getPrice());
        Long id = IdWorker.getId();
        mapper.insertDraft(id, courseId, owner.getId(), edit, unlockPoints);
        saveArticles(id, edit.getArticleIds());
        return id;
    }

    @Transactional
    public void editDraft(Long id, StudioView.SeriesCreate edit) {
        Long userId = requireUser();
        StudioView.Series series = requireDraft(lockSeries(id, userId));
        int unlockPoints = resolveUnlockPoints(edit, series.getUnlockPoints());
        if (series.isPreviouslyPublished() && (unlockPoints != series.getUnlockPoints()
                || !Objects.equals(edit.getCategoryId(), series.getCategoryId()))) {
            throw new BadRequestException("已发布专栏不能修改解锁积分和分类，请新建专栏");
        }
        Integer price = edit.getPrice() == null ? series.getPrice() : edit.getPrice();
        edit.setUnlockPoints(unlockPoints);
        saveCourseDraft(series.getCourseId(), edit, unlockPoints, price);
        if (mapper.updateDraftPayload(id, userId, JsonUtils.toJsonStr(edit)) != 1) throw new DbException("草稿保存失败");
        saveArticles(id, edit.getArticleIds());
    }

    public Integer unlockCost(Long courseId) {
        return mapper.findUnlockPoints(courseId);
    }

    public boolean seriesLikeable(Long id) {
        if (id == null || id <= 0) throw new BadRequestException("专栏标识无效");
        StudioView.Series series = mapper.find(id, null, null);
        return series != null && series.isAvailable() && !series.isBlocked()
                && "published".equals(series.getStatus());
    }

    public List<Long> boundMediaIds(Long courseId) { return mapper.boundMediaIds(courseId, requireUser()); }

    @Transactional
    public void beginRevision(Long id) {
        Long user = requireUser();
        StudioView.Series series = lockSeries(id, user);
        if (series.isBlocked()) throw new ForbiddenException("运营屏蔽的专栏不能自行恢复");
        if ("draft".equals(series.getStatus())) {
            if (series.isPreviouslyPublished() && courseDraftService.getById(series.getCourseId()) == null) {
                courseDraftService.prepareCreatorRevision(series.getCourseId());
            }
            return;
        }
        if (!series.isPreviouslyPublished()) {
            if (!"archived".equals(series.getStatus())) throw new BadRequestException("该专栏没有可恢复的草稿");
            mapper.changeState(id, user, "draft");
            return;
        }
        courseDraftService.prepareCreatorRevision(series.getCourseId());
        mapper.clearArticles(id, "draft");
        mapper.copyArticles(id, "published", "draft");
        if (mapper.beginRevision(id, user, JsonUtils.toJsonStr(revisionForm(series))) != 1) throw new DbException("修订稿创建失败");
    }

    @Transactional
    public void changeState(Long id, String state) {
        Long user = requireUser();
        StudioView.Series series = lockSeries(id, user);
        if (series.isBlocked()) throw new ForbiddenException("运营屏蔽的专栏不能自行恢复");
        if (state.equals(series.getStatus())) return;
        boolean allowed = ("unlisted".equals(state) && "published".equals(series.getStatus()))
                || ("published".equals(state) && "unlisted".equals(series.getStatus()))
                || ("archived".equals(state) && "draft".equals(series.getStatus()) && !series.isPreviouslyPublished());
        if (!allowed) throw new BadRequestException("当前状态不支持此操作");
        if (mapper.changeState(id, user, state) != 1) throw new DbException("专栏状态更新失败");
    }

    @Transactional
    public void moderate(Long id, boolean blocked) {
        if (!Objects.equals(userClient.queryUserType(requireUser()), UserType.STAFF.getValue())) {
            throw new ForbiddenException("需要运营人员权限");
        }
        if (mapper.lockSeries(id, null) == null) throw new BadRequestException("专栏不存在");
        mapper.setBlocked(id, blocked);
    }

    @Transactional
    public Map<String, Object> draft(Long id) {
        StudioView.Series series = requireDraft(lockSeries(id, requireUser()));
        series.setArticleIds(mapper.articleIds(id, "draft"));
        return Map.of("series", series, "episodes", mapper.draftEpisodes(series.getCourseId()));
    }

    @Transactional
    public void saveEpisodes(Long id, StudioView.EpisodesEdit edit) {
        StudioView.Series series = requireDraft(lockSeries(id, requireUser()));
        Long courseId = series.getCourseId();
        Set<Long> ids = edit.getEpisodes().stream().map(StudioView.EpisodeEdit::getMediaId)
                .collect(Collectors.toSet());
        if (ids.size() != edit.getEpisodes().size()) {
            throw new BadRequestException("同一视频只能添加一次");
        }
        if (edit.getPreviewMediaId() != null && !ids.contains(edit.getPreviewMediaId())) {
            throw new BadRequestException("请选择当前专栏中的分集作为试看");
        }
        Map<Long, StudioMediaClient.Asset> assets = ownedAssets(ids, courseId);
        Map<Long, StudioView.DraftEpisode> previous = mapper.draftEpisodes(courseId).stream()
                .collect(Collectors.toMap(StudioView.DraftEpisode::getMediaId, row -> row, (first, other) -> first));
        Long existingChapter = mapper.draftChapter(courseId);
        catalogueDraftService.remove(Wrappers.<CourseCatalogueDraft>lambdaQuery()
                .eq(CourseCatalogueDraft::getCourseId, courseId)
                .eq(CourseCatalogueDraft::getType, CataType.SECTION).gt(CourseCatalogueDraft::getMediaId, 0));
        Long previewSectionId = null;
        if (!ids.isEmpty()) {
            List<CourseCatalogueDraft> rows = new ArrayList<>();
            Long chapterId = existingChapter == null ? IdWorker.getId() : existingChapter;
            if (existingChapter == null) rows.add(new CourseCatalogueDraft().setId(chapterId).setCourseId(courseId)
                    .setName("专栏视频").setType(CataType.CHAPTER).setCIndex(1).setDepId(0L));
            int position = 0;
            for (StudioView.EpisodeEdit item : edit.getEpisodes()) {
                StudioMediaClient.Asset asset = assets.get(item.getMediaId());
                StudioView.DraftEpisode old = previous.get(item.getMediaId());
                Long sectionId = old == null ? IdWorker.getId() : old.getId();
                if (item.getMediaId().equals(edit.getPreviewMediaId())) previewSectionId = sectionId;
                rows.add(new CourseCatalogueDraft().setId(sectionId).setCourseId(courseId)
                        .setName(item.getTitle().strip()).setType(CataType.SECTION).setDepId(0L)
                        .setParentCatalogueId(chapterId).setCIndex(++position).setTrailer(0)
                        .setMediaId(asset.getId()).setVideoName(asset.getFilename()).setMediaDuration(asset.getDuration()));
            }
            if (!catalogueDraftService.saveBatch(rows)) throw new DbException("分集保存失败，请重试");
        }
        courseDraftService.updateStep(courseId, CourseStep.CATALOGUE);
        mapper.updatePreview(id, previewSectionId, edit.getPreviewMinutes());
    }

    @Transactional
    public String publish(Long id) {
        StudioView.Series series = lockSeries(id, requireUser());
        if (series.isBlocked()) throw new ForbiddenException("运营屏蔽的专栏不能自行恢复");
        if ("published".equals(series.getStatus())) return series.getSlug();
        requireDraft(series);
        List<Long> related = mapper.articleIds(id, "draft");
        if (!related.isEmpty() && articleLinks(related, true).size() != related.size()) {
            throw new BadRequestException("部分关联文章已不可用，请重新选择");
        }
        Long courseId = series.getCourseId();
        validateCategory(series.getCategoryId());
        List<StudioView.DraftEpisode> episodes = mapper.draftEpisodes(courseId);
        if (episodes.isEmpty()) throw new BadRequestException("请先上传并保存至少一集视频");
        Map<Long, StudioMediaClient.Asset> assets = ownedAssets(episodes.stream()
                .map(StudioView.DraftEpisode::getMediaId).collect(Collectors.toSet()), courseId);
        if (assets.values().stream().anyMatch(asset -> !asset.isReady())) {
            throw new BadRequestException("部分视频仍在处理中，请稍后重试发布");
        }
        List<CourseCatalogueDraft> durations = episodes.stream().map(episode -> new CourseCatalogueDraft()
                .setId(episode.getId()).setMediaDuration(assets.get(episode.getMediaId()).getDuration()))
                .collect(Collectors.toList());
        if (!catalogueDraftService.updateBatchById(durations)) throw new DbException("视频时长更新失败");
        int total = durations.stream().mapToInt(CourseCatalogueDraft::getMediaDuration).sum();
        catalogueDraftService.lambdaUpdate().eq(CourseCatalogueDraft::getCourseId, courseId)
                .eq(CourseCatalogueDraft::getType, CataType.CHAPTER)
                .set(CourseCatalogueDraft::getMediaDuration, total).update();
        courseDraftService.updateStep(courseId, CourseStep.MEDIA);
        if (series.isPreviouslyPublished()) {
            List<Long> activeIds = catalogueDraftService.lambdaQuery().eq(CourseCatalogueDraft::getCourseId, courseId)
                    .select(CourseCatalogueDraft::getId).list().stream().map(CourseCatalogueDraft::getId).collect(Collectors.toList());
            catalogueService.lambdaUpdate().eq(CourseCatalogue::getCourseId, courseId)
                    .in(CourseCatalogue::getType, CataType.CHAPTER, CataType.SECTION)
                    .notIn(CourseCatalogue::getId, activeIds).set(CourseCatalogue::getDeleted, 1).update();
        }
        courseDraftService.publishCreatorDraft(courseId);
        if (mapper.publishDraft(id, revisionForm(series), series.getUnlockPoints()) != 1) throw new DbException("专栏发布失败，请重试");
        mapper.clearArticles(id, "published");
        mapper.copyArticles(id, "draft", "published");
        return series.getSlug();
    }

    private int resolveUnlockPoints(StudioView.SeriesCreate edit, Integer previous) {
        if (edit.getUnlockPoints() != null) return edit.getUnlockPoints();
        if (edit.getPrice() == null) throw new BadRequestException("请填写解锁积分");
        if (edit.getPrice() == 0) return 0;
        return previous != null && previous > 0 ? previous : DEFAULT_UNLOCK_POINTS;
    }

    private Long saveCourseDraft(Long courseId, StudioView.SeriesCreate edit, int unlockPoints, Integer price) {
        String title = edit.getTitle().strip();
        if (title.length() > 80) throw new BadRequestException("专栏标题不能超过 80 个字符");
        validateCategory(edit.getCategoryId());
        String description = edit.getDescription();
        int detailEnd = description.offsetByCodePoints(0, Math.min(1024, description.codePointCount(0, description.length())));
        CourseBaseInfoSaveDTO course = new CourseBaseInfoSaveDTO();
        course.setId(courseId);
        course.setName(title);
        course.setThirdCateId(edit.getCategoryId());
        course.setPrice(unlockPoints == 0 || price == null ? 0 : price);
        course.setFree(unlockPoints == 0);
        course.setCoverUrl(DEFAULT_COVER);
        course.setIntroduce(edit.getSubtitle());
        course.setDetail(description.substring(0, detailEnd));
        course.setUsePeople("对专栏主题感兴趣的观众");
        course.setValidDuration(0);
        course.setPurchaseEndTime(LocalDateTime.of(2099, 12, 31, 23, 59));
        return courseDraftService.save(course).getId();
    }

    private void validateCategory(Long id) {
        for (int level = 3; level >= 1; level--) {
            Category category = categoryService.getById(id);
            if (category == null || category.getLevel() != level || !Integer.valueOf(1).equals(category.getStatus())) {
                throw new BadRequestException("请选择有效的内容分类");
            }
            id = category.getParentId();
        }
    }

    private Map<Long, StudioMediaClient.Asset> ownedAssets(Set<Long> ids, Long courseId) {
        if (ids.isEmpty()) return Collections.emptyMap();
        Map<Long, StudioMediaClient.Asset> assets = mediaClient.ownedAssets(new ArrayList<>(ids), courseId).stream()
                .collect(Collectors.toMap(StudioMediaClient.Asset::getId, asset -> asset));
        if (!assets.keySet().containsAll(ids)) throw new ForbiddenException("只能添加本人上传或此专栏原已绑定的视频");
        return assets;
    }

    private StudioView.Creator requireOwner() {
        StudioView.Creator creator = mapper.owner(requireUser());
        if (creator == null) throw new ForbiddenException("请先开通创作者空间");
        return creator;
    }

    private StudioView.Series lockSeries(Long id, Long userId) {
        StudioView.Series series = mapper.lockSeries(id, userId);
        if (series == null) throw new ForbiddenException("只能管理自己的专栏");
        applyDraftMetadata(series);
        return series;
    }

    private StudioView.Series requireDraft(StudioView.Series series) {
        if (series.isBlocked() || !"draft".equals(series.getStatus()) || series.getCategoryId() == null) {
            throw new BadRequestException("该专栏当前无法编辑草稿");
        }
        return series;
    }

    @Transactional
    public void save(Long id, boolean saved) {
        Long userId = requireUser();
        if (!saved) { mapper.unsave(userId, id); return; }
        StudioView.Series series = mapper.find(id, null, userId);
        if (series == null || series.isBlocked()) throw new BadRequestException("专栏不可收藏");
        access(series);
        if (!series.isAvailable() && !series.isOwned() && !series.isCreatorOwned()) throw new ForbiddenException("专栏暂未公开");
        if (saved) mapper.save(userId, id); else mapper.unsave(userId, id);
    }

    @Transactional
    public void follow(String slug, boolean following) {
        Long userId = requireUser();
        StudioView.Creator creator = mapper.creator(slug, userId);
        if (creator == null) throw new BadRequestException("创作者不存在");
        if (following) mapper.follow(userId, creator.getId()); else mapper.unfollow(userId, creator.getId());
    }

    @Transactional
    public void editSeries(Long id, StudioView.SeriesEdit edit) {
        Long userId = requireUser();
        StudioView.Creator owner = mapper.owner(userId);
        StudioView.Series series = mapper.find(id, null, userId);
        if (owner == null || series == null || !owner.getId().equals(series.getCreatorId()))
            throw new ForbiddenException("只能编辑自己的专栏");
        if (series.isBlocked() || !series.isAvailable()) throw new BadRequestException("请在可编辑的草稿中保存修改");
        mapper.updateSeries(id, userId, edit, null);
    }

    @Transactional
    public void editCreator(StudioView.CreatorEdit edit) {
        Long userId = requireUser();
        if (mapper.owner(userId) == null) throw new ForbiddenException("当前账号没有创作者空间");
        mapper.updateCreator(userId, edit);
    }

    private void access(StudioView.Series series) {
        if (UserContext.getUser() == null || learningClient.isLessonValid(series.getCourseId()) == null) return;
        LearningLessonDTO lesson = learningClient.queryLearningRecordByCourse(series.getCourseId());
        if (lesson == null) return;
        series.setOwned(true);
        series.setLessonId(lesson.getId());
        series.setLatestSectionId(lesson.getLatestSectionId());
        Set<Long> current = mapper.episodes(series.getCourseId()).stream().map(StudioView.Episode::getId).collect(Collectors.toSet());
        long finished = lesson.getRecords() == null ? 0 : lesson.getRecords().stream()
                .filter(r -> current.contains(r.getSectionId()) && Boolean.TRUE.equals(r.getFinished()))
                .map(LearningRecordDTO::getSectionId).distinct().count();
        series.setProgressPercent(series.getEpisodeCount() == null || series.getEpisodeCount() == 0 ? 0 :
                Math.min(100, (int) (100 * finished / series.getEpisodeCount())));
    }

    private StudioView.SeriesCreate revisionForm(StudioView.Series series) {
        StudioView.SeriesCreate edit = new StudioView.SeriesCreate();
        edit.setTitle(series.getTitle()); edit.setSubtitle(series.getSubtitle());
        edit.setDescription(series.getDescription()); edit.setTopic(series.getTopic()); edit.setCoverTone(series.getCoverTone());
        edit.setPrice(series.getPrice()); edit.setCategoryId(series.getCategoryId()); edit.setUnlockPoints(series.getUnlockPoints());
        return edit;
    }

    private void applyDraftMetadata(StudioView.Series series) {
        if (!"draft".equals(series.getStatus()) || series.getDraftPayload() == null) return;
        StudioView.SeriesCreate edit = JsonUtils.toBean(series.getDraftPayload(), StudioView.SeriesCreate.class);
        series.setTitle(edit.getTitle()); series.setSubtitle(edit.getSubtitle()); series.setDescription(edit.getDescription());
        series.setTopic(edit.getTopic()); series.setCoverTone(edit.getCoverTone()); series.setUnlockPoints(edit.getUnlockPoints());
    }

    private Long requireUser() {
        Long userId = UserContext.getUser();
        if (userId == null) throw new UnauthorizedException("请先登录 LearnTrail");
        return userId;
    }
}
