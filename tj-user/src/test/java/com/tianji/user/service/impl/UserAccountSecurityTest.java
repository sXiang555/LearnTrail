package com.tianji.user.service.impl;

import com.tianji.common.exceptions.BadRequestException;
import com.tianji.common.exceptions.UnauthorizedException;
import com.tianji.common.utils.UserContext;
import com.tianji.user.domain.dto.UserFormDTO;
import com.tianji.user.domain.po.User;
import com.tianji.user.domain.po.UserDetail;
import com.tianji.user.service.IUserDetailService;
import org.junit.jupiter.api.*;
import org.mockito.ArgumentCaptor;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.util.ReflectionTestUtils;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class UserAccountSecurityTest {
    private final UserServiceImpl service = spy(new UserServiceImpl());
    private final IUserDetailService details = mock(IUserDetailService.class);
    private final PasswordEncoder encoder = mock(PasswordEncoder.class);
    private UserFormDTO form;

    @BeforeEach
    void setup() {
        UserContext.setUser(2L);
        ReflectionTestUtils.setField(service, "detailService", details);
        ReflectionTestUtils.setField(service, "passwordEncoder", encoder);
        when(details.updateById(any(UserDetail.class))).thenReturn(true);
        form = new UserFormDTO();
        form.setOldPassword(""); form.setPassword(""); form.setName("本人资料");
    }
    @AfterEach
    void cleanup() { UserContext.removeUser(); }

    @Test
    void clientCannotChooseVictimOrRole() {
        form.setId(1L); form.setRoleId(1L); form.setType(1);
        service.updateUserWithPassword(form);
        ArgumentCaptor<UserDetail> saved = ArgumentCaptor.forClass(UserDetail.class);
        verify(details).updateById(saved.capture());
        assertEquals(2L, saved.getValue().getId());
        assertNull(saved.getValue().getType());
        assertNull(saved.getValue().getRoleId());
        verify(service, never()).updateById(any(User.class));
    }

    @Test
    void passwordOnlyDoesNotGenerateEmptyProfileUpdate() {
        User user = new User(); user.setId(2L); user.setPassword("stored-hash");
        doReturn(user).when(service).getById(2L);
        doReturn(true).when(service).updateById(any(User.class));
        when(encoder.matches("old-password", "stored-hash")).thenReturn(true);
        when(encoder.encode("new-password")).thenReturn("new-hash");
        form.setName(null); form.setPassword("new-password"); form.setOldPassword("old-password");
        service.updateUserWithPassword(form);
        verify(service).updateById(argThat(saved -> "new-hash".equals(saved.getPassword())));
        verifyNoInteractions(details);
    }

    @Test
    void wrongOldPasswordDoesNotWriteAnything() {
        User user = new User(); user.setId(2L); user.setPassword("stored-hash");
        doReturn(user).when(service).getById(2L);
        form.setPassword("new-password"); form.setOldPassword("wrong-password");
        assertThrows(BadRequestException.class, () -> service.updateUserWithPassword(form));
        verifyNoInteractions(details);
        verify(service, never()).updateById(any(User.class));
    }

    @Test
    void emptyOldPasswordCannotChangePassword() {
        form.setPassword("new-password");
        assertThrows(BadRequestException.class, () -> service.updateUserWithPassword(form));
        verifyNoInteractions(details, encoder);
    }

    @Test
    void oversizedUtf8PasswordIsRejectedBeforeDatabaseAccess() {
        form.setOldPassword("old-password"); form.setPassword("密".repeat(25));
        assertThrows(BadRequestException.class, () -> service.updateUserWithPassword(form));
        verifyNoInteractions(details, encoder);
    }

    @Test
    void anonymousCannotUpdateProfile() {
        UserContext.removeUser();
        assertThrows(UnauthorizedException.class, () -> service.updateUserWithPassword(form));
        verifyNoInteractions(details, encoder);
    }
}
