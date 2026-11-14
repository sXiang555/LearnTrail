package com.learntrail.api.client.auth;

import com.learntrail.api.dto.auth.RoleDTO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

import java.util.List;

@FeignClient("lt-auth")
public interface AuthClient {

    @GetMapping("/roles/{id}")
    RoleDTO queryRoleById(@PathVariable("id") Long id);

    @GetMapping("/roles/list")
    List<RoleDTO> listAllRoles();
}
