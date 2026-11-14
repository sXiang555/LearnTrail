package com.learntrail.learning.handler;

import com.learntrail.common.utils.CollUtils;
import com.learntrail.common.utils.DateUtils;
import com.learntrail.learning.constants.RedisConstants;
import com.learntrail.learning.domain.po.PointsBoard;
import com.learntrail.learning.domain.po.PointsBoardSeason;
import com.learntrail.learning.service.IPointsBoardSeasonService;
import com.learntrail.learning.service.IPointsBoardService;
import com.learntrail.learning.service.IPointsRecordService;
import com.learntrail.learning.utils.TableInfoContext;
import com.xxl.job.core.context.XxlJobHelper;
import com.xxl.job.core.handler.annotation.XxlJob;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import static com.learntrail.learning.constants.LearningConstants.POINTS_BOARD_TABLE_PREFIX;

@Slf4j
@Component
@RequiredArgsConstructor
public class PointsBoardPersistentHandler {

    private static final String ARCHIVE_STATUS_KEY_PREFIX = "boards:archive:";

    private final IPointsBoardSeasonService seasonService;

    private final IPointsBoardService pointsBoardService;

    private final IPointsRecordService pointsRecordService;

    private final StringRedisTemplate redisTemplate;
    private final StringRedisTemplate stringRedisTemplate;

    @XxlJob("createSeasonJob")
    public void createCurrentSeason() {
        ensureSeason(LocalDate.now());
    }

    @XxlJob("createTableJob")
    public void createPointsBoardTableOfLastSeason(){
        // 1.获取上月时间
        LocalDateTime lastMonth = LocalDateTime.now().minusMonths(1);
        // 2.查询赛季id，若月初创建赛季的任务漏跑则补建
        Integer seasonId = ensureSeason(lastMonth.toLocalDate());
        // 3.创建表
        pointsBoardService.createPointsBoardTableBySeason(seasonId);
    }

    @XxlJob("savePointsBoard2DB")
    public void savePointsBoard2DB(){
        // 1.获取上月时间
        LocalDateTime lastMonth = LocalDateTime.now().minusMonths(1);
        // 2.计算动态表名
        // 2.1.查询赛季信息
        LocalDate monthStart = lastMonth.toLocalDate().withDayOfMonth(1);
        PointsBoardSeason season = seasonService.lambdaQuery()
                .eq(PointsBoardSeason::getBeginTime, monthStart)
                .one();
        if (season == null) {
            throw new IllegalStateException("上月赛季不存在，无法归档榜单：" + monthStart);
        }
        Integer seasonId = season.getId();
        // 3.查询榜单数据
        // 3.1.拼接KEY
        String key = RedisConstants.POINTS_BOARD_KEY_PREFIX + lastMonth.format(DateUtils.POINTS_BOARD_SUFFIX_FORMATTER);
        // 3.2.查询数据
        int shardIndex = XxlJobHelper.getShardIndex();
        int shardTotal = XxlJobHelper.getShardTotal();
        String statusKeyPrefix = ARCHIVE_STATUS_KEY_PREFIX + seasonId + ":";
        String shardTotalKey = statusKeyPrefix + "total";
        String expectedShardTotal = String.valueOf(shardTotal);
        Boolean newArchive = stringRedisTemplate.opsForValue().setIfAbsent(shardTotalKey, expectedShardTotal);
        if (newArchive == null || (!newArchive &&
                !expectedShardTotal.equals(stringRedisTemplate.opsForValue().get(shardTotalKey)))) {
            throw new IllegalStateException("赛季归档分片总数发生变化：" + seasonId);
        }
        int pageSize = 10;
        int pageNo = shardIndex+1;
        int skipped = 0;
        TableInfoContext.setInfo(POINTS_BOARD_TABLE_PREFIX + seasonId);
        try {
            while (true) {
                List<PointsBoard> pointsBoards = pointsBoardService.queryCurrentBoardList(key, pageNo, pageSize);
                if (CollUtils.isEmpty(pointsBoards)) break;
                // 名次作为历史榜主键；重跑时只跳过内容完全相同的记录
                skipped += saveOrVerifyBoards(pointsBoards, seasonId);
                pageNo += shardTotal;
            }
        } finally {
            TableInfoContext.remove();
        }
        // 仅在本分片榜单全部写入成功后标记，不包含后续积分明细迁移
        stringRedisTemplate.opsForValue().set(statusKeyPrefix + "shard:" + shardIndex, "done");
        XxlJobHelper.log("赛季" + seasonId + "分片" + shardIndex + "榜单归档完成，重复记录跳过" + skipped + "条");

        // 6.迁移历史赛季积分明细（只在第一个分片执行）
        if (shardIndex == 0 && seasonId != null) {
            try {
                // 获取上个月的起始和结束时间
                LocalDate lastMonthStart = lastMonth.toLocalDate().withDayOfMonth(1);
                LocalDate lastMonthEnd = lastMonthStart.plusMonths(1);
                LocalDateTime beginTime = lastMonthStart.atStartOfDay();
                LocalDateTime endTime = lastMonthEnd.atStartOfDay();
                pointsRecordService.migratePointsRecordToHistory(seasonId, beginTime, endTime);
            } catch (Exception e) {
                log.error("迁移历史赛季积分明细失败，赛季：{}", seasonId, e);
            }
        }
    }

    @XxlJob("clearPointsBoardFromRedis")
    public void clearPointsBoardFromRedis(){
        // 1.获取上月时间
        LocalDateTime lastMonth = LocalDateTime.now().minusMonths(1);
        // 2.计算key
        String key = RedisConstants.POINTS_BOARD_KEY_PREFIX + lastMonth.format(DateUtils.POINTS_BOARD_SUFFIX_FORMATTER);
        LocalDate monthStart = lastMonth.toLocalDate().withDayOfMonth(1);
        PointsBoardSeason season = seasonService.lambdaQuery()
                .eq(PointsBoardSeason::getBeginTime, monthStart)
                .one();
        if (season == null) {
            throw new IllegalStateException("上月赛季不存在，不能清理榜单：" + monthStart);
        }
        String statusKeyPrefix = ARCHIVE_STATUS_KEY_PREFIX + season.getId() + ":";
        String shardTotalText = stringRedisTemplate.opsForValue().get(statusKeyPrefix + "total");
        if (shardTotalText == null) {
            throw new IllegalStateException("赛季归档尚未开始，不能清理榜单：" + season.getId());
        }
        int shardTotal = Integer.parseInt(shardTotalText);
        for (int shardIndex = 0; shardIndex < shardTotal; shardIndex++) {
            if (!"done".equals(stringRedisTemplate.opsForValue().get(statusKeyPrefix + "shard:" + shardIndex))) {
                throw new IllegalStateException("赛季归档分片未完成，不能清理榜单：" + season.getId() + "/" + shardIndex);
            }
        }
        String clearedKey = statusKeyPrefix + "cleared";
        Boolean keyExists = redisTemplate.hasKey(key);
        if (keyExists == null) {
            throw new IllegalStateException("无法确认上月榜单是否存在：" + key);
        }
        // 只有明确记录过清理成功，才能把不存在的Redis Key视为正常重跑
        if (!keyExists && "done".equals(stringRedisTemplate.opsForValue().get(clearedKey))) {
            return;
        }
        Long redisCount = keyExists ? stringRedisTemplate.opsForZSet().zCard(key) : 0L;
        if (redisCount == null) {
            throw new IllegalStateException("无法读取上月榜单人数：" + key);
        }
        TableInfoContext.setInfo(POINTS_BOARD_TABLE_PREFIX + season.getId());
        try {
            long archivedCount = pointsBoardService.count();
            if (archivedCount != redisCount) {
                throw new IllegalStateException("归档人数与Redis榜单不一致：赛季" + season.getId()
                        + "，历史表" + archivedCount + "人，Redis" + redisCount + "人");
            }
        } finally {
            TableInfoContext.remove();
        }
        // 所有分片完成且人数一致后，才能删除上月榜单
        if (keyExists && !Boolean.TRUE.equals(redisTemplate.delete(key))) {
            throw new IllegalStateException("删除上月Redis榜单失败：" + key);
        }
        stringRedisTemplate.opsForValue().set(clearedKey, "done");
    }

    private int saveOrVerifyBoards(List<PointsBoard> boards, Integer seasonId) {
        for (PointsBoard board : boards) {
            board.setId(board.getRank().longValue());
        }
        Map<Long, PointsBoard> existingBoards = queryExistingBoards(boards);
        List<PointsBoard> missingBoards = new ArrayList<>(boards.size());
        int skipped = 0;
        for (PointsBoard board : boards) {
            PointsBoard existing = existingBoards.get(board.getId());
            if (existing == null) {
                missingBoards.add(board);
            } else {
                verifyBoard(existing, board, seasonId);
                skipped++;
            }
        }
        if (missingBoards.isEmpty()) {
            return skipped;
        }
        try {
            if (!pointsBoardService.saveBatch(missingBoards)) {
                throw new IllegalStateException("批量保存历史榜单失败：赛季" + seasonId);
            }
            return skipped;
        } catch (DuplicateKeyException e) {
            // 并发重跑可能写入了同一页，重新核对；仍有缺失时让任务失败并重试
            Map<Long, PointsBoard> latestBoards = queryExistingBoards(boards);
            for (PointsBoard board : boards) {
                PointsBoard existing = latestBoards.get(board.getId());
                if (existing == null) {
                    throw new IllegalStateException("并发归档后仍有缺失名次：赛季" + seasonId
                            + "，名次" + board.getId(), e);
                }
                verifyBoard(existing, board, seasonId);
            }
            return boards.size();
        }
    }

    private Map<Long, PointsBoard> queryExistingBoards(List<PointsBoard> boards) {
        List<Long> ids = new ArrayList<>(boards.size());
        for (PointsBoard board : boards) {
            ids.add(board.getId());
        }
        Map<Long, PointsBoard> existingBoards = new HashMap<>();
        for (PointsBoard board : pointsBoardService.listByIds(ids)) {
            existingBoards.put(board.getId(), board);
        }
        return existingBoards;
    }

    private void verifyBoard(PointsBoard existing, PointsBoard board, Integer seasonId) {
        if (!Objects.equals(existing.getUserId(), board.getUserId())
                || !Objects.equals(existing.getPoints(), board.getPoints())) {
            throw new IllegalStateException("历史榜名次冲突：赛季" + seasonId + "，名次" + board.getId()
                    + "，已有用户" + existing.getUserId() + "，当前用户" + board.getUserId());
        }
    }

    private Integer ensureSeason(LocalDate date) {
        LocalDate monthStart = date.withDayOfMonth(1);
        PointsBoardSeason season = seasonService.lambdaQuery()
                .eq(PointsBoardSeason::getBeginTime, monthStart)
                .one();
        if (season != null) {
            return season.getId();
        }

        season = new PointsBoardSeason()
                .setName(monthStart.getYear() + "年" + monthStart.getMonthValue() + "月赛季")
                .setBeginTime(monthStart)
                .setEndTime(monthStart.plusMonths(1).minusDays(1));
        if (!seasonService.save(season)) {
            throw new IllegalStateException("创建赛季失败：" + monthStart);
        }
        return season.getId();
    }
}
