package com.learntrail.common.autoconfigure.redisson;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBucket;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.redisson.client.codec.StringCodec;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

@Slf4j
public class PublicContentCache {
    private static final String ROOT = "lt:public-content:v1:";
    private static final String EMPTY = "null";
    private final RedissonClient redis;
    private final ObjectMapper json;

    public PublicContentCache(RedissonClient redis, ObjectMapper json) {
        this.redis = redis;
        this.json = json;
    }

    public <T> T load(String key, Class<T> type, Supplier<T> source) {
        String hit;
        RLock lock;
        boolean acquired;
        try {
            hit = get(key);
            if (hit != null) return read(hit, type);
            lock = redis.getLock(ROOT + "rebuild:" + key);
            acquired = lock.tryLock(160, 3500, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return source.get();
        } catch (RuntimeException | JsonProcessingException e) {
            unavailable(e);
            return source.get();
        }

        try {
            try {
                hit = get(key);
                if (hit != null) return read(hit, type);
            } catch (RuntimeException | JsonProcessingException e) {
                unavailable(e);
                return source.get();
            }

            T content = source.get();
            if (acquired) {
                try {
                    long ttl = content == null ? ThreadLocalRandom.current().nextLong(5, 11)
                            : ThreadLocalRandom.current().nextLong(90, 151);
                    bucket(key).set(json.writeValueAsString(content), ttl, TimeUnit.SECONDS);
                } catch (RuntimeException | JsonProcessingException e) {
                    unavailable(e);
                }
            }
            return content;
        } finally {
            if (acquired) {
                try { lock.unlock(); }
                catch (RuntimeException e) { unavailable(e); }
            }
        }
    }

    public boolean knownMissing(String key) {
        try { return EMPTY.equals(get("missing:" + key)); }
        catch (RuntimeException e) { unavailable(e); return false; }
    }

    public void markMissing(String key) {
        try { bucket("missing:" + key).set(EMPTY, ThreadLocalRandom.current().nextLong(5, 11), TimeUnit.SECONDS); }
        catch (RuntimeException e) { unavailable(e); }
    }

    public void evictAfterCommit(String identity, String previousVersionKey) {
        Runnable evict = () -> {
            try {
                bucket("missing:" + identity).delete();
                if (previousVersionKey != null) bucket(previousVersionKey).delete();
            } catch (RuntimeException e) {
                // MySQL revision checks keep previous positive entries inaccessible. TTL removes them eventually.
                unavailable(e);
            }
        };
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override public void afterCommit() { evict.run(); }
            });
        } else {
            evict.run();
        }
    }

    private <T> T read(String value, Class<T> type) throws JsonProcessingException {
        return EMPTY.equals(value) ? null : json.readValue(value, type);
    }

    private String get(String key) { return bucket(key).get(); }
    private RBucket<String> bucket(String key) { return redis.getBucket(ROOT + key, StringCodec.INSTANCE); }

    private void unavailable(Exception error) {
        log.debug("公开内容缓存暂不可用，异常类型 {}", error.getClass().getSimpleName());
    }
}
