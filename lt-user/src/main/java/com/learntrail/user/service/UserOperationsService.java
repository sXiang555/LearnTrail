package com.learntrail.user.service;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.learntrail.common.domain.dto.PageDTO;
import com.learntrail.common.enums.UserType;
import com.learntrail.common.exceptions.*;
import com.learntrail.common.utils.UserContext;
import com.learntrail.user.domain.po.User;
import com.learntrail.user.domain.po.UserDetail;
import com.learntrail.user.domain.query.UserPageQuery;
import com.learntrail.user.enums.UserStatus;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;
import java.util.Objects;

@Service
@RequiredArgsConstructor
public class UserOperationsService {
    private final IUserService users;
    private final IUserDetailService details;

    public void requireStaff() {
        Long id = UserContext.getUser();
        User actor = id == null ? null : users.getById(id);
        if (actor == null || actor.getType() != UserType.STAFF || actor.getStatus() != UserStatus.NORMAL) {
            throw new ForbiddenException("仅正常状态的运营人员可管理用户");
        }
    }

    public PageDTO<Row> page(UserPageQuery query) {
        requireStaff();
        if (query.getPageNo() == null || query.getPageSize() == null || query.getPageNo() < 1 ||
                query.getPageNo() > 100000 || query.getPageSize() < 1 || query.getPageSize() > 30 ||
                (query.getStatus() != null && query.getStatus() != 0 && query.getStatus() != 1) ||
                (query.getName() != null && query.getName().length() > 80)) throw new BadRequestException("查询条件不正确");
        query.setSortBy("u.id"); query.setIsAsc(false);
        Page<UserDetail> page = details.queryUserDetailByPage(query, null);
        return PageDTO.of(page, detail -> {
            Row row = new Row(); row.setId(detail.getId()); row.setUsername(detail.getUsername());
            row.setName(detail.getName()); row.setStatus(detail.getStatus()); row.setCreateTime(detail.getCreateTime());
            row.setType(detail.getType() == null ? null : detail.getType().getValue());
            return row;
        });
    }

    @Transactional
    public void status(Long id, int status) {
        requireStaff();
        if (status != 0 && status != 1) throw new BadRequestException("状态须为 0 或 1");
        User target = users.getById(id);
        if (target == null) throw new BadRequestException("用户不存在");
        if (Objects.equals(id, UserContext.getUser()) || target.getType() == UserType.STAFF) {
            throw new ForbiddenException("此入口不能修改本人或员工状态");
        }
        User update = new User(); update.setId(id); update.setStatus(UserStatus.of(status));
        if (!users.updateById(update)) throw new DbException("用户状态更新失败");
    }

    @Data
    public static class Row {
        private Long id;
        private String username;
        private String name;
        private Integer type;
        private Integer status;
        private LocalDateTime createTime;
    }
}
