package com.learntrail.trade.service;

import com.learntrail.api.client.course.CourseClient;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.common.utils.UserContext;
import com.learntrail.trade.domain.dto.PlaceOrderDTO;
import com.learntrail.trade.service.impl.OrderServiceImpl;
import org.junit.jupiter.api.*;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class OrderPointsBoundaryTest {
    private final CourseClient courses = mock(CourseClient.class);
    private final OrderServiceImpl service = new OrderServiceImpl(courses, null, null, null, null);
    @BeforeEach void setup() { UserContext.setUser(1L); when(courses.studioUnlockCost(10L)).thenReturn(100); }
    @AfterEach void cleanup() { UserContext.removeUser(); }
    @Test void oldCashPlacementCannotCreateLearnTrailPointsOrder() {
        PlaceOrderDTO form = new PlaceOrderDTO(); form.setCourseIds(List.of(10L)); form.setOrderId(5L);
        assertThrows(BadRequestException.class, () -> service.placeOrder(form));
        verify(courses, never()).getSimpleInfoList(any());
    }
    @Test void oldCashConfirmationCannotTreatPointsAsCurrency() {
        assertThrows(BadRequestException.class, () -> service.prePlaceOrder(List.of(10L)));
        verify(courses, never()).getSimpleInfoList(any());
    }
}
