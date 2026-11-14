package com.learntrail.media.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.OrderItem;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.learntrail.api.client.course.CourseClient;
import com.learntrail.api.client.learning.LearningClient;
import com.learntrail.api.client.media.StudioMediaClient;
import com.learntrail.api.client.user.UserClient;
import com.learntrail.api.dto.course.MediaQuoteDTO;
import com.learntrail.api.dto.course.SectionInfoDTO;
import com.learntrail.api.dto.user.UserDTO;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.exceptions.CommonException;
import com.learntrail.common.exceptions.DbException;
import com.learntrail.common.exceptions.ForbiddenException;
import com.learntrail.common.exceptions.UnauthorizedException;
import com.learntrail.common.utils.*;
import com.learntrail.media.constants.FileErrorInfo;
import com.learntrail.media.domain.dto.MediaDTO;
import com.learntrail.media.domain.dto.MediaUploadResultDTO;
import com.learntrail.media.domain.po.Media;
import com.learntrail.media.domain.query.MediaQuery;
import com.learntrail.media.domain.vo.MediaVO;
import com.learntrail.media.domain.vo.VideoPlayVO;
import com.learntrail.media.enums.FileStatus;
import com.learntrail.media.mapper.MediaMapper;
import com.learntrail.media.service.IMediaService;
import com.learntrail.media.storage.IMediaStorage;
import com.learntrail.media.storage.LocalVideoStorage;
import com.learntrail.media.storage.MediaUploadResult;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.util.*;
import java.util.stream.Collectors;

import static com.learntrail.media.constants.FileErrorInfo.MEDIA_NOT_EXISTS;

/**
 * <p>
 * 媒资表，主要是视频文件 服务实现类
 * </p>
 *
 * @author 虎哥
 * @since 2022-06-30
 */
@Service
@RequiredArgsConstructor
public class MediaServiceImpl extends ServiceImpl<MediaMapper, Media> implements IMediaService {

    private final IMediaStorage mediaStorage;
    private final LocalVideoStorage localVideoStorage;

    private final CourseClient courseClient;

    private final LearningClient learningClient;

    private final UserClient userClient;

    @Override
    public String getUploadSignature() {
        return mediaStorage.getUploadSignature();
    }

    @Override
    public StudioMediaClient.Asset uploadStudioVideo(MultipartFile file) {
        Long userId = requireStudioUser();
        if (!Boolean.TRUE.equals(courseClient.studioAccess())) {
            throw new ForbiddenException("请先开通创作者空间");
        }
        String filename = Optional.ofNullable(file.getOriginalFilename()).orElse("").replace('\\', '/');
        filename = filename.substring(filename.lastIndexOf('/') + 1);
        if (file.isEmpty() || file.getSize() > 100L * 1024 * 1024 || filename.length() > 100
                || !filename.toLowerCase(Locale.ROOT).endsWith(".mp4")) {
            throw new BadRequestException("请选择不超过 100 MB 的 MP4 视频，文件名最多 100 个字符");
        }

        if (localVideoStorage.enabled()) {
            Media local = localVideoStorage.store(file, filename, userId);
            try {
                if (!save(local)) throw new DbException("视频登记失败，请重试");
            } catch (RuntimeException e) {
                localVideoStorage.delete(local.getFileId());
                throw e;
            }
            return studioAsset(local);
        }

        MediaUploadResult uploaded;
        try (InputStream input = file.getInputStream()) {
            uploaded = mediaStorage.uploadFile(filename, input, file.getSize());
        } catch (IOException | CommonException e) {
            throw new BadRequestException("视频上传失败，请检查点播服务配置后重试");
        }
        Media media = new Media();
        media.setFileId(uploaded.getFileId());
        media.setMediaUrl(uploaded.getMediaUrl());
        media.setCoverUrl(Optional.ofNullable(uploaded.getCoverUrl()).orElse(""));
        media.setFilename(filename);
        media.setSize(file.getSize());
        media.setStatus(FileStatus.UPLOADED);
        media.setCreater(userId);
        try {
            save(media);
        } catch (DuplicateKeyException e) {
            // VOD may deliver the processing event before the upload request returns.
            lambdaUpdate().eq(Media::getFileId, media.getFileId()).eq(Media::getCreater, 0L)
                    .set(Media::getCreater, userId).set(Media::getFilename, filename).update();
            media = lambdaQuery().eq(Media::getFileId, media.getFileId()).eq(Media::getCreater, userId).one();
            if (media == null) throw new ForbiddenException("无法登记该视频的归属");
        }
        return studioAsset(media);
    }

    @Override
    public List<StudioMediaClient.Asset> ownedStudioAssets(List<Long> ids, Long courseId) {
        Long userId = requireStudioUser();
        if (ids.isEmpty()) return Collections.emptyList();
        if (ids.size() > 30) throw new BadRequestException("每次最多查询 30 个视频");
        List<Long> bound = courseId == null ? Collections.emptyList() : courseClient.studioBoundMediaIds(courseId);
        if (bound == null) bound = Collections.emptyList();
        final List<Long> allowed = bound;
        return lambdaQuery().in(Media::getId, ids)
                .and(query -> query.eq(Media::getCreater, userId).or(!allowed.isEmpty()).in(!allowed.isEmpty(), Media::getId, allowed)).list()
                .stream().map(this::studioAsset).collect(Collectors.toList());
    }

    private StudioMediaClient.Asset studioAsset(Media media) {
        StudioMediaClient.Asset asset = new StudioMediaClient.Asset();
        asset.setId(media.getId());
        asset.setFilename(media.getFilename());
        asset.setDuration(media.getDuration() == null ? 0 : (int) Math.ceil(media.getDuration()));
        asset.setReady(media.getStatus() == FileStatus.PROCESSED && asset.getDuration() > 0);
        return asset;
    }

    private Long requireStudioUser() {
        Long userId = UserContext.getUser();
        if (userId == null) throw new UnauthorizedException("请先登录 LearnTrail");
        return userId;
    }

    @Override
    public VideoPlayVO getPlaySignatureBySectionId(Long sectionId) {
        // 1.根据sectionId查询媒课程信息
        SectionInfoDTO sectionInfo = courseClient.sectionInfo(sectionId);
        Long courseId = sectionInfo.getCourseId();
        // 2.查询用户课程表，是否是购买过的课程
        Long lessonId = learningClient.isLessonValid(courseId);

        if(lessonId != null){
            // 2.1.是，查询媒资信息，直接获取签名
            Media media = getById(sectionInfo.getMediaId());
            AssertUtils.isNotNull(media, MEDIA_NOT_EXISTS);
            // 1）获取签名
            return playback(media, null);
        }
        // 2.2.否，判断课程章节是否免费
        Boolean trailer = sectionInfo.getTrailer();
        if(BooleanUtils.isFalse(trailer)) {
            // 2.3.不免费，抛出异常
            throw new ForbiddenException(FileErrorInfo.MEDIA_NOT_FREE);
        }

        // 3.免费，获取课程信息
        Media media = getById(sectionInfo.getMediaId());
        AssertUtils.isNotNull(media, MEDIA_NOT_EXISTS);
        // 4.获取签名
        return playback(media, sectionInfo.getFreeDuration());
    }


    @Override
    public VideoPlayVO getPlaySignatureByMediaId(Long mediaId) {
        // 1.根据id查询媒资信息
        Media media = getById(mediaId);
        AssertUtils.isNotNull(media, MEDIA_NOT_EXISTS);
        if (localVideoStorage.isLocal(media.getFileId()) && !requireStudioUser().equals(media.getCreater())) {
            throw new ForbiddenException("只能预览自己上传的视频");
        }
        return playback(media, null);
    }

    private VideoPlayVO playback(Media media, Integer previewMinutes) {
        VideoPlayVO vo = new VideoPlayVO();
        if (localVideoStorage.isLocal(media.getFileId())) {
            if (!localVideoStorage.enabled()) throw new BadRequestException("本地视频存储暂不可用");
            vo.setUrl(localVideoStorage.playbackPath(media.getFileId(), previewMinutes));
        } else {
            vo.setFileId(media.getFileId());
            vo.setSignature(mediaStorage.getPlaySignature(media.getFileId(), UserContext.getUser(), previewMinutes));
        }
        return vo;
    }

    @Override
    public PageDTO<MediaVO> queryMediaPage(MediaQuery query) {
        // 1.分页条件
        Page<Media> mediaPage = new Page<>(query.getPageNo(), query.getPageSize());
        if(StringUtils.isNotBlank(query.getSortBy())){
            mediaPage.addOrder(new OrderItem(query.getSortBy(), query.getIsAsc()));
        }
        // 2.分页搜索
        lambdaQuery()
                .like(StringUtils.isNotBlank(query.getName()), Media::getFilename, query.getName())
                .page(mediaPage);
        // 3.解析数据
        List<Media> records = mediaPage.getRecords();
        if (CollUtils.isEmpty(records)) {
            return PageDTO.empty(mediaPage);
        }
        List<Long> ids = new ArrayList<>(records.size());
        Set<Long> createIds = new HashSet<>();
        for (Media m : records) {
            ids.add(m.getId());
            createIds.add(m.getCreater());
        }
        createIds.remove(0L);
        // 4.查询引用次数
        List<MediaQuoteDTO> mediaQuoteDTOS = courseClient.mediaUserInfo(ids);
        AssertUtils.isNotEmpty(mediaQuoteDTOS, FileErrorInfo.MEDIA_QUOTE_NOT_EXISTS);
        Map<Long, Integer> quoteMap = mediaQuoteDTOS
                .stream()
                .collect(Collectors.toMap(MediaQuoteDTO::getMediaId, MediaQuoteDTO::getQuoteNum));

        // 5.查询创建者信息
        Map<Long, String> userMap = null;
        if(CollUtils.isNotEmpty(createIds)) {
            List<UserDTO> users = userClient.queryUserByIds(createIds);
            AssertUtils.isNotEmpty(users, FileErrorInfo.USER_NOT_EXISTS);
            userMap = users.stream().collect(Collectors.toMap(UserDTO::getId, UserDTO::getName));
        }
        // 6.数据转换
        List<MediaVO> list = new ArrayList<>(records.size());
        for (Media m : records) {
            MediaVO v = BeanUtils.toBean(m, MediaVO.class);
            v.setUseTimes(quoteMap.get(m.getId()));
            if(userMap != null) {
                v.setCreater(userMap.get(m.getCreater()));
            }
            list.add(v);
        }
        return new PageDTO<>(mediaPage.getTotal(), mediaPage.getPages(), list);
    }

    @Override
    public MediaDTO save(MediaUploadResultDTO result) {
        // 1.查询视频信息
        List<Media> list = mediaStorage.queryMediaInfos(result.getFileId());
        AssertUtils.isNotEmpty(list, MEDIA_NOT_EXISTS);
        // 2.判断是否存在，幂等处理
        Media media = lambdaQuery().eq(Media::getFileId, result.getFileId()).one();
        if (media != null) {
            // 已经存在并且处理过
            return BeanUtils.toBean(media, MediaDTO.class);
        }
        // 3.查询视频信息
        media = list.get(0);
        // 4.直接保存数据库
        save(list.get(0));
        return BeanUtils.toBean(media, MediaDTO.class);
    }

    @Override
    public void updateMediaProcedureResult(Media media) {
        Media old = lambdaQuery().eq(Media::getFileId, media.getFileId()).one();
        if (old == null) {
            try {
                save(media);
                return;
            } catch (DuplicateKeyException ignored) {
                // The upload request created the row while the event was being processed.
            }
        }
        lambdaUpdate().eq(Media::getFileId, media.getFileId())
                .set(Media::getStatus, media.getStatus().getValue())
                .set(media.getDuration() != null, Media::getDuration, media.getDuration())
                .set(media.getCoverUrl() != null, Media::getCoverUrl, media.getCoverUrl())
                .update();
    }

    @Override
    @Transactional
    public void deleteMedia(String fileId) {
        // 1.删除云端文件
        mediaStorage.deleteFile(fileId);
        // 2.删除本地信息
        remove(new LambdaQueryWrapper<Media>().eq(Media::getFileId, fileId));
    }
}
