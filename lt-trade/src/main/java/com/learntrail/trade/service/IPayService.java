package com.learntrail.trade.service;

import com.learntrail.trade.domain.dto.OrderDelayQueryDTO;
import com.learntrail.trade.domain.dto.PayApplyFormDTO;
import com.learntrail.trade.domain.vo.PayChannelVO;

import java.util.List;

public interface IPayService {
    List<PayChannelVO> queryPayChannels();

    String applyPayOrder(PayApplyFormDTO payApply);

    void queryPayResult(OrderDelayQueryDTO message);
}
