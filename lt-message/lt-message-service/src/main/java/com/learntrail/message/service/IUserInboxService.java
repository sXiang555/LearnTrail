package com.learntrail.message.service;

import com.learntrail.message.domain.dto.UserInboxDTO;
import com.learntrail.message.domain.dto.UserInboxFormDTO;
import com.learntrail.api.dto.user.UserDTO;
import com.learntrail.message.domain.query.UserInboxQuery;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.message.domain.po.NoticeTemplate;
import com.learntrail.message.domain.po.UserInbox;
import com.baomidou.mybatisplus.extension.service.IService;

import java.util.List;

/**
 * <p>
 * 用户通知记录 服务类
 * </p>
 *
 * @author 虎哥
 * @since 2022-08-19
 */
public interface IUserInboxService extends IService<UserInbox> {

    void saveNoticeToInbox(NoticeTemplate noticeTemplate, List<UserDTO> users);

    PageDTO<UserInboxDTO> queryUserInBoxesPage(UserInboxQuery query);

    Long sentMessageToUser(UserInboxFormDTO userInboxFormDTO);
}
