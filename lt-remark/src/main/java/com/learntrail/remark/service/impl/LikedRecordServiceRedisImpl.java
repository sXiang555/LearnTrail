package com.learntrail.remark.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.learntrail.api.client.course.CourseClient;
import com.learntrail.api.client.learning.LearningClient;
import com.learntrail.api.dto.learning.StudioArticleLinkDTO;
import com.learntrail.api.dto.remark.LikedTimesDTO;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.exceptions.ForbiddenException;
import com.learntrail.common.exceptions.UnauthorizedException;
import com.learntrail.common.autoconfigure.mq.RabbitMqHelper;
import com.learntrail.common.utils.CollUtils;
import com.learntrail.common.utils.StringUtils;
import com.learntrail.common.utils.UserContext;
import com.learntrail.remark.constants.RedisConstants;
import com.learntrail.remark.domain.dto.LikeRecordFormDTO;
import com.learntrail.remark.domain.po.LikedRecord;
import com.learntrail.remark.mapper.LikedRecordMapper;
import com.learntrail.remark.service.ILikedRecordService;
import com.learntrail.remark.task.ContentStatsOutbox;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.connection.StringRedisConnection;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ZSetOperations;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.IntStream;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;

import static com.learntrail.common.constants.MqConstants.Exchange.LIKE_RECORD_EXCHANGE;
import static com.learntrail.common.constants.MqConstants.Key.LIKED_TIMES_KEY_TEMPLATE;

/**
 * <p>
 * 点赞记录表 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Service
@RequiredArgsConstructor
public class LikedRecordServiceRedisImpl extends ServiceImpl<LikedRecordMapper, LikedRecord> implements ILikedRecordService {

    private final RabbitMqHelper mqHelper;
    private final RedisTemplate redisTemplate;
    private final StringRedisTemplate stringRedisTemplate;
    private final CourseClient courses;
    private final LearningClient learning;

    // New content types must not collide with legacy QA/NOTE business IDs.
    private static final DefaultRedisScript<Long> CONTENT_WRITE = new DefaultRedisScript<>(
            "if ARGV[2] == '1' then redis.call('SADD', KEYS[1], ARGV[1]) " +
            "else redis.call('SREM', KEYS[1], ARGV[1]) end; " +
            "redis.call('HSETNX',KEYS[2],'views',0); redis.call('HSETNX',KEYS[2],'version',ARGV[3]); " +
            "redis.call('HINCRBY',KEYS[2],'version',1); redis.call('ZADD',KEYS[3],ARGV[3],ARGV[4]); " +
            "return redis.call('SCARD', KEYS[1])", Long.class);
    private static final DefaultRedisScript<List> CONTENT_STATE = new DefaultRedisScript<>(
            "if redis.call('HSETNX',KEYS[2],'version',ARGV[2])==1 then " +
            "redis.call('HSETNX',KEYS[2],'views',0); redis.call('ZADD',KEYS[3],ARGV[2],ARGV[3]) end; " +
            "return {redis.call('SCARD', KEYS[1]), redis.call('SISMEMBER', KEYS[1], ARGV[1]), " +
            "tonumber(redis.call('HGET',KEYS[2],'views') or '0')}", List.class);
    private static final DefaultRedisScript<List> CONTENT_VIEW = new DefaultRedisScript<>(
            "local added=redis.call('SADD',KEYS[4],ARGV[1]); " +
            "if added==1 then redis.call('EXPIRE',KEYS[4],172800); " +
            "redis.call('HINCRBY',KEYS[2],'views',1); redis.call('HSETNX',KEYS[2],'version',ARGV[2]); " +
            "redis.call('HINCRBY',KEYS[2],'version',1); redis.call('ZADD',KEYS[3],ARGV[2],ARGV[3]) end; " +
            "return {tonumber(redis.call('HGET',KEYS[2],'views') or '0'),added}", List.class);

    @Override
    public void addLikeRecord(LikeRecordFormDTO recordDTO) {
        if (isContentType(recordDTO.getBizType())) {
            Long user = requireContentUser();
            requirePublicContent(recordDTO.getBizType(), recordDTO.getBizId());
            if (recordDTO.getLiked() == null) throw new BadRequestException("请明确点赞或取消点赞");
            Long count = stringRedisTemplate.execute(CONTENT_WRITE,
                    contentKeys(recordDTO.getBizType(), recordDTO.getBizId()),
                    user.toString(), recordDTO.getLiked() ? "1" : "0", Long.toString(System.currentTimeMillis()),
                    recordDTO.getBizType() + ":" + recordDTO.getBizId());
            if (count == null) throw new IllegalStateException("点赞暂不可用，请重新读取状态后重试");
            return;
        }
        // 1.基于前端的参数，判断是执行点赞还是取消点赞
        boolean success = recordDTO.getLiked() ? like(recordDTO) : unlike(recordDTO);
        // 2.判断是否执行成功，如果失败，则直接结束
        if (!success) {
            return;
        }
        // 3.如果执行成功，统计点赞总数
        Long likedTimes = redisTemplate.opsForSet()
                .size(RedisConstants.LIKES_BIZ_KEY_PREFIX + recordDTO.getBizId());
        if (likedTimes == null) {
            return;
        }
        // 4.缓存点赞总数到Redis
        redisTemplate.opsForZSet().add(
                RedisConstants.LIKES_TIMES_KEY_PREFIX + recordDTO.getBizType(),
                recordDTO.getBizId().toString(),
                likedTimes
        );
    }

    @Override
    public Map<String, Object> contentLikeState(String bizType, Long bizId) {
        Long user = requireContentUser();
        requirePublicContent(bizType, bizId);
        List<?> values = stringRedisTemplate.execute(CONTENT_STATE,
                contentKeys(bizType, bizId), user.toString(), Long.toString(System.currentTimeMillis()), bizType + ":" + bizId);
        if (values == null || values.size() != 3 || !(values.get(0) instanceof Number)
                || !(values.get(1) instanceof Number) || !(values.get(2) instanceof Number)) {
            throw new IllegalStateException("点赞状态暂不可用，请稍后重试");
        }
        return Map.of("bizId", bizId, "bizType", bizType,
                "likedTimes", ((Number) values.get(0)).longValue(),
                "liked", ((Number) values.get(1)).longValue() == 1,
                "viewTimes", ((Number) values.get(2)).longValue());
    }

    @Override
    public Map<String, Object> recordContentView(String bizType, Long bizId) {
        Long user = requireContentUser();
        requirePublicContent(bizType, bizId);
        List<String> keys = new ArrayList<>(contentKeys(bizType, bizId));
        String date = LocalDate.now(ZoneId.of("Asia/Shanghai")).format(DateTimeFormatter.BASIC_ISO_DATE);
        keys.add("likes:content:view:" + bizType + ":" + bizId + ":" + date);
        List<?> values = stringRedisTemplate.execute(CONTENT_VIEW, keys, user.toString(),
                Long.toString(System.currentTimeMillis()), bizType + ":" + bizId);
        if (values == null || values.size() != 2) throw new IllegalStateException("浏览统计暂不可用，请稍后重试");
        return Map.of("viewTimes", ((Number) values.get(0)).longValue(),
                "counted", ((Number) values.get(1)).longValue() == 1);
    }

    private static boolean isContentType(String type) {
        return "ARTICLE".equals(type) || "SERIES".equals(type);
    }

    private static String contentKey(String type, Long id) {
        return RedisConstants.LIKES_BIZ_KEY_PREFIX + type + ":" + id;
    }

    private static List<String> contentKeys(String type, Long id) {
        return List.of(contentKey(type, id), ContentStatsOutbox.meta(type, id), ContentStatsOutbox.DIRTY);
    }

    private Long requireContentUser() {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录后点赞");
        return user;
    }

    private void requirePublicContent(String type, Long id) {
        if (!isContentType(type) || id == null || id <= 0) {
            throw new BadRequestException("点赞内容类型或标识无效");
        }
        boolean available;
        if ("ARTICLE".equals(type)) {
            List<StudioArticleLinkDTO> articles = learning.studioArticleLinks(List.of(id), false);
            if (articles == null) throw new BadRequestException("文章服务暂不可用，请稍后重试");
            available = articles.stream().anyMatch(article -> id.equals(article.getId()));
        } else {
            Boolean published = courses.studioSeriesLikeable(id);
            if (published == null) throw new BadRequestException("专栏服务暂不可用，请稍后重试");
            available = published;
        }
        if (!available) throw new ForbiddenException("内容不存在或暂未公开，不能点赞");
    }

    @Override
    public Set<Long> isBizLiked(List<Long> bizIds) {
        // 1.获取登录用户id
        Long userId = UserContext.getUser();
        // 2.查询点赞状态
        List<Object> objects = stringRedisTemplate.executePipelined((RedisCallback<Object>) connection -> {
            StringRedisConnection src = (StringRedisConnection) connection;
            for (Long bizId : bizIds) {
                String key = RedisConstants.LIKES_BIZ_KEY_PREFIX + bizId;
                src.sIsMember(key, userId.toString());
            }
            return null;
        });
        // 3.返回结果
        return IntStream.range(0, objects.size()) // 创建从0到集合size的流
                .filter(i -> (boolean) objects.get(i)) // 遍历每个元素，保留结果为true的角标i
                .mapToObj(bizIds::get)// 用角标i取bizIds中的对应数据，就是点赞过的id
                .collect(Collectors.toSet());// 收集
    }

    @Override
    public void readLikedTimesAndSendMessage(String bizType, int maxBizSize) {
        String key = RedisConstants.LIKES_TIMES_KEY_PREFIX + bizType;
        Set<ZSetOperations.TypedTuple<String>> tuples = stringRedisTemplate.opsForZSet().popMin(key, maxBizSize);
        if (CollUtils.isEmpty(tuples)){
            return;
        }
        //构建消息
        ArrayList<LikedTimesDTO> likedTimesDTOS = new ArrayList<>(tuples.size());
        for (ZSetOperations.TypedTuple<String> tuple : tuples){
            Double likedTimes = tuple.getScore();
            String bizId = tuple.getValue();
            if (likedTimes == null || bizId == null){
                continue;
            }
            likedTimesDTOS.add(LikedTimesDTO.of(Long.valueOf(bizId), likedTimes.intValue()));
        }

        mqHelper.send(
                LIKE_RECORD_EXCHANGE,
                StringUtils.format(LIKED_TIMES_KEY_TEMPLATE, bizType),
                likedTimesDTOS
        );



    }

    private boolean unlike(LikeRecordFormDTO recordDTO) {
        // 1.获取用户id
        Long userId = UserContext.getUser();
        // 2.获取Key
        String key = RedisConstants.LIKES_BIZ_KEY_PREFIX + recordDTO.getBizId();
        // 3.执行SREM命令
        Long result = redisTemplate.opsForSet().remove(key, userId.toString());
        return result != null && result > 0;
    }

    private boolean like(LikeRecordFormDTO recordDTO) {
        // 1.查询点赞记录
        Long user = UserContext.getUser();
        String key = RedisConstants.LIKES_BIZ_KEY_PREFIX + recordDTO.getBizId();
        // 3.如果不存在，直接新增
        Long add = redisTemplate.opsForSet().add(
                key,
                user.toString()
        );
        return add != null && add > 0;
    }
}
