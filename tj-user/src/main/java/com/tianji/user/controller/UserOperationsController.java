package com.tianji.user.controller;

import com.tianji.common.domain.dto.PageDTO;
import com.tianji.user.domain.query.UserPageQuery;
import com.tianji.user.service.UserOperationsService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/operations/users")
@RequiredArgsConstructor
public class UserOperationsController {
    private final UserOperationsService service;

    @GetMapping
    public PageDTO<UserOperationsService.Row> page(UserPageQuery query) { return service.page(query); }

    @PutMapping("/{id}/status/{status}")
    public void status(@PathVariable Long id, @PathVariable int status) { service.status(id, status); }
}
