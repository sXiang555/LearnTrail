package com.learntrail.media.service;

import cn.hutool.core.util.HexUtil;
import cn.hutool.json.JSONArray;
import cn.hutool.json.JSONObject;
import com.learntrail.common.utils.JsonUtils;
import com.learntrail.media.config.MediaUploadProperties;
import com.learntrail.media.domain.dto.MediaUploadModels.Output;
import com.learntrail.media.domain.dto.MediaUploadModels.Session;
import com.learntrail.media.storage.MultipartStorage;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import java.io.*;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;
import java.util.stream.Stream;

@Component
public class MediaTranscoder {
    private final MediaUploadProperties config;
    private final MultipartStorage storage;
    private final String ffmpeg;
    private final String ffprobe;

    public MediaTranscoder(MediaUploadProperties config, MultipartStorage storage,
            @Value("${learntrail.media.ffmpeg:ffmpeg}") String ffmpeg,
            @Value("${learntrail.media.ffprobe:ffprobe}") String ffprobe) {
        this.config = config; this.storage = storage; this.ffmpeg = ffmpeg; this.ffprobe = ffprobe;
    }

    public Output process(Session session, String token, BooleanSupplier alive) throws IOException {
        Path root = Path.of(config.getTemporaryDirectory()).toAbsolutePath().normalize();
        Files.createDirectories(root, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
        if (Files.getFileStore(root).getUsableSpace() < session.getFileSize() * 4 + 256L * 1024 * 1024) {
            throw new Failure("TEMPORARY_DISK_FULL", false);
        }
        Path work = Files.createTempDirectory(root, "process-", PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(config.getProcessTimeoutSeconds());
        try {
            Path source = work.resolve("source.mp4");
            download(session, source, alive, deadline);
            float duration = probe(source, work.resolve("source.json"), alive, deadline);
            Path full = work.resolve("0.mp4");
            List<String> encode = command(source);
            encode.addAll(List.of("-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-map_chapters", "-1",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2", "-c:a", "aac",
                    "-movflags", "+faststart", "-fs", Long.toString(config.getMaxFileBytes() * 2), full.toString()));
            execute(encode, work.resolve("encode.log"), alive, deadline);
            float processedDuration = probe(full, work.resolve("full.json"), alive, deadline);
            if (Math.abs(processedDuration - duration) > Math.max(1, duration * 0.001)) throw new Failure("OUTPUT_INCOMPLETE", true);

            Path cover = work.resolve("cover.jpg");
            List<String> image = command(full);
            image.addAll(List.of("-ss", Float.toString(Math.min(1, duration / 2)), "-frames:v", "1",
                    "-vf", "scale=640:-2", "-q:v", "3", cover.toString()));
            execute(image, work.resolve("cover.log"), alive, deadline);
            for (int minutes = 1; minutes <= 3; minutes++) {
                Path preview = work.resolve(minutes + ".mp4");
                if (duration <= minutes * 60) Files.createLink(preview, full);
                else {
                    List<String> clip = command(full);
                    clip.addAll(List.of("-t", Integer.toString(minutes * 60), "-map", "0:v:0", "-map", "0:a:0?",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2",
                            "-c:a", "aac", "-movflags", "+faststart", preview.toString()));
                    execute(clip, work.resolve("preview.log"), alive, deadline);
                }
            }
            String prefix = "processed/" + session.getId() + "/" + token;
            for (String name : List.of("0.mp4", "1.mp4", "2.mp4", "3.mp4", "cover.jpg")) {
                check(alive, deadline);
                storage.put(session.getBucket(), prefix + "/" + name, work.resolve(name),
                        name.endsWith(".jpg") ? "image/jpeg" : "video/mp4");
            }
            check(alive, deadline);
            Output output = new Output(); output.setDuration(duration); output.setSize(Files.size(full));
            output.setObjectPrefix(prefix); return output;
        } finally { clean(work); }
    }

    private void download(Session session, Path target, BooleanSupplier alive, long deadline) throws IOException {
        MessageDigest digest;
        try { digest = MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
        long total = 0;
        try (InputStream in = storage.open(session.getBucket(), session.getObjectKey());
             OutputStream out = Files.newOutputStream(target, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {
            byte[] buffer = new byte[1024 * 1024]; int count;
            while ((count = in.read(buffer)) != -1) {
                check(alive, deadline); total += count;
                if (total > session.getFileSize()) throw new Failure("SOURCE_SIZE_MISMATCH", true);
                digest.update(buffer, 0, count); out.write(buffer, 0, count);
            }
        }
        if (total != session.getFileSize() || !HexUtil.encodeHexStr(digest.digest()).equals(session.getSha256())) {
            throw new Failure("SOURCE_HASH_MISMATCH", true);
        }
    }

    private float probe(Path input, Path output, BooleanSupplier alive, long deadline) throws IOException {
        execute(List.of(ffprobe, "-v", "error", "-protocol_whitelist", "file", "-enable_drefs", "0",
                "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", input.toString()), output, alive, deadline);
        if (Files.size(output) > 65536) throw new Failure("INVALID_MEDIA", true);
        try {
            JSONObject document = JsonUtils.parseObj(Files.readString(output));
            double duration = document.getJSONObject("format").getDouble("duration");
            JSONArray streams = document.getJSONArray("streams");
            boolean video = false;
            for (int i = 0; i < streams.size(); i++) {
                JSONObject stream = streams.getJSONObject(i);
                if ("video".equals(stream.getStr("codec_type"))) {
                    int width = stream.getInt("width", 0), height = stream.getInt("height", 0);
                    if (width <= 0 || height <= 0 || width > 4096 || height > 4096) throw new Failure("MEDIA_DIMENSIONS_EXCEEDED", true);
                    video = true;
                }
            }
            if (!video || !Double.isFinite(duration) || duration <= 0 || duration > 7200) throw new Failure("INVALID_MEDIA_DURATION", true);
            return (float) duration;
        } catch (Failure e) { throw e; }
        catch (RuntimeException e) { throw new Failure("INVALID_MEDIA", true); }
    }

    private List<String> command(Path input) {
        return new ArrayList<>(List.of(ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-threads", "2", "-filter_threads", "1", "-protocol_whitelist", "file", "-enable_drefs", "0", "-i", input.toString()));
    }

    private void execute(List<String> command, Path log, BooleanSupplier alive, long deadline) throws IOException {
        check(alive, deadline);
        Process process = new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile()).start();
        try {
            while (!process.waitFor(500, TimeUnit.MILLISECONDS)) check(alive, deadline);
            check(alive, deadline);
            if (process.exitValue() != 0) throw new Failure("INVALID_MEDIA", true);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt(); throw new Failure("WORKER_INTERRUPTED", false);
        } finally { if (process.isAlive()) process.destroyForcibly(); }
    }

    private void check(BooleanSupplier alive, long deadline) {
        if (Thread.currentThread().isInterrupted() || !alive.getAsBoolean()) throw new Failure("LEASE_LOST", false);
        if (System.nanoTime() > deadline) throw new Failure("PROCESS_TIMEOUT", false);
    }

    private void clean(Path work) {
        try (Stream<Path> paths = Files.walk(work)) {
            for (Path path : paths.sorted(Comparator.reverseOrder()).collect(java.util.stream.Collectors.toList())) Files.deleteIfExists(path);
        } catch (IOException ignored) { /* Only this attempt's private temporary directory is retained on cleanup failure. */ }
    }

    public static class Failure extends RuntimeException {
        private final String code;
        private final boolean permanent;
        public Failure(String code, boolean permanent) { super(code); this.code = code; this.permanent = permanent; }
        public String getCode() { return code; }
        public boolean isPermanent() { return permanent; }
    }
}
