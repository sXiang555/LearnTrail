package com.learntrail.auth.service;

import com.learntrail.api.client.user.UserClient;
import com.learntrail.auth.service.impl.AccountServiceImpl;
import com.learntrail.auth.util.JwtTool;
import com.learntrail.common.domain.dto.LoginUserDTO;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.utils.UserContext;
import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class AccountRefreshTest {
    @AfterEach void cleanup() { UserContext.removeUser(); }
    @Test void verifiedRefreshIdentityIsRelayedAndRemovedWhenAccountDisabled() {
        JwtTool jwt = mock(JwtTool.class);
        UserClient users = mock(UserClient.class);
        LoginUserDTO identity = new LoginUserDTO(); identity.setUserId(2L);
        when(jwt.parseRefreshToken("signed-refresh")).thenReturn(identity);
        when(users.isAccountEnabled(2L)).thenAnswer(invocation -> {
            assertEquals(2L, UserContext.getUser());
            return false;
        });
        AccountServiceImpl service = new AccountServiceImpl(jwt, users, mock(ILoginRecordService.class));
        assertThrows(BadRequestException.class, () -> service.refreshToken("signed-refresh"));
        assertNull(UserContext.getUser());
        verify(jwt, never()).createToken(identity);
    }
}
