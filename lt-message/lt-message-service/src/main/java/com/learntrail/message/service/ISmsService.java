package com.learntrail.message.service;

import com.learntrail.api.dto.sms.SmsInfoDTO;
import com.learntrail.api.dto.user.UserDTO;
import com.learntrail.message.domain.po.NoticeTemplate;

import java.util.List;

public interface ISmsService {
    void sendMessageByTemplate(NoticeTemplate noticeTemplate, List<UserDTO> users);

    void sendMessage(SmsInfoDTO smsInfoDTO);

    void sendMessageAsync(SmsInfoDTO smsInfoDTO);
}
