package com.learntrail.learning.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.learntrail.api.client.user.UserClient;
import com.learntrail.api.dto.user.UserDTO;
import com.learntrail.common.utils.CollUtils;
import com.learntrail.common.utils.DateUtils;
import com.learntrail.common.utils.UserContext;
import com.learntrail.learning.constants.LearningConstants;
import com.learntrail.learning.constants.RedisConstants;
import com.learntrail.learning.domain.po.PointsBoard;
import com.learntrail.learning.domain.query.PointsBoardQuery;
import com.learntrail.learning.domain.vo.PointsBoardItemVO;
import com.learntrail.learning.domain.vo.PointsBoardVO;
import com.learntrail.learning.mapper.PointsBoardMapper;
import com.learntrail.learning.service.IPointsBoardService;
import com.learntrail.learning.utils.TableInfoContext;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.BoundZSetOperations;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ZSetOperations;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;

import static com.learntrail.learning.constants.LearningConstants.POINTS_BOARD_TABLE_PREFIX;

/**
 * <p>
 * 学霸天梯榜 服务实现类
 * </p>
 *
 * @author 虎哥
 */
@Service
@RequiredArgsConstructor
public class PointsBoardServiceImpl extends ServiceImpl<PointsBoardMapper, PointsBoard> implements IPointsBoardService {

    private final StringRedisTemplate redisTemplate;

    private final UserClient userClient;
    private final StringRedisTemplate stringRedisTemplate;

    @Override
    public PointsBoardVO queryPointsBoardBySeason(PointsBoardQuery query) {
        // 1.判断是否是查询当前赛季
        Long season = query.getSeason();
        Boolean isCurrentSeason = season == null || season == 0;
        // 2.获取Redis的Key
        LocalDateTime now = LocalDateTime.now();
        String key = RedisConstants.POINTS_BOARD_KEY_PREFIX + now.format(DateUtils.POINTS_BOARD_SUFFIX_FORMATTER);
        // 2.查询我的积分和排名
        PointsBoard myBoard = isCurrentSeason ? queryMyCurrentBoard(key) : queryMyHistoryBoard(season);
        // 3.查询榜单列表
        List<PointsBoard> boardList = isCurrentSeason ?
                queryCurrentBoardList(key, query.getPageNo(), query.getPageSize())
                : queryHistoryBoardList(query);
        // 4.封装VO
        // 4.1.处理我的信息
        PointsBoardVO pointsBoardVO = new PointsBoardVO();
        if (myBoard != null) {
            pointsBoardVO.setPoints(myBoard.getPoints());
            pointsBoardVO.setRank(myBoard.getRank());
        }
        if (CollUtils.isEmpty(boardList)) {
            return null;
        }
        // 4.2.查询用户信息
        List<Long> userIds = boardList.stream().map(PointsBoard::getUserId).collect(Collectors.toList());
        List<UserDTO> userDTOS = userClient.queryUserByIds(userIds);
        Map<Long, UserDTO> userMap = new HashMap<>(userDTOS.size());
        if (!CollUtils.isEmpty(userDTOS)) {
            userMap = userDTOS.stream().collect(Collectors.toMap(UserDTO::getId, v -> v));
        }
        // 4.3.转换VO
        List<PointsBoardItemVO> list = new ArrayList<>(boardList.size());
        for (PointsBoard pointsBoard : boardList) {
            PointsBoardItemVO pointsBoardItemVO = new PointsBoardItemVO();
            pointsBoardItemVO.setPoints(pointsBoard.getPoints());
            pointsBoardItemVO.setRank(pointsBoard.getRank());
            pointsBoardItemVO.setName(userMap.get(pointsBoard.getUserId()).getName());
            list.add(pointsBoardItemVO);
        }
        pointsBoardVO.setBoardList(list);
        return pointsBoardVO;
    }

    @Override
    public void createPointsBoardTableBySeason(Integer season) {
        baseMapper.createPointsBoardTable(POINTS_BOARD_TABLE_PREFIX + season);
    }

    private List<PointsBoard> queryHistoryBoardList(PointsBoardQuery query) {
        // 1.计算表名
        String tableName = POINTS_BOARD_TABLE_PREFIX + query.getSeason();
        try {
            // 2.设置动态表名
            TableInfoContext.setInfo(tableName);
            // 3.分页查询，按积分值降序排序（积分高的排在前面）
            Page<PointsBoard> page = lambdaQuery()
                    .orderByDesc(PointsBoard::getPoints)
                    .page(query.toMpPage());
            // 4.数据处理：根据查询结果的位置手动计算排名
            List<PointsBoard> records = page.getRecords();
            if (CollUtils.isEmpty(records)) {
                return Collections.emptyList();
            }
            int rankNo = (query.getPageNo() - 1) * query.getPageSize() + 1;
            for (PointsBoard record : records) {
                record.setRank(rankNo++);
            }
            return records;
        } finally {
            TableInfoContext.remove();
        }
    }

    @Override
    public List<PointsBoard> queryCurrentBoardList(String key, Integer pageNo, Integer pageSize) {
        // 1.计算分页
        int offset = (pageNo - 1) * pageSize;
        // 2.查询
        Set<ZSetOperations.TypedTuple<String>> tuples = stringRedisTemplate
                .opsForZSet().reverseRangeWithScores(key, offset, offset + pageSize - 1);

        int rank = offset+1;
        // 3.封装
        if (CollUtils.isEmpty(tuples)) {
            return Collections.emptyList();
        }
        List<PointsBoard> pointsBoards = new ArrayList<>(tuples.size());
        for (ZSetOperations.TypedTuple<String> tuple : tuples) {
            PointsBoard pointsBoard = new PointsBoard();
            String userId = tuple.getValue();
            Double score = tuple.getScore();
            if (userId == null || score == null) {
                continue;
            }
            pointsBoard.setUserId(Long.valueOf(userId));
            pointsBoard.setPoints(score.intValue());
            pointsBoard.setRank(rank++);
            pointsBoards.add(pointsBoard);
        }
        return pointsBoards;
    }

    private PointsBoard queryMyHistoryBoard(Long season) {
        // 1.获取登录用户
        Long userId = UserContext.getUser();
        // 2.计算表名
        String tableName = POINTS_BOARD_TABLE_PREFIX + season;
        try {
            // 3.查询数据
            // 构建查询条件
            TableInfoContext.setInfo(tableName);
            PointsBoard pointsBoard = lambdaQuery()
                    .eq(PointsBoard::getUserId, userId)
                    .one();
            // 4.数据处理
            TableInfoContext.remove();
            if(pointsBoard !=null){
                pointsBoard.setRank(pointsBoard.getId().intValue());
            }
            return pointsBoard;
        } catch (Exception e) {
            TableInfoContext.remove();
            throw e;
        }
    }

    private PointsBoard queryMyCurrentBoard(String key) {
        // 1.绑定key
        BoundZSetOperations<String, String> ops = stringRedisTemplate.boundZSetOps(key);
        // 2.获取当前用户信息
        String userId = String.valueOf(UserContext.getUser());
        // 3.查询积分
        Double score = ops.score(userId);
        // 4.查询排名
        Long rank = ops.reverseRank(userId);
        // 5.封装返回
        if (score == null || rank == null)
            return null;
        PointsBoard pointsBoard = new PointsBoard();
        pointsBoard.setPoints(score.intValue());
        pointsBoard.setRank(rank.intValue() + 1);
        return pointsBoard;
    }
}
