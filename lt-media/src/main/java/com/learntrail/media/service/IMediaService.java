package com.learntrail.media.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.learntrail.api.client.media.StudioMediaClient;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.media.domain.dto.MediaDTO;
import com.learntrail.media.domain.dto.MediaUploadResultDTO;
import com.learntrail.media.domain.po.Media;
import com.learntrail.media.domain.query.MediaQuery;
import com.learntrail.media.domain.vo.MediaVO;
import com.learntrail.media.domain.vo.VideoPlayVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * <p>
 * 媒资表，主要是视频文件 服务类
 * </p>
 *
 * @author 虎哥
 * @since 2022-06-30
 */
public interface IMediaService extends IService<Media> {

    String getUploadSignature();

    StudioMediaClient.Asset uploadStudioVideo(MultipartFile file);

    List<StudioMediaClient.Asset> ownedStudioAssets(List<Long> ids, Long courseId);

    VideoPlayVO getPlaySignatureBySectionId(Long fileId);

    MediaDTO save(MediaUploadResultDTO mediaResult);

    void updateMediaProcedureResult(Media media);

    void deleteMedia(String fileId);

    VideoPlayVO getPlaySignatureByMediaId(Long mediaId);

    PageDTO<MediaVO> queryMediaPage(MediaQuery query);
}
