package com.learntrail.media.storage;

import com.learntrail.media.domain.dto.MediaUploadModels.Part;
import com.learntrail.media.domain.dto.MediaUploadModels.Session;
import java.io.InputStream;
import java.nio.file.Path;
import java.util.List;

/** 数据面直达对象存储，应用只签发授权、核对状态及处理媒体。 */
public interface MultipartStorage {
    String initiate(Session session);
    String signPart(Session session, int number);
    List<Part> parts(Session session);
    void complete(Session session, List<Part> parts);
    boolean completed(Session session);
    void abort(Session session);
    InputStream open(String bucket, String key);
    void put(String bucket, String key, Path file, String contentType);
    String readUrl(String bucket, String key, int seconds);
}
