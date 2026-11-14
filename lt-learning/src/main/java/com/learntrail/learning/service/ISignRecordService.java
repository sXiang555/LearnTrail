package com.learntrail.learning.service;

import com.learntrail.learning.domain.po.SignRecord;
import com.baomidou.mybatisplus.extension.service.IService;
import com.learntrail.learning.domain.vo.SignResultVO;

import java.util.List;

/**
 * <p>
 * 签到记录表 服务类
 * </p>
 *
 * @author sxiang
 */
public interface ISignRecordService extends IService<SignRecord> {


    SignResultVO addSignRecords();


    List<Integer> querySignRecords();
}
