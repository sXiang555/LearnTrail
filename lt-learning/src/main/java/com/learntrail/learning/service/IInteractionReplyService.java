package com.learntrail.learning.service;

import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.learning.domain.dto.ReplyDTO;
import com.learntrail.learning.domain.po.InteractionReply;
import com.baomidou.mybatisplus.extension.service.IService;
import com.learntrail.learning.domain.query.ReplyPageQuery;
import com.learntrail.learning.domain.vo.ReplyVO;

/**
 * <p>
 * 互动问题的回答或评论 服务类
 * </p>
 *
 * @author 虎哥
 */
public interface IInteractionReplyService extends IService<InteractionReply> {

    void saveReply(ReplyDTO replyDTO);

    PageDTO<ReplyVO> queryReplyPage(ReplyPageQuery pageQuery, boolean forAdmin);

    void hiddenReply(Long id, Boolean hidden);

    ReplyVO queryReplyById(Long id);
}
