package com.learntrail.media.storage;

import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.media.config.MediaUploadProperties;
import com.learntrail.media.domain.dto.MediaUploadModels.Session;
import com.learntrail.media.domain.po.Media;
import com.learntrail.media.enums.FileStatus;
import com.learntrail.media.mapper.MediaUploadMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

/** 调用方必须先完成本人媒资或课程小节的真实权限校验。 */
@Component
@RequiredArgsConstructor
public class ObjectVideoPlayback {
    private final MediaUploadProperties config;
    private final MediaUploadMapper uploads;
    private final MultipartStorage storage;

    public boolean isStored(String fileId) { return fileId != null && fileId.matches("S[a-f0-9]{31}"); }

    public String url(Media media, Integer previewMinutes) {
        if (!config.isEnabled() || !isStored(media.getFileId()) || media.getStatus() != FileStatus.PROCESSED) {
            throw new BadRequestException("视频尚未处理完成或对象存储暂不可用");
        }
        Session session = uploads.find("0" + media.getFileId().substring(1));
        if (session == null || !"READY".equals(session.getPhase()) || !media.getId().equals(session.getMediaId())
                || media.getMediaUrl() == null || !media.getMediaUrl().matches("processed/" + session.getId() + "/[a-f0-9]{32}")) {
            throw new BadRequestException("视频处理结果暂不可用");
        }
        int minutes = previewMinutes == null ? 0 : previewMinutes;
        if (minutes < 0 || minutes > 3 || (previewMinutes != null && minutes == 0)) {
            throw new BadRequestException("试看时长无效");
        }
        try { return storage.readUrl(session.getBucket(), media.getMediaUrl() + "/" + minutes + ".mp4", 7200); }
        catch (RuntimeException e) { throw new BadRequestException("播放地址签发失败，请稍后重试"); }
    }
}
