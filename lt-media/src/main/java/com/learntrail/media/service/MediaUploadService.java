package com.learntrail.media.service;

import com.learntrail.api.client.course.CourseClient;
import com.learntrail.api.client.media.StudioMediaClient;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.exceptions.ForbiddenException;
import com.learntrail.common.exceptions.UnauthorizedException;
import com.learntrail.common.utils.UserContext;
import com.learntrail.media.config.MediaUploadProperties;
import com.learntrail.media.domain.dto.MediaUploadModels.*;
import com.learntrail.media.domain.po.Media;
import com.learntrail.media.enums.FileStatus;
import com.learntrail.media.mapper.MediaMapper;
import com.learntrail.media.mapper.MediaUploadMapper;
import com.learntrail.media.storage.MultipartStorage;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.*;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
@Slf4j
public class MediaUploadService {
    private final MediaUploadProperties config;
    private final MediaUploadMapper mapper;
    private final MediaMapper media;
    private final CourseClient courses;
    private final MultipartStorage storage;
    private final MediaUploadStore store;

    public Map<String, Object> capabilities() {
        user();
        return Map.of("enabled", config.isEnabled(), "maxFileBytes", config.getMaxFileBytes(),
                "partBytes", config.getPartBytes(), "parallelism", config.getUploadParallelism());
    }

    public View initiate(Init form) {
        Long user = user(); enabled();
        if (!Boolean.TRUE.equals(courses.studioAccess())) throw new ForbiddenException("请先开通创作者空间");
        String name = form.getFilename().strip();
        if (form.getSize() < 1 || form.getSize() > config.getMaxFileBytes() || name.isEmpty()
                || name.length() > 100 || !name.toLowerCase(Locale.ROOT).endsWith(".mp4")
                || name.contains("/") || name.contains("\\")) throw new BadRequestException("请选择允许大小内的 MP4 文件");
        Session session = mapper.request(user, form.getRequestKey());
        if (session == null) {
            Long existing = mapper.verified(user, form.getSha256(), form.getSize());
            if (existing != null) {
                View reused = new View(); reused.setPhase("READY"); reused.setAsset(asset(existing, user));
                reused.setFilename(name); reused.setSize(form.getSize()); return reused;
            }
            session = new Session();
            // The existing media.file_id column has 32 characters, including its storage marker.
            session.setId("0" + token().substring(1));
            session.setUserId(user);
            session.setRequestKey(form.getRequestKey()); session.setFilename(name); session.setFileSize(form.getSize());
            session.setSha256(form.getSha256()); session.setBucket(config.getBucket());
            session.setPartSize(config.getPartBytes());
            session.setPartCount((int) ((form.getSize() + config.getPartBytes() - 1) / config.getPartBytes()));
            session.setObjectKey("uploads/" + user + "/" + session.getId() + "/source.mp4");
            session.setExpiresAt(now().plusHours(config.getSessionHours()));
            mapper.insert(session);
            session = mapper.request(user, form.getRequestKey());
        }
        if (session == null) throw new IllegalStateException("上传会话登记失败");
        if (session.getFileSize() != form.getSize() || !session.getSha256().equals(form.getSha256())
                || !session.getFilename().equals(name)) throw new BadRequestException("同一请求标识不能上传不同文件");
        String claim = token();
        if ("INITIATING".equals(session.getPhase()) && mapper.claimInit(session.getId(), claim) == 1) {
            try {
                storage.abort(session);
                String uploadId = storage.initiate(session);
                mapper.initialized(session.getId(), claim, uploadId);
            } catch (RuntimeException e) {
                mapper.initFailed(session.getId(), claim);
                throw new BadRequestException("对象存储暂不可用，可使用相同文件重试");
            }
        }
        return status(session.getId());
    }

    public View status(String id) {
        Session session = owned(id);
        View view = view(session);
        if ("UPLOADING".equals(session.getPhase()) && session.getExpiresAt().isAfter(now())) {
            try {
                List<Part> actual = storage.parts(session);
                for (Part part : actual) {
                    if (part.getNumber() < 1 || part.getNumber() > session.getPartCount()
                            || part.getSize() != expectedSize(session, part.getNumber())) {
                        throw new BadRequestException("已上传分片大小不匹配，请重新上传该文件");
                    }
                    store.rememberPart(id, session.getUserId(), part);
                }
                view.setParts(actual);
            } catch (BadRequestException e) { throw e; }
            catch (RuntimeException e) { throw new BadRequestException("分片状态暂不可用，请稍后重试"); }
        }
        return view;
    }

    public SignedPart sign(String id, int number) {
        Session session = owned(id); active(session);
        if (number < 1 || number > session.getPartCount()) throw new BadRequestException("分片编号超出文件范围");
        SignedPart result = new SignedPart(); result.setNumber(number);
        result.setSize(expectedSize(session, number)); result.setExpiresIn(config.getUrlSeconds());
        try { result.setUrl(storage.signPart(session, number)); }
        catch (RuntimeException e) { throw new BadRequestException("上传地址签发失败，请稍后重试"); }
        return result;
    }

    public void remember(String id, PartReceipt receipt) {
        Session session = owned(id); active(session);
        int number = receipt.getNumber();
        if (number < 1 || number > session.getPartCount() || receipt.getEtag().contains("\r")
                || receipt.getEtag().contains("\n")) throw new BadRequestException("分片回执无效");
        store.rememberPart(id, session.getUserId(), new Part(number, receipt.getEtag(), expectedSize(session, number)));
    }

    public View complete(String id) {
        Session session = owned(id);
        if (session.getMediaId() != null) return view(session);
        if (!Set.of("UPLOADING", "COMPLETING").contains(session.getPhase())) throw new BadRequestException("当前上传状态不能完成");
        if (!session.getExpiresAt().isAfter(now()) && !"COMPLETING".equals(session.getPhase())) {
            throw new BadRequestException("上传会话已过期，请重新选择文件");
        }
        finish(session);
        return view(mapper.owned(id, session.getUserId()));
    }

    private void finish(Session session) {
        String claim = token();
        if (mapper.claimComplete(session.getId(), claim) != 1) return;
        try {
            if (!storage.completed(session)) {
                if (!session.getExpiresAt().isAfter(now())) {
                    if (mapper.expireCompletion(session.getId(), claim) == 1) clean(session, "EXPIRING", "EXPIRED");
                    return;
                }
                List<Part> actual = storage.parts(session);
                try { verifyParts(session, mapper.parts(session.getId()), actual); }
                catch (BadRequestException e) { mapper.incomplete(session.getId(), claim, "MISSING_OR_INVALID_PARTS"); throw e; }
                storage.complete(session, actual);
            }
            if (!storage.completed(session)) throw new IllegalStateException("对象合并结果未确认");
            store.attach(session, claim);
        } catch (BadRequestException e) { throw e; }
        catch (RuntimeException e) {
            // 不把超时当作失败；保留 COMPLETING，由同一对象元信息与租约恢复。
            throw new BadRequestException("合并结果待核对，请稍后查询或重试，不必重新上传");
        }
    }

    public static void verifyParts(Session session, List<Part> receipts, List<Part> actual) {
        if (actual.size() != session.getPartCount() || receipts.size() != session.getPartCount()) {
            throw new BadRequestException("仍有分片缺失，请先恢复上传");
        }
        Map<Integer, Part> recorded = receipts.stream().collect(Collectors.toMap(Part::getNumber, p -> p, (a, b) -> a));
        Set<Integer> seen = new HashSet<>();
        for (Part part : actual) {
            Part receipt = recorded.get(part.getNumber());
            if (part.getNumber() < 1 || part.getNumber() > session.getPartCount() || !seen.add(part.getNumber())
                    || receipt == null || part.getSize() != expectedSize(session, part.getNumber())
                    || !etag(part.getEtag()).equals(etag(receipt.getEtag()))) {
                throw new BadRequestException("分片回执与对象存储不一致，请恢复上传后重试");
            }
        }
        actual.sort(Comparator.comparingInt(Part::getNumber));
    }

    public void abort(String id) {
        Session session = owned(id);
        if ("ABORTED".equals(session.getPhase()) || "EXPIRED".equals(session.getPhase())) return;
        if (!"ABORTING".equals(session.getPhase()) && mapper.abandon(id, "ABORTING") != 1) {
            throw new BadRequestException("文件正在完成或处理，暂时不能取消上传");
        }
        clean(session, "ABORTING", "ABORTED");
    }

    public void recover() {
        if (!config.isEnabled()) return;
        for (Session session : mapper.interruptedCompletion()) {
            try { finish(session); }
            catch (RuntimeException e) { log.debug("上传会话 {} 等待核对", session.getId()); }
        }
        for (Session session : mapper.expired()) {
            try {
                String previous = "ABORTING".equals(session.getPhase()) ? "ABORTING" : "EXPIRING";
                if (Set.of("INITIATING", "UPLOADING").contains(session.getPhase())
                        && mapper.abandon(session.getId(), previous) != 1) continue;
                clean(session, previous, "ABORTING".equals(previous) ? "ABORTED" : "EXPIRED");
            } catch (RuntimeException e) { log.debug("上传会话 {} 等待清理", session.getId()); }
        }
    }

    private void clean(Session session, String previous, String next) {
        try { storage.abort(session); mapper.abandoned(session.getId(), previous, next); }
        catch (RuntimeException e) { throw new BadRequestException("上传取消已登记，后台将继续清理残留分片"); }
    }

    private Session owned(String id) {
        Long user = user(); enabled();
        Session session = mapper.owned(id, user);
        if (session == null) throw new BadRequestException("上传会话不存在");
        return session;
    }
    private void active(Session session) {
        if (!"UPLOADING".equals(session.getPhase()) || !session.getExpiresAt().isAfter(now())) {
            throw new BadRequestException("上传会话已结束或过期");
        }
    }
    private View view(Session session) {
        View result = new View(); result.setId(session.getId()); result.setFilename(session.getFilename());
        String phase = session.getPhase();
        boolean expired = Set.of("UPLOADING", "INITIATING").contains(phase) && !session.getExpiresAt().isAfter(now());
        result.setPhase(expired ? "EXPIRED" : phase);
        result.setSize(session.getFileSize()); result.setPartSize(session.getPartSize());
        result.setPartCount(session.getPartCount()); result.setParallelism(config.getUploadParallelism());
        result.setExpiresAt(session.getExpiresAt()); result.setError(session.getErrorCode());
        if (session.getMediaId() != null) result.setAsset(asset(session.getMediaId(), session.getUserId()));
        return result;
    }
    private StudioMediaClient.Asset asset(Long id, Long user) {
        Media value = media.selectById(id);
        if (value == null || !user.equals(value.getCreater()) || !Integer.valueOf(0).equals(value.getDeleted())) {
            throw new BadRequestException("媒体资源不存在");
        }
        StudioMediaClient.Asset asset = new StudioMediaClient.Asset(); asset.setId(value.getId());
        asset.setFilename(value.getFilename()); asset.setDuration(value.getDuration() == null ? 0 : (int) Math.ceil(value.getDuration()));
        asset.setReady(value.getStatus() == FileStatus.PROCESSED && asset.getDuration() > 0); return asset;
    }
    public static long expectedSize(Session session, int number) {
        return Math.min(session.getPartSize(), session.getFileSize() - (long) (number - 1) * session.getPartSize());
    }
    private static String etag(String value) { return value == null ? "" : value.replace("\"", ""); }
    private String token() { return UUID.randomUUID().toString().replace("-", ""); }
    private LocalDateTime now() { return LocalDateTime.now(ZoneOffset.UTC); }
    private void enabled() { if (!config.isEnabled()) throw new BadRequestException("分片上传暂未启用"); }
    private Long user() {
        Long user = UserContext.getUser(); if (user == null) throw new UnauthorizedException("请先登录 LearnTrail"); return user;
    }
}
