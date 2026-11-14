package com.learntrail.user.service.impl;

import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.learntrail.api.client.auth.AuthClient;
import com.learntrail.api.dto.auth.RoleDTO;
import com.learntrail.api.dto.user.LoginFormDTO;
import com.learntrail.api.dto.user.UserDTO;
import com.learntrail.common.domain.dto.LoginUserDTO;
import com.learntrail.common.enums.UserType;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.exceptions.ForbiddenException;
import com.learntrail.common.exceptions.DbException;
import com.learntrail.common.exceptions.UnauthorizedException;
import com.learntrail.common.utils.AssertUtils;
import com.learntrail.common.utils.BeanUtils;
import com.learntrail.common.utils.StringUtils;
import com.learntrail.common.utils.UserContext;
import com.learntrail.user.domain.dto.UserFormDTO;
import com.learntrail.user.domain.po.User;
import com.learntrail.user.domain.po.UserDetail;
import com.learntrail.user.domain.vo.UserDetailVO;
import com.learntrail.user.enums.UserStatus;
import com.learntrail.user.mapper.UserMapper;
import com.learntrail.user.service.ICodeService;
import com.learntrail.user.service.IUserDetailService;
import com.learntrail.user.service.IUserService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.nio.charset.StandardCharsets;

import static com.learntrail.user.constants.UserConstants.*;
import static com.learntrail.user.constants.UserErrorInfo.Msg.*;


/**
 * <p>
 * 学员用户表 服务实现类
 * </p>
 *
 * @author 虎哥
 * @since 2022-06-28
 */
@Service
public class UserServiceImpl extends ServiceImpl<UserMapper, User> implements IUserService {

    @Autowired
    private PasswordEncoder passwordEncoder;
    @Autowired
    private ICodeService codeService;
    @Autowired
    private AuthClient authClient;
    @Autowired
    private IUserDetailService detailService;

    @Override
    public LoginUserDTO queryUserDetail(LoginFormDTO loginDTO, boolean isStaff) {
        // 1.判断登录方式
        Integer type = loginDTO.getType();
        User user = null;
        // 2.用户名和密码登录
        if (type == 1) {
            user = loginByPw(loginDTO);
        }
        // 3.验证码登录
        if (type == 2) {
            user = loginByVerifyCode(loginDTO.getCellPhone(), loginDTO.getPassword());
        }
        // 4.错误的登录方式
        if (user == null) {
            throw new BadRequestException(ILLEGAL_LOGIN_TYPE);
        }
        // 5.判断用户类型与登录方式是否匹配
        if (isStaff ^ user.getType() != UserType.STUDENT) {
            throw new BadRequestException(isStaff ? "非管理端用户" : "非学生端用户");
        }
        // 6.封装返回
        LoginUserDTO userDTO = new LoginUserDTO();
        userDTO.setUserId(user.getId());
        userDTO.setRoleId(handleRoleId(user));
        return userDTO;
    }

    @Override
    public void resetPassword(Long userId) {
        User user = new User();
        user.setId(userId);
        user.setPassword(passwordEncoder.encode(DEFAULT_PASSWORD));
        AssertUtils.isTrue(updateById(user), USER_ID_NOT_EXISTS);
    }

    @Override
    public UserDetailVO myInfo() {
        // 1.获取登录用户id
        Long userId = UserContext.getUser();
        if (userId == null) {
            return null;
        }
        // 2.查询用户
        UserDetail userDetail = detailService.queryById(userId);
        AssertUtils.isNotNull(userDetail, USER_ID_NOT_EXISTS);
        // 3.封装vo
        UserType type = userDetail.getType();
        // 3.1.基本信息
        UserDetailVO vo = BeanUtils.toBean(userDetail, UserDetailVO.class);
        vo.setType(type.getValue());
        // 3.2.详情信息
        switch (type) {
            case STAFF:
                RoleDTO roleDTO = authClient.queryRoleById(userDetail.getRoleId());
                vo.setRoleName(roleDTO == null ? "" : roleDTO.getName());
                break;
            case STUDENT:
                vo.setRoleName(STUDENT_ROLE_NAME);
                break;
            case TEACHER:
                vo.setRoleName(TEACHER_ROLE_NAME);
                break;
            default:
                break;
        }
        return vo;
    }

    @Override
    public void addUserByPhone(User user, String code) {
        // 1.验证码校验
        codeService.verifyCode(user.getCellPhone(), code);
        // 2.判断手机号是否存在
        Integer count = lambdaQuery().eq(User::getCellPhone, user.getCellPhone()).count();
        if (count > 0) {
            throw new BadRequestException(PHONE_ALREADY_EXISTS);
        }
        // 3.加密密码
        user.setPassword(passwordEncoder.encode(user.getPassword()));
        // 4.新增
        user.setUsername(user.getCellPhone());
        save(user);
    }

    @Override
    public void updatePasswordByPhone(String cellPhone, String code, String password) {
        // 1.验证码校验
        codeService.verifyCode(cellPhone, code);
        // 2.查询用户
        User oldUser = lambdaQuery().eq(User::getCellPhone, cellPhone).one();
        if (oldUser == null) {
            // 手机号不存在
            throw new BadRequestException(PHONE_NOT_EXISTS);
        }
        // 2.修改密码
        User user = new User();
        user.setId(user.getId());
        user.setPassword(passwordEncoder.encode(password));
        updateById(user);
    }

    public void updatePhoneById(Long id, String cellPhone) {
        // 1.1.判断是否需要修改手机号
        if (StringUtils.isNotBlank(cellPhone)) {
            // 1.2.需要修改，封装数据
            User user = new User();
            user.setId(id);
            user.setUsername(cellPhone);
            user.setCellPhone(cellPhone);
            // 1.3.修改
            updateById(user);
        }
    }

    @Override
    @Transactional
    public Long saveUser(UserDTO userDTO) {
        UserType type = UserType.of(userDTO.getType());
        // 1.保存用户基本信息
        User user = new User();
        user.setPassword(passwordEncoder.encode(DEFAULT_PASSWORD));
        user.setCellPhone(userDTO.getCellPhone());
        user.setUsername(userDTO.getCellPhone());
        user.setType(type);
        save(user);
        // 2.新增详情
        UserDetail detail = BeanUtils.toBean(userDTO, UserDetail.class);
        detail.setId(user.getId());
        detail.setType(type);
        if(type == UserType.TEACHER){
            detail.setRoleId(TEACHER_ROLE_ID);
        }else{
            if (userDTO.getRoleId() == null) {
                throw new BadRequestException("员工角色信息不能为空");
            }
        }
        detailService.save(detail);
        return user.getId();
    }

    @Override
    @Transactional
    public void updateUser(UserDTO userDTO) {
        // 1.如果传递了手机号，则修改手机号
        String cellphone = userDTO.getCellPhone();
        if(StringUtils.isNotBlank(cellphone)){
            User user = new User();
            user.setId(userDTO.getId());
            user.setCellPhone(cellphone);
            user.setUsername(cellphone);
            updateById(user);
        }
        // 2.修改详情
        UserDetail detail = BeanUtils.toBean(userDTO, UserDetail.class);
        detail.setType(null);
        detailService.updateById(detail);
    }

    @Override
    @Transactional
    public void updateUserWithPassword(UserFormDTO userDTO) {
        Long userId = UserContext.getUser();
        if (userId == null) throw new UnauthorizedException(USER_ID_NOT_EXISTS);
        userDTO.setId(userId);
        if (userDTO.getName() != null && (userDTO.getName().isBlank() || userDTO.getName().length() > 50)) {
            throw new BadRequestException("昵称须为 1–50 个字符");
        }
        if (userDTO.getIntro() != null && userDTO.getIntro().length() > 255) throw new BadRequestException("个人介绍最多 255 个字符");
        if (userDTO.getEmail() != null && userDTO.getEmail().length() > 255) throw new BadRequestException("邮箱地址过长");
        if (StringUtils.isNotBlank(userDTO.getIcon()) && (!userDTO.getIcon().matches("https?://[^\\s]+") || userDTO.getIcon().length() > 255)) {
            throw new BadRequestException("头像请使用有效的 HTTP 或 HTTPS 图片地址");
        }
        // 1.尝试更新密码
        String pw = userDTO.getPassword();
        String oldPw = userDTO.getOldPassword();
        if(StringUtils.isNotBlank(pw)) {
            if (StringUtils.isBlank(oldPw)) throw new BadRequestException("请输入原密码");
            if (pw.length() < 8 || pw.length() > 64 || pw.getBytes(StandardCharsets.UTF_8).length > 72) {
                throw new BadRequestException("新密码须为 8–64 个字符，UTF-8 长度不超过 72 字节");
            }
            // 1.1.查询用户
            User user = getById(userId);
            // 1.2.校验
            if (user == null) {
                throw new UnauthorizedException(USER_ID_NOT_EXISTS);
            }
            // 1.3.校验密码
            if (!passwordEncoder.matches(oldPw, user.getPassword())) {
                // 密码不一致
                throw new BadRequestException("原密码不正确");
            }
            // 1.4.修改密码
            user = new User();
            user.setId(userId);
            user.setPassword(passwordEncoder.encode(pw));
            if (!updateById(user)) throw new DbException("密码修改失败");
        }
        // 自助接口仅接受公开资料字段；纯密码操作不生成空 SET SQL。
        if (userDTO.getName() != null || userDTO.getIcon() != null ||
                userDTO.getEmail() != null || userDTO.getIntro() != null) {
            UserDetail detail = new UserDetail();
            detail.setId(userId);
            detail.setName(userDTO.getName());
            detail.setIcon(userDTO.getIcon());
            detail.setEmail(userDTO.getEmail());
            detail.setIntro(userDTO.getIntro());
            if (!detailService.updateById(detail)) throw new DbException("个人资料保存失败");
        }
    }

    public User loginByPw(LoginFormDTO loginDTO) {
        // 1.数据校验
        String username = loginDTO.getUsername();
        String cellPhone = loginDTO.getCellPhone();
        if (StrUtil.isBlank(username) && StrUtil.isBlank(cellPhone)) {
            throw new BadRequestException(INVALID_UN);
        }
        // 2.根据用户名或手机号查询
        User user = lambdaQuery()
                .eq(StrUtil.isNotBlank(username), User::getUsername, username)
                .eq(StrUtil.isNotBlank(cellPhone), User::getCellPhone, cellPhone)
                .one();
        AssertUtils.isNotNull(user, INVALID_UN_OR_PW);
        // 3.校验是否禁用
        if (user.getStatus() == UserStatus.FROZEN) {
            throw new ForbiddenException(USER_FROZEN);
        }
        // 4.校验密码
        if (!passwordEncoder.matches(loginDTO.getPassword(), user.getPassword())) {
            throw new BadRequestException(INVALID_UN_OR_PW);
        }

        return user;
    }

    private Long handleRoleId(User user) {
        Long roleId = 0L;
        switch (user.getType()) {
            case STUDENT:
                roleId = STUDENT_ROLE_ID;
                break;
            case TEACHER:
                roleId = TEACHER_ROLE_ID;
                break;
            case STAFF:
                UserDetail detail = detailService.getById(user.getId());
                roleId = detail.getRoleId();
                break;
        }
        return roleId;
    }

    public User loginByVerifyCode(String phone, String code) {
        // 1.校验验证码
        codeService.verifyCode(phone, code);
        // 2.根据手机号查询
        User user = lambdaQuery().eq(User::getCellPhone, phone).one();
        if (user == null) {
            throw new BadRequestException(PHONE_NOT_EXISTS);
        }
        // 3.校验是否禁用
        if (user.getStatus() == UserStatus.FROZEN) {
            throw new ForbiddenException(USER_FROZEN);
        }
        return user;
    }
}
