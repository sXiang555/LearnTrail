package com.learntrail.media.service;

import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.media.domain.dto.MediaUploadModels.*;
import com.learntrail.media.domain.po.Media;
import com.learntrail.media.enums.FileStatus;
import com.learntrail.media.mapper.MediaMapper;
import com.learntrail.media.mapper.MediaTaskMapper;
import com.learntrail.media.mapper.MediaUploadMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;
import java.time.ZoneOffset;

/** 事务只覆盖数据库状态；对象存储和 FFmpeg 不占用数据库事务。 */
@Service
@RequiredArgsConstructor
public class MediaUploadStore {
    private final MediaUploadMapper uploads;
    private final MediaTaskMapper tasks;
    private final MediaMapper media;

    @Transactional
    public void rememberPart(String id, Long user, Part part) {
        Session session = uploads.lock(id, user);
        if (session == null || !"UPLOADING".equals(session.getPhase())
                || !session.getExpiresAt().isAfter(LocalDateTime.now(ZoneOffset.UTC))) {
            throw new BadRequestException("上传会话已结束或过期");
        }
        uploads.part(id, part);
    }

    @Transactional
    public void attach(Session snapshot, String token) {
        Session session = uploads.lock(snapshot.getId(), snapshot.getUserId());
        if (session == null) throw new BadRequestException("上传会话不存在");
        if (session.getMediaId() != null) return;
        if (!"COMPLETING".equals(session.getPhase()) || !token.equals(session.getLeaseToken())) {
            throw new BadRequestException("上传状态已变化，请重新查询");
        }
        Media asset = new Media();
        asset.setId(IdWorker.getId());
        asset.setFileId("S" + session.getId().substring(1));
        asset.setFilename(session.getFilename());
        asset.setSize(session.getFileSize());
        asset.setCreater(session.getUserId());
        asset.setUpdater(session.getUserId());
        asset.setStatus(FileStatus.UPLOADED);
        asset.setMediaUrl(""); asset.setCoverUrl(""); asset.setDeleted(0);
        if (media.insert(asset) != 1 || tasks.insert(IdWorker.getId(), session.getId(), asset.getId()) != 1
                || uploads.attached(session.getId(), token, asset.getId()) != 1) {
            throw new IllegalStateException("媒体任务登记失败");
        }
    }

    @Transactional
    public void succeeded(Task task, Session session, Output output) {
        // 先以有效租约和本次领取令牌提交，旧 Worker 的一切数据库写入都会回滚。
        if (tasks.succeeded(task.getId(), task.getRunToken()) != 1) {
            throw new IllegalStateException("媒体任务执行资格已失效");
        }
        UpdateWrapper<Media> update = new UpdateWrapper<Media>().eq("id", task.getMediaId())
                .eq("creater", session.getUserId()).eq("status", FileStatus.UPLOADED.getValue())
                .set("status", FileStatus.PROCESSED.getValue()).set("duration", output.getDuration())
                .set("size", output.getSize()).set("media_url", output.getObjectPrefix());
        if (media.update(null, update) != 1 || tasks.ready(session.getId(), task.getMediaId()) != 1) {
            throw new IllegalStateException("媒体结果登记失败");
        }
        tasks.verified(session.getId());
    }

    @Transactional
    public void failed(Task task, String error, int maximum, boolean permanent) {
        int delay = Math.min(900, 30 * (1 << Math.min(task.getAttempts(), 4)));
        if (tasks.failed(task.getId(), task.getRunToken(), error, delay, maximum, permanent) == 1) {
            tasks.syncFailure(task.getId());
        }
    }
}
