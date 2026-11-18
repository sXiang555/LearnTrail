package com.learntrail.media.mapper;

import com.learntrail.media.domain.dto.MediaUploadModels.Task;
import org.apache.ibatis.annotations.*;
import java.util.List;

public interface MediaTaskMapper {
    @Insert("INSERT INTO learntrail_media_task(id,upload_id,media_id,next_attempt_at,dispatch_after,created_at,updated_at) " +
            "VALUES(#{id},#{upload},#{media},UTC_TIMESTAMP(3),UTC_TIMESTAMP(3),UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))")
    int insert(@Param("id") Long id, @Param("upload") String upload, @Param("media") Long media);
    @Select("SELECT * FROM learntrail_media_task WHERE id=#{id}")
    Task find(Long id);
    @Select("SELECT * FROM learntrail_media_task WHERE upload_id=#{id} AND pipeline_version=1")
    Task upload(String id);
    @Select("SELECT id FROM learntrail_media_task WHERE status IN ('PENDING','QUEUED','RETRY') " +
            "AND next_attempt_at<=UTC_TIMESTAMP(3) AND dispatch_after<=UTC_TIMESTAMP(3) " +
            "ORDER BY dispatch_after,id LIMIT 16")
    List<Long> due();
    @Update("UPDATE learntrail_media_task SET status='QUEUED',dispatch_after=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 120 SECOND)," +
            "updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND status IN ('PENDING','QUEUED','RETRY')")
    int dispatched(Long id);
    @Update("UPDATE learntrail_media_task SET dispatch_after=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 30 SECOND) " +
            "WHERE id=#{id} AND status IN ('PENDING','QUEUED','RETRY')")
    int dispatchFailed(Long id);
    @Update("UPDATE learntrail_media_task SET status='RUNNING',run_token=#{token},attempts=attempts+1," +
            "lease_until=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL #{lease} SECOND),updated_at=UTC_TIMESTAMP(3) " +
            "WHERE id=#{id} AND status IN ('PENDING','QUEUED','RETRY') AND attempts<#{maximum} " +
            "AND next_attempt_at<=UTC_TIMESTAMP(3)")
    int claim(@Param("id") Long id, @Param("token") String token, @Param("lease") int lease, @Param("maximum") int maximum);
    @Update("UPDATE learntrail_media_task SET lease_until=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL #{lease} SECOND)," +
            "updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND status='RUNNING' AND run_token=#{token} " +
            "AND lease_until>UTC_TIMESTAMP(3)")
    int renew(@Param("id") Long id, @Param("token") String token, @Param("lease") int lease);
    @Update("UPDATE learntrail_media_upload u JOIN learntrail_media_task t ON t.upload_id=u.id " +
            "SET u.phase='PROCESSING',u.updated_at=UTC_TIMESTAMP(3) WHERE t.id=#{id} AND t.status='RUNNING' AND t.run_token=#{token}")
    int processing(@Param("id") Long id, @Param("token") String token);
    @Update("UPDATE learntrail_media_task SET status='SUCCEEDED',lease_until=NULL,run_token=NULL,error_code=NULL," +
            "updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND status='RUNNING' AND run_token=#{token} " +
            "AND lease_until>UTC_TIMESTAMP(3)")
    int succeeded(@Param("id") Long id, @Param("token") String token);
    @Update("UPDATE learntrail_media_task SET status=IF(attempts>=#{maximum} OR #{permanent},'FAILED','RETRY')," +
            "error_code=#{error},next_attempt_at=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL #{delay} SECOND)," +
            "dispatch_after=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL #{delay} SECOND),lease_until=NULL,run_token=NULL," +
            "updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} AND status='RUNNING' AND run_token=#{token} AND lease_until>UTC_TIMESTAMP(3)")
    int failed(@Param("id") Long id, @Param("token") String token, @Param("error") String error,
               @Param("delay") int delay, @Param("maximum") int maximum, @Param("permanent") boolean permanent);
    @Update("UPDATE learntrail_media_task SET status=IF(attempts>=#{maximum},'FAILED','RETRY'),error_code='WORKER_INTERRUPTED'," +
            "next_attempt_at=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 30 SECOND),dispatch_after=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 30 SECOND)," +
            "lease_until=NULL,run_token=NULL,updated_at=UTC_TIMESTAMP(3) WHERE status='RUNNING' AND lease_until<UTC_TIMESTAMP(3)")
    int reclaim(int maximum);
    @Update("<script>UPDATE learntrail_media_upload u JOIN learntrail_media_task t ON t.upload_id=u.id " +
            "SET u.phase=IF(t.status='FAILED','FAILED','VERIFYING'),u.error_code=t.error_code,u.updated_at=UTC_TIMESTAMP(3) " +
            "WHERE t.status IN ('FAILED','RETRY') AND u.phase IN ('VERIFYING','PROCESSING') " +
            "AND (u.phase='PROCESSING' OR t.status='FAILED' OR NOT (u.error_code &lt;=&gt; t.error_code)) " +
            "<if test='taskId != null'>AND t.id=#{taskId}</if></script>")
    int syncFailure(@Param("taskId") Long taskId);
    @Insert("INSERT IGNORE INTO learntrail_media_digest(user_id,sha256,file_size,pipeline_version,media_id,created_at) " +
            "SELECT user_id,sha256,file_size,1,media_id,UTC_TIMESTAMP(3) FROM learntrail_media_upload WHERE id=#{id} AND phase='READY'")
    int verified(String id);
    @Update("UPDATE learntrail_media_upload SET phase='READY',error_code=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=#{id} " +
            "AND media_id=#{media} AND phase IN ('VERIFYING','PROCESSING')")
    int ready(@Param("id") String id, @Param("media") Long media);
}
