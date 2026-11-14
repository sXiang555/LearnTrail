package com.learntrail.user.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.learntrail.api.dto.user.LoginFormDTO;
import com.learntrail.api.dto.user.UserDTO;
import com.learntrail.common.domain.dto.LoginUserDTO;
import com.learntrail.user.domain.dto.UserFormDTO;
import com.learntrail.user.domain.po.User;
import com.learntrail.user.domain.vo.UserDetailVO;

/**
 * <p>
 * 学员用户表 服务类
 * </p>
 *
 * @author 虎哥
 * @since 2022-06-28
 */
public interface IUserService extends IService<User> {
    LoginUserDTO queryUserDetail(LoginFormDTO loginDTO, boolean isStaff);

    void resetPassword(Long userId);

    UserDetailVO myInfo();

    void addUserByPhone(User user, String code);

    void updatePasswordByPhone(String cellPhone, String code, String password);

    Long saveUser(UserDTO userDTO);

    void updateUser(UserDTO userDTO);

    void updateUserWithPassword(UserFormDTO userDTO);
}
