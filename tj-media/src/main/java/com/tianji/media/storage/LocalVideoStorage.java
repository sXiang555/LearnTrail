package com.tianji.media.storage;

import cn.hutool.crypto.digest.HMac;
import cn.hutool.crypto.digest.HmacAlgorithm;
import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.ForbiddenException;
import com.tianji.media.domain.po.Media;
import com.tianji.media.enums.FileStatus;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

@Slf4j
@Component
public class LocalVideoStorage {
    private static final Pattern FILE_ID = Pattern.compile("L[0-9a-f]{31}");
    private static final Pattern DURATION = Pattern.compile("Duration: (\\d+):(\\d+):(\\d+(?:\\.\\d+)?)");
    private final byte[] signingKey = new byte[32];
    private final Path root;
    private final String ffmpeg;

    public LocalVideoStorage(@Value("${learntrail.media.local-directory:}") String directory,
                             @Value("${learntrail.media.ffmpeg:ffmpeg}") String ffmpeg) {
        root = directory.isBlank() ? null : Path.of(directory).toAbsolutePath().normalize();
        this.ffmpeg = ffmpeg;
        new SecureRandom().nextBytes(signingKey);
    }

    public boolean enabled() {
        return root != null;
    }

    public boolean isLocal(String fileId) {
        return fileId != null && FILE_ID.matcher(fileId).matches();
    }

    public Media store(MultipartFile file, String filename, Long owner) {
        String id = "L" + UUID.randomUUID().toString().replace("-", "").substring(1);
        Path staging = null;
        try {
            var privateDirectory = PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------"));
            Files.createDirectories(root, privateDirectory);
            staging = Files.createTempDirectory(root, ".upload-", privateDirectory);
            Path input = staging.resolve("source.mp4");
            Path full = staging.resolve("0.mp4");
            Path logFile = staging.resolve("ffmpeg.log");
            file.transferTo(input);
            String output = convert(input, full, logFile, "-c:v", "copy", "-c:a", "aac");
            Matcher duration = DURATION.matcher(output);
            if (!output.contains("Video: h264") || !duration.find()) {
                throw new BadRequestException("本地上传需要使用 H.264 编码的 MP4 视频");
            }
            float seconds = 3600 * Integer.parseInt(duration.group(1))
                    + 60 * Integer.parseInt(duration.group(2)) + Float.parseFloat(duration.group(3));
            if (seconds <= 0) throw new BadRequestException("无法读取视频时长");
            for (int minutes = 1; minutes <= 3; minutes++) {
                Path preview = staging.resolve(minutes + ".mp4");
                int cutoff = minutes * 60;
                if (seconds <= cutoff) {
                    Files.createLink(preview, full);
                } else {
                    convert(full, preview, logFile, "-t", Integer.toString(cutoff), "-c:v", "libx264",
                            "-preset", "ultrafast", "-crf", "24", "-threads", "2", "-c:a", "aac");
                }
            }
            Files.delete(input);
            Files.delete(logFile);
            long size = Files.size(full);
            Files.move(staging, root.resolve(id), StandardCopyOption.ATOMIC_MOVE);
            Media media = new Media();
            media.setFileId(id);
            media.setFilename(filename);
            media.setMediaUrl("");
            media.setCoverUrl("");
            media.setDuration(seconds);
            media.setSize(size);
            media.setCreater(owner);
            media.setStatus(FileStatus.PROCESSED);
            return media;
        } catch (IOException e) {
            log.warn("本地视频处理失败", e);
            throw new BadRequestException("本地视频处理失败，请检查 MP4 文件和 FFmpeg 配置");
        } finally {
            clean(staging);
        }
    }

    public void delete(String fileId) {
        if (enabled() && isLocal(fileId)) clean(root.resolve(fileId));
    }

    public String playbackPath(String fileId, Integer previewMinutes) {
        int minutes = previewMinutes == null ? 0 : Math.max(1, Math.min(3, previewMinutes));
        String payload = fileId + "." + minutes + "." + Instant.now().plusSeconds(7200).getEpochSecond();
        return "/api/ms/medias/studio/playback/" + payload + "." + sign(payload);
    }

    public Path resolve(String ticket) {
        String[] parts = ticket.split("\\.", -1);
        if (!enabled() || parts.length != 4 || !isLocal(parts[0]) || !parts[1].matches("[0-3]")
                || !parts[2].matches("[0-9]{1,12}")) {
            throw new ForbiddenException("播放链接无效");
        }
        String expected = sign(String.join(".", parts[0], parts[1], parts[2]));
        if (!MessageDigest.isEqual(expected.getBytes(StandardCharsets.US_ASCII), parts[3].getBytes(StandardCharsets.US_ASCII))
                || Long.parseLong(parts[2]) < Instant.now().getEpochSecond()) {
            throw new ForbiddenException("播放链接无效或已过期");
        }
        Path file = root.resolve(parts[0]).resolve(parts[1] + ".mp4");
        if (!Files.isRegularFile(file)) throw new BadRequestException("视频文件暂不可用");
        return file;
    }

    private String sign(String payload) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(new HMac(HmacAlgorithm.HmacSHA256, signingKey)
                .digest(payload.getBytes(StandardCharsets.US_ASCII)));
    }

    private String convert(Path input, Path output, Path logFile, String... encoding) throws IOException {
        List<String> command = new ArrayList<>(List.of(ffmpeg, "-nostdin", "-hide_banner", "-nostats",
                "-protocol_whitelist", "file", "-f", "mov", "-i", input.toString(),
                "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-map_chapters", "-1"));
        command.addAll(List.of(encoding));
        command.addAll(List.of("-movflags", "+faststart", output.toString()));
        Process process = new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(logFile.toFile()).start();
        try {
            if (!process.waitFor(90, TimeUnit.SECONDS) || process.exitValue() != 0) {
                throw new IOException("FFmpeg exited unsuccessfully or timed out");
            }
            return Files.readString(logFile);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("Video processing interrupted", e);
        } finally {
            if (process.isAlive()) process.destroyForcibly();
        }
    }

    private void clean(Path directory) {
        if (directory == null || !Files.exists(directory)) return;
        try (Stream<Path> paths = Files.walk(directory)) {
            for (Path path : paths.sorted(Comparator.reverseOrder()).toArray(Path[]::new)) {
                Files.deleteIfExists(path);
            }
        } catch (IOException e) {
            log.warn("本地视频目录清理失败", e);
        }
    }
}
