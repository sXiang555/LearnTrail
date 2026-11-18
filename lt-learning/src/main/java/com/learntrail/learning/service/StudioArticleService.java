package com.learntrail.learning.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.learntrail.api.client.course.CourseClient;
import com.learntrail.api.client.user.UserClient;
import com.learntrail.api.dto.user.UserDTO;
import com.learntrail.api.dto.learning.StudioArticleLinkDTO;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.common.autoconfigure.redisson.PublicContentCache;
import com.learntrail.common.enums.UserType;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.exceptions.ForbiddenException;
import com.learntrail.common.exceptions.UnauthorizedException;
import com.learntrail.common.utils.UserContext;
import com.learntrail.learning.domain.po.StudioArticle;
import com.learntrail.learning.mapper.StudioArticleMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;
import java.net.URI;
import java.net.URISyntaxException;
import java.util.*;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class StudioArticleService {
    private static final Set<String> STATES = Set.of("draft", "published", "archived", "blocked");
    private final StudioArticleMapper mapper;
    private final UserClient users;
    private final CourseClient courses;
    @Autowired(required = false)
    private PublicContentCache publicCache;

    public List<StudioArticleLinkDTO> links(List<Long> ids, boolean owned) {
        if (ids == null || ids.isEmpty() || ids.size() > 20 || ids.stream().anyMatch(id -> id == null || id <= 0)) {
            throw new BadRequestException("请选择最多 20 篇文章");
        }
        LambdaQueryWrapper<StudioArticle> query = Wrappers.<StudioArticle>lambdaQuery()
                .in(StudioArticle::getId, ids);
        if (owned) query.eq(StudioArticle::getAuthorUserId, requireUser()).ne(StudioArticle::getStatus, "blocked");
        else query.eq(StudioArticle::getStatus, "published");
        Map<Long, StudioArticle> found = mapper.selectList(query).stream()
                .collect(Collectors.toMap(StudioArticle::getId, item -> item));
        List<StudioArticleLinkDTO> result = new ArrayList<>();
        for (Long id : new LinkedHashSet<>(ids)) {
            StudioArticle article = found.get(id);
            if (article == null) continue;
            StudioArticleLinkDTO link = new StudioArticleLinkDTO();
            link.setId(id); link.setSlug(article.getSlug()); link.setTitle(article.getTitle());
            link.setSummary(article.getSummary()); result.add(link);
        }
        return result;
    }

    public Map<String, Boolean> access() {
        Long user = requireUser();
        return Map.of("operator", isOperator(user), "creator", Boolean.TRUE.equals(courses.studioAccess()));
    }

    public PageDTO<StudioArticle> page(String scope, String q, String status, int pageNo, int pageSize) {
        return page(scope, q, status, pageNo, pageSize, null);
    }

    public PageDTO<StudioArticle> page(String scope, String q, String status, int pageNo, int pageSize, Long author) {
        return page(scope, q, status, pageNo, pageSize, author, "newest");
    }

    public PageDTO<StudioArticle> page(String scope, String q, String status, int pageNo, int pageSize, Long author, String sort) {
        if (!Set.of("newest", "likes", "views").contains(sort)) throw new BadRequestException("文章排序无效");
        Long user = "published".equals(scope) ? UserContext.getUser() : requireUser();
        LambdaQueryWrapper<StudioArticle> query = Wrappers.<StudioArticle>lambdaQuery().select(
                StudioArticle::getId, StudioArticle::getSlug, StudioArticle::getTitle, StudioArticle::getSummary,
                StudioArticle::getAuthorUserId, StudioArticle::getStatus, StudioArticle::getCreatedAt,
                StudioArticle::getUpdatedAt, StudioArticle::getPublishedAt, StudioArticle::getContentFormat,
                StudioArticle::getSourceType, StudioArticle::getSourceAuthor, StudioArticle::getSourceUrl,
                StudioArticle::getLicenseNote, StudioArticle::getLikedTimes, StudioArticle::getViewTimes);
        if ("published".equals(scope)) query.eq(StudioArticle::getStatus, "published");
        else if ("mine".equals(scope)) query.eq(StudioArticle::getAuthorUserId, user);
        else if ("admin".equals(scope)) requireOperator(user);
        else throw new BadRequestException("文章查询范围无效");
        if (author != null) query.eq(StudioArticle::getAuthorUserId, author);
        if (!"published".equals(scope) && status != null && !status.isBlank()) {
            if (!STATES.contains(status)) throw new BadRequestException("文章状态无效");
            query.eq(StudioArticle::getStatus, status);
        }
        if (q != null && !q.isBlank()) query.and(part -> part.like(StudioArticle::getTitle, q.strip())
                .or().like(StudioArticle::getSummary, q.strip()));
        if ("published".equals(scope)) {
            if ("likes".equals(sort)) query.orderByDesc(StudioArticle::getLikedTimes);
            if ("views".equals(sort)) query.orderByDesc(StudioArticle::getViewTimes);
            query.orderByDesc(StudioArticle::getPublishedAt, StudioArticle::getId);
        }
        else query.orderByDesc(StudioArticle::getUpdatedAt, StudioArticle::getId);
        Page<StudioArticle> page = mapper.selectPage(new Page<>(Math.max(1, pageNo), Math.max(1, Math.min(30, pageSize))), query);
        enrich(page.getRecords(), user);
        return PageDTO.of(page);
    }

    public StudioArticle published(String slug) {
        Long user = UserContext.getUser();
        StudioArticle article = publicCache == null ? readPublished(slug) : readCachedPublished(slug);
        if (article == null) throw new BadRequestException("文章不存在或尚未发布");
        enrich(List.of(article), user);
        return article;
    }

    private StudioArticle readPublished(String slug) {
        return mapper.selectOne(Wrappers.<StudioArticle>lambdaQuery()
                .eq(StudioArticle::getSlug, slug).eq(StudioArticle::getStatus, "published"));
    }

    private StudioArticle readCachedPublished(String slug) {
        String key = articleCacheKey(slug);
        if (publicCache.knownMissing(key)) return null;
        StudioArticle article = mapper.publishedMetadata(slug);
        if (article == null) {
            publicCache.markMissing(key);
            return null;
        }
        Long revision = article.getPublicRevision();
        if (revision == null) return readPublished(slug);
        String body = publicCache.load(key + ":" + revision, String.class,
                () -> mapper.publishedBody(article.getId(), revision));
        if (body == null) return readPublished(slug);
        article.setBody(body);
        return article;
    }

    private String articleCacheKey(String slug) { return "article:" + slug.toLowerCase(Locale.ROOT); }

    private void invalidate(StudioArticle article, Long revision) {
        if (publicCache != null) {
            String key = articleCacheKey(article.getSlug());
            publicCache.evictAfterCommit(key, revision == null ? null : key + ":" + revision);
        }
    }

    public Map<Long, Long> publishedCounts() {
        return mapper.publishedCounts().stream().collect(Collectors.toMap(
                StudioArticle.AuthorCount::getAuthorUserId, StudioArticle.AuthorCount::getArticleCount));
    }

    public StudioArticle editable(Long id) {
        StudioArticle article = mapper.selectById(id);
        checkEditable(article, requireUser());
        return article;
    }

    @Transactional
    public Long create(StudioArticle.Edit edit) {
        Long user = requireUser();
        if (!isOperator(user) && !Boolean.TRUE.equals(courses.studioAccess())) {
            throw new ForbiddenException("请先开通创作者空间");
        }
        StudioArticle article = new StudioArticle();
        article.setId(IdWorker.getId());
        article.setSlug("article-" + article.getId());
        article.setAuthorUserId(user);
        article.setStatus("draft");
        apply(article, edit);
        if (mapper.insert(article) != 1) throw new IllegalStateException("文章创建失败");
        return article.getId();
    }

    @Transactional
    public void edit(Long id, StudioArticle.Edit edit) {
        StudioArticle article = mapper.lock(id);
        checkEditable(article, requireUser());
        Long revision = publicCache == null ? null : mapper.publicRevision(id);
        apply(article, edit);
        article.setUpdatedAt(LocalDateTime.now());
        if (mapper.updateById(article) != 1) throw new IllegalStateException("文章保存失败");
        invalidate(article, revision);
    }

    @Transactional
    public void state(Long id, String status) {
        Long user = requireUser();
        if (!STATES.contains(status)) throw new BadRequestException("文章状态无效");
        StudioArticle article = mapper.lock(id);
        checkEditable(article, user);
        if ("blocked".equals(status)) requireOperator(user);
        Long revision = publicCache == null ? null : mapper.publicRevision(id);
        article.setStatus(status);
        article.setUpdatedAt(LocalDateTime.now());
        if ("published".equals(status)) article.setPublishedAt(LocalDateTime.now());
        if (mapper.updateById(article) != 1) throw new IllegalStateException("文章状态更新失败");
        invalidate(article, revision);
    }

    private void checkEditable(StudioArticle article, Long user) {
        if (article == null) throw new BadRequestException("文章不存在");
        if ("blocked".equals(article.getStatus()) || !user.equals(article.getAuthorUserId())) requireOperator(user);
    }

    private void apply(StudioArticle article, StudioArticle.Edit edit) {
        if (edit.getTitle() == null || edit.getTitle().isBlank() || edit.getTitle().length() > 100
                || edit.getSummary() == null || edit.getSummary().isBlank() || edit.getSummary().length() > 240
                || edit.getBody() == null || edit.getBody().isBlank() || edit.getBody().length() > 200000) {
            throw new BadRequestException("请填写标题、摘要和正文；正文最多 20 万字符");
        }
        if (edit.getContentFormat() == null || edit.getSourceType() == null
                || !Set.of("text", "markdown").contains(edit.getContentFormat())
                || !Set.of("original", "reprint", "link").contains(edit.getSourceType())) {
            throw new BadRequestException("正文格式或来源类型无效");
        }
        String author = trim(edit.getSourceAuthor());
        String url = trim(edit.getSourceUrl());
        String note = trim(edit.getLicenseNote());
        if (author.length() > 100 || url.length() > 2048 || note.length() > 500) {
            throw new BadRequestException("来源信息超出长度限制");
        }
        if ("original".equals(edit.getSourceType())) {
            author = ""; url = ""; note = "";
        } else {
            if (author.isBlank()) throw new BadRequestException("请填写原作者");
            url = sourceUrl(url);
            if ("reprint".equals(edit.getSourceType()) && note.isBlank()) {
                throw new BadRequestException("请填写转载授权或开放许可依据");
            }
            if ("link".equals(edit.getSourceType())) {
                if (edit.getBody().length() > 5000) throw new BadRequestException("外链资料只收录导读，最多 5000 字符");
                note = "";
            }
        }
        article.setTitle(edit.getTitle().strip());
        article.setSummary(edit.getSummary().strip());
        article.setBody(edit.getBody().strip());
        article.setContentFormat(edit.getContentFormat());
        article.setSourceType(edit.getSourceType());
        article.setSourceAuthor(author);
        article.setSourceUrl(url);
        article.setLicenseNote(note);
    }

    private String trim(String value) { return value == null ? "" : value.strip(); }

    private String sourceUrl(String value) {
        try {
            URI uri = new URI(value);
            if (("https".equalsIgnoreCase(uri.getScheme()) || "http".equalsIgnoreCase(uri.getScheme()))
                    && uri.getHost() != null && uri.getRawUserInfo() == null && uri.getPort() <= 65535) {
                String encoded = uri.toASCIIString();
                if (encoded.length() > 2048) throw new BadRequestException("原文链接编码后不能超过 2048 字符");
                return encoded;
            }
        } catch (URISyntaxException ignored) { }
        throw new BadRequestException("原文链接必须是有效的 HTTP(S) 地址，且不能包含账号密码");
    }

    private void enrich(List<StudioArticle> articles, Long user) {
        if (articles.isEmpty()) return;
        Set<Long> authorIds = articles.stream().map(StudioArticle::getAuthorUserId).filter(Objects::nonNull).collect(Collectors.toSet());
        Map<Long, String> names = new HashMap<>();
        if (!authorIds.isEmpty()) {
            List<UserDTO> found = users.queryUserByIds(authorIds);
            if (found != null) found.forEach(author -> names.put(author.getId(), author.getName()));
        }
        Set<Long> rewarded = user == null ? Collections.emptySet() : new HashSet<>(mapper.rewarded(user,
                articles.stream().map(StudioArticle::getId).collect(Collectors.toList())));
        for (StudioArticle article : articles) {
            article.setAuthorName(article.getAuthorUserId() == null ? "平台编辑部" : names.getOrDefault(article.getAuthorUserId(), "创作者"));
            article.setRewarded(rewarded.contains(article.getId()));
        }
    }

    private boolean isOperator(Long user) { return Objects.equals(users.queryUserType(user), UserType.STAFF.getValue()); }
    private void requireOperator(Long user) {
        if (!isOperator(user)) throw new ForbiddenException("需要运营人员权限");
    }
    private Long requireUser() {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录 LearnTrail");
        return user;
    }
}
