package com.tianji.learning.controller;

import com.tianji.learning.domain.vo.SignResultVO;
import io.swagger.annotations.ApiOperation;
import org.springframework.web.bind.annotation.*;
import com.tianji.learning.service.ISignRecordService;
import com.tianji.learning.domain.po.SignRecord;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * <p>
 * 签到记录表 控制器
 * </p>
 *
 * @author sxiang
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/sign-records")
public class SignRecordController {

    private final ISignRecordService signRecordService;

    @PostMapping
    @ApiOperation("签到功能接口")
    public SignResultVO addSignRecords() {
        return signRecordService.addSignRecords();
    }

    @GetMapping
    @ApiOperation("查询签到记录")
    public List<Integer> querySignRecords() {
        return signRecordService.querySignRecords();
    }

    


}
