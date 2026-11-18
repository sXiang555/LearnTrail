package com.learntrail.media.mapper;

import com.learntrail.media.domain.dto.MediaUploadModels.Part;
import com.learntrail.media.domain.dto.MediaUploadModels.Session;
import org.apache.ibatis.annotations.*;
import java.util.List;

public interface MediaUploadMapper {
    @Select("SELECT * FROM learntrail_media_upload WHERE id=#{id} AND user_id=#{user}")
    Session owned(@Param("id") String id, @Param("user") Long user);
    @Select("SELECT * FROM learntrail_media_upload WHERE id=#{id} AND user_id=#{user} FOR UPDATE")
    Session lock(@Param("id") String id, @Param("user") Long user);
    @Select("SELECT * FROM learntrail_media_upload WHERE id=#{id}")
    Session find(String id);
    @Select("SELECT * FROM learntrail_media_upload WHERE user_id=#{user} AND request_key=#{key}")
    Session request(@Param("user") Long user, @Param("key") String key);
    @Insert("INSERT IGNORE INTO learntrail_media_upload(id,user_id,request_key,filename,file_size,sha256," +
            "part_size,part_count,bucket,object_key,expires_at,created_at,updated_at) VALUES(#{id},#{userId}," +
            "#{requestKey},#{filename},#{fileSize},#{sha256},#{partSize},#{partCount},#{bucket},#{objectKey}," +
            "#{expiresAt},UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))")
    int insert(Session session);
    @Update("UPDATE learntrail_media_upload SET lease_token=#{token},lease_until=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 120 SECOND)," +
            "updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND phase='INITIATING' AND expires_at>UTC_TIMESTAMP(3) " +
            "AND (lease_until IS NULL OR lease_until<UTC_TIMESTAMP(3))")
    int claimInit(@Param("id") String id, @Param("token") String token);
    @Update("UPDATE learntrail_media_upload SET storage_upload_id=#{storageId},phase='UPLOADING',lease_token=NULL," +
            "lease_until=NULL,error_code=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} " +
            "AND phase='INITIATING' AND lease_token=#{token}")
    int initialized(@Param("id") String id, @Param("token") String token, @Param("storageId") String storageId);
    @Update("UPDATE learntrail_media_upload SET lease_token=NULL,lease_until=NULL,error_code='INIT_RETRY'," +
            "updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND phase='INITIATING' AND lease_token=#{token}")
    int initFailed(@Param("id") String id, @Param("token") String token);
    @Update("UPDATE learntrail_media_upload SET phase='COMPLETING',lease_token=#{token}," +
            "lease_until=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 120 SECOND),updated_at=UTC_TIMESTAMP(3) " +
            "WHERE id=#{id} AND (phase='COMPLETING' OR (phase='UPLOADING' AND expires_at>UTC_TIMESTAMP(3))) " +
            "AND (lease_until IS NULL OR lease_until<UTC_TIMESTAMP(3))")
    int claimComplete(@Param("id") String id, @Param("token") String token);
    @Update("UPDATE learntrail_media_upload SET phase='UPLOADING',lease_token=NULL,lease_until=NULL," +
            "error_code=#{error},updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND phase='COMPLETING' AND lease_token=#{token}")
    int incomplete(@Param("id") String id, @Param("token") String token, @Param("error") String error);
    @Update("UPDATE learntrail_media_upload SET phase='EXPIRING',lease_token=NULL,lease_until=NULL,updated_at=UTC_TIMESTAMP(3) " +
            "WHERE id=#{id} AND phase='COMPLETING' AND lease_token=#{token}")
    int expireCompletion(@Param("id") String id, @Param("token") String token);
    @Update("UPDATE learntrail_media_upload SET phase='VERIFYING',media_id=#{media},lease_token=NULL,lease_until=NULL," +
            "error_code=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND phase='COMPLETING' AND lease_token=#{token}")
    int attached(@Param("id") String id, @Param("token") String token, @Param("media") Long media);
    @Insert("INSERT INTO learntrail_media_upload_part(upload_id,part_number,etag,part_size,updated_at) " +
            "VALUES(#{id},#{part.number},#{part.etag},#{part.size},UTC_TIMESTAMP(3)) ON DUPLICATE KEY UPDATE " +
            "etag=VALUES(etag),part_size=VALUES(part_size),updated_at=VALUES(updated_at)")
    int part(@Param("id") String id, @Param("part") Part part);
    @Select("SELECT part_number AS number,etag,part_size AS size FROM learntrail_media_upload_part " +
            "WHERE upload_id=#{id} ORDER BY part_number")
    List<Part> parts(String id);
    @Select("SELECT d.media_id FROM learntrail_media_digest d JOIN media m ON m.id=d.media_id " +
            "WHERE d.user_id=#{user} AND d.sha256=#{hash} AND d.file_size=#{size} AND d.pipeline_version=1 " +
            "AND m.creater=#{user} AND m.status=3 AND m.deleted=0")
    Long verified(@Param("user") Long user, @Param("hash") String hash, @Param("size") long size);
    @Update("UPDATE learntrail_media_upload SET phase=#{next},lease_token=NULL,lease_until=NULL,updated_at=UTC_TIMESTAMP(3) " +
            "WHERE id=#{id} AND phase IN ('UPLOADING','INITIATING') AND (lease_until IS NULL OR lease_until<UTC_TIMESTAMP(3))")
    int abandon(@Param("id") String id, @Param("next") String next);
    @Update("UPDATE learntrail_media_upload SET phase=#{next},updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND phase=#{previous}")
    int abandoned(@Param("id") String id, @Param("previous") String previous, @Param("next") String next);
    @Select("SELECT * FROM learntrail_media_upload WHERE " +
            "(phase IN ('INITIATING','UPLOADING') AND expires_at<UTC_TIMESTAMP(3) " +
            "AND (lease_until IS NULL OR lease_until<UTC_TIMESTAMP(3))) OR phase IN ('ABORTING','EXPIRING') " +
            "ORDER BY expires_at LIMIT 20")
    List<Session> expired();
    @Select("SELECT * FROM learntrail_media_upload WHERE phase='COMPLETING' AND lease_until<UTC_TIMESTAMP(3) " +
            "ORDER BY lease_until LIMIT 10")
    List<Session> interruptedCompletion();
}
