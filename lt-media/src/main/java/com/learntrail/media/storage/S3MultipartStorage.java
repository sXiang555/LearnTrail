package com.learntrail.media.storage;

import com.learntrail.media.config.MediaUploadProperties;
import com.learntrail.media.domain.dto.MediaUploadModels.Part;
import com.learntrail.media.domain.dto.MediaUploadModels.Session;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.core.checksums.RequestChecksumCalculation;
import software.amazon.awssdk.core.checksums.ResponseChecksumValidation;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.model.*;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
import software.amazon.awssdk.services.s3.presigner.model.UploadPartPresignRequest;
import javax.annotation.PreDestroy;
import java.io.InputStream;
import java.net.URI;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Component
public class S3MultipartStorage implements MultipartStorage {
    private final MediaUploadProperties properties;
    private final S3Client client;
    private final S3Presigner signer;

    public S3MultipartStorage(MediaUploadProperties properties) {
        this.properties = properties;
        if (!properties.isEnabled()) { client = null; signer = null; return; }
        StaticCredentialsProvider credentials = StaticCredentialsProvider.create(
                AwsBasicCredentials.create(properties.getAccessKey(), properties.getSecretKey()));
        S3Configuration config = S3Configuration.builder().pathStyleAccessEnabled(true).build();
        client = S3Client.builder().endpointOverride(URI.create(properties.getEndpoint()))
                .region(Region.of(properties.getRegion())).credentialsProvider(credentials)
                .serviceConfiguration(config)
                .requestChecksumCalculation(RequestChecksumCalculation.WHEN_REQUIRED)
                .responseChecksumValidation(ResponseChecksumValidation.WHEN_REQUIRED)
                .httpClientBuilder(UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(5)).socketTimeout(Duration.ofSeconds(30)))
                .overrideConfiguration(c -> c.apiCallTimeout(Duration.ofSeconds(45))
                        .apiCallAttemptTimeout(Duration.ofSeconds(30))).build();
        String endpoint = properties.getPublicEndpoint().isBlank() ? properties.getEndpoint() : properties.getPublicEndpoint();
        signer = S3Presigner.builder().endpointOverride(URI.create(endpoint))
                .region(Region.of(properties.getRegion())).credentialsProvider(credentials)
                .serviceConfiguration(config).build();
    }

    @Override
    public String initiate(Session session) {
        return client.createMultipartUpload(CreateMultipartUploadRequest.builder().bucket(session.getBucket())
                .key(session.getObjectKey()).contentType("video/mp4")
                .metadata(Map.of("lt-upload-id", session.getId())).build()).uploadId();
    }

    @Override
    public String signPart(Session session, int number) {
        return signer.presignUploadPart(UploadPartPresignRequest.builder()
                .signatureDuration(Duration.ofSeconds(properties.getUrlSeconds()))
                .uploadPartRequest(UploadPartRequest.builder().bucket(session.getBucket()).key(session.getObjectKey())
                        .uploadId(session.getStorageUploadId()).partNumber(number).build()).build()).url().toString();
    }

    @Override
    public List<Part> parts(Session session) {
        List<Part> parts = new ArrayList<>();
        Integer marker = null;
        do {
            ListPartsResponse response = client.listParts(ListPartsRequest.builder().bucket(session.getBucket())
                    .key(session.getObjectKey()).uploadId(session.getStorageUploadId())
                    .partNumberMarker(marker).maxParts(1000).build());
            response.parts().forEach(p -> parts.add(new Part(p.partNumber(), p.eTag(), p.size())));
            marker = Boolean.TRUE.equals(response.isTruncated()) ? response.nextPartNumberMarker() : null;
        } while (marker != null && parts.size() <= 10000);
        return parts;
    }

    @Override
    public void complete(Session session, List<Part> parts) {
        List<CompletedPart> completed = parts.stream().map(p -> CompletedPart.builder()
                .partNumber(p.getNumber()).eTag(p.getEtag()).build()).collect(Collectors.toList());
        client.completeMultipartUpload(CompleteMultipartUploadRequest.builder().bucket(session.getBucket())
                .key(session.getObjectKey()).uploadId(session.getStorageUploadId())
                .multipartUpload(CompletedMultipartUpload.builder().parts(completed).build()).build());
    }

    @Override
    public boolean completed(Session session) {
        try {
            HeadObjectResponse head = client.headObject(HeadObjectRequest.builder()
                    .bucket(session.getBucket()).key(session.getObjectKey()).build());
            return head.contentLength() == session.getFileSize()
                    && session.getId().equals(head.metadata().get("lt-upload-id"));
        } catch (S3Exception e) {
            if (e.statusCode() == 404) return false;
            throw e;
        }
    }

    @Override
    public void abort(Session session) {
        // 也覆盖初始化请求在对象存储成功、数据库登记之前中断留下的 uploadId。
        for (ListMultipartUploadsResponse page : client.listMultipartUploadsPaginator(
                ListMultipartUploadsRequest.builder().bucket(session.getBucket()).prefix(session.getObjectKey()).build())) {
            for (MultipartUpload upload : page.uploads()) {
                if (!session.getObjectKey().equals(upload.key())) continue;
                try {
                    client.abortMultipartUpload(AbortMultipartUploadRequest.builder().bucket(session.getBucket())
                            .key(upload.key()).uploadId(upload.uploadId()).build());
                } catch (S3Exception e) { if (e.statusCode() != 404) throw e; }
            }
        }
    }

    @Override
    public InputStream open(String bucket, String key) {
        return client.getObject(GetObjectRequest.builder().bucket(bucket).key(key).build());
    }

    @Override
    public void put(String bucket, String key, Path file, String contentType) {
        client.putObject(PutObjectRequest.builder().bucket(bucket).key(key).contentType(contentType).build(), RequestBody.fromFile(file));
    }

    @Override
    public String readUrl(String bucket, String key, int seconds) {
        return signer.presignGetObject(GetObjectPresignRequest.builder().signatureDuration(Duration.ofSeconds(seconds))
                .getObjectRequest(GetObjectRequest.builder().bucket(bucket).key(key).build()).build()).url().toString();
    }

    @PreDestroy
    public void close() {
        if (client != null) client.close();
        if (signer != null) signer.close();
    }
}
