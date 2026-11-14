package com.tianji.user.service;

import com.tianji.common.enums.UserType;
import com.tianji.common.exceptions.*;
import com.tianji.common.utils.UserContext;
import com.tianji.user.domain.po.User;
import com.tianji.user.domain.query.UserPageQuery;
import com.tianji.user.enums.UserStatus;
import org.junit.jupiter.api.*;
import org.mockito.ArgumentCaptor;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class UserOperationsServiceTest {
    private final IUserService users = mock(IUserService.class);
    private final IUserDetailService details = mock(IUserDetailService.class);
    private final UserOperationsService service = new UserOperationsService(users, details);
    @BeforeEach void setup() {
        UserContext.setUser(1L);
        when(users.getById(1L)).thenReturn(new User().setId(1L).setType(UserType.STAFF).setStatus(UserStatus.NORMAL));
    }
    @AfterEach void cleanup() { UserContext.removeUser(); }
    @Test void anonymousAndOrdinaryAccountsCannotListUsers() {
        UserContext.removeUser();
        assertThrows(ForbiddenException.class, () -> service.page(new UserPageQuery()));
        UserContext.setUser(2L);
        when(users.getById(2L)).thenReturn(new User().setType(UserType.STUDENT).setStatus(UserStatus.NORMAL));
        assertThrows(ForbiddenException.class, () -> service.page(new UserPageQuery()));
        verifyNoInteractions(details);
    }
    @Test void frozenStaffCannotOperate() {
        when(users.getById(1L)).thenReturn(new User().setType(UserType.STAFF).setStatus(UserStatus.FROZEN));
        assertThrows(ForbiddenException.class, () -> service.status(2L, 0));
        verify(users, never()).updateById(any(User.class));
    }
    @Test void staffAndSelfAreProtected() {
        when(users.getById(3L)).thenReturn(new User().setType(UserType.STAFF));
        assertThrows(ForbiddenException.class, () -> service.status(1L, 0));
        assertThrows(ForbiddenException.class, () -> service.status(3L, 0));
        verify(users, never()).updateById(any(User.class));
    }
    @Test void changesOnlyRequestedOrdinaryAccountStatus() {
        when(users.getById(2L)).thenReturn(new User().setId(2L).setType(UserType.STUDENT));
        when(users.updateById(any(User.class))).thenReturn(true);
        service.status(2L, 0);
        ArgumentCaptor<User> saved = ArgumentCaptor.forClass(User.class);
        verify(users).updateById(saved.capture());
        assertEquals(2L, saved.getValue().getId()); assertEquals(UserStatus.FROZEN, saved.getValue().getStatus());
        assertNull(saved.getValue().getPassword()); assertNull(saved.getValue().getType());
    }
    @Test void invalidStatusIsRejected() {
        assertThrows(BadRequestException.class, () -> service.status(2L, 9));
        verify(users, never()).updateById(any(User.class));
    }
}
