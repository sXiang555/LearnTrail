package com.tianji.learning.service;

import com.tianji.api.client.course.CourseClient;
import com.tianji.api.client.user.UserClient;
import com.tianji.common.enums.UserType;
import com.tianji.common.exceptions.*;
import com.tianji.common.utils.UserContext;
import com.tianji.common.domain.query.PageQuery;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class DiscussionAccess {
    private final CourseClient courses;
    private final UserClient users;

    public Long user() {
        Long user = UserContext.getUser();
        if (user == null) throw new UnauthorizedException("请先登录");
        return user;
    }
    public void course(Long courseId) {
        user();
        if (courseId == null || !Boolean.TRUE.equals(courses.studioCanDiscuss(courseId))) {
            throw new ForbiddenException("解锁专栏后才能参与讨论，已屏蔽内容暂不开放讨论");
        }
    }
    public void staff() {
        if (!Integer.valueOf(UserType.STAFF.getValue()).equals(users.queryUserType(user()))) {
            throw new ForbiddenException("仅运营人员可管理讨论");
        }
    }
    public boolean student() { return Integer.valueOf(UserType.STUDENT.getValue()).equals(users.queryUserType(user())); }
    public void page(PageQuery query) {
        if (query.getPageNo() == null || query.getPageSize() == null || query.getPageNo() < 1 ||
                query.getPageNo() > 100000 || query.getPageSize() < 1 || query.getPageSize() > 30) {
            throw new BadRequestException("分页参数超出范围");
        }
        query.setSortBy("id"); query.setIsAsc(false);
    }
    public void text(String text, int max) {
        if (text == null || text.isBlank() || text.length() > max) throw new BadRequestException("内容不能为空或超过长度限制");
    }
}
