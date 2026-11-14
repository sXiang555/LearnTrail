package com.learntrail.user.service;

import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.user.domain.dto.StudentFormDTO;
import com.learntrail.user.domain.query.UserPageQuery;
import com.learntrail.user.domain.vo.StudentPageVo;

/**
 * <p>
 * 学员详情表 服务类
 * </p>
 *
 * @author 虎哥
 * @since 2022-07-12
 */
public interface IStudentService {

    void saveStudent(StudentFormDTO studentFormDTO);

    void registerAccount(String username, String nickname, String password);

    void updateMyPassword(StudentFormDTO studentFormDTO);

    PageDTO<StudentPageVo> queryStudentPage(UserPageQuery pageQuery);
}
