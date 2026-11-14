package com.learntrail.user.controller;


import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.user.domain.dto.StudentFormDTO;
import com.learntrail.user.domain.query.UserPageQuery;
import com.learntrail.user.domain.vo.StudentPageVo;
import com.learntrail.user.service.IStudentService;
import io.swagger.annotations.Api;
import io.swagger.annotations.ApiOperation;
import lombok.Data;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import javax.validation.Valid;
import javax.validation.constraints.NotBlank;
import javax.validation.constraints.Pattern;
import javax.validation.constraints.Size;

/**
 * <p>
 * 学员详情表 前端控制器
 * </p>
 *
 * @author 虎哥
 * @since 2022-07-12
 */
@RestController
@RequestMapping("/students")
@Api(tags = "用户管理接口")
public class StudentController {

    @Autowired
    private IStudentService studentService;

    @ApiOperation("分页查询学生信息")
    @GetMapping("/page")
    public PageDTO<StudentPageVo> queryStudentPage(UserPageQuery pageQuery){
        return studentService.queryStudentPage(pageQuery);
    }

    @ApiOperation("学员注册")
    @PostMapping("/register")
    public void registerStudent(@RequestBody StudentFormDTO studentFormDTO) {
        studentService.saveStudent(studentFormDTO);
    }

    @ApiOperation("LearnTrail 用户名注册")
    @PostMapping("/register/account")
    public void registerAccount(@Valid @RequestBody AccountRegistration form) {
        studentService.registerAccount(form.getUsername(), form.getNickname(), form.getPassword());
    }

    @Data
    public static class AccountRegistration {
        @NotBlank(message = "请输入用户名")
        @Pattern(regexp = "[a-zA-Z][a-zA-Z0-9_]{3,31}", message = "用户名须以字母开头，包含 4–32 个字母、数字或下划线")
        private String username;

        @NotBlank(message = "请输入昵称")
        @Size(max = 50, message = "昵称不能超过 50 个字")
        private String nickname;

        @NotBlank(message = "请输入密码")
        @Size(min = 8, max = 64, message = "密码须为 8–64 个字符")
        private String password;
    }

    @ApiOperation("修改学员密码")
    @PutMapping("/password")
    public void updateMyPassword(@RequestBody StudentFormDTO studentFormDTO) {
        studentService.updateMyPassword(studentFormDTO);
    }
}
