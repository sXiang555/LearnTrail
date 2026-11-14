package com.learntrail.trade.service;

import com.learntrail.api.dto.points.PointsTrade;
import com.learntrail.common.exceptions.BadRequestException;
import com.learntrail.trade.mapper.PointsOrderMapper;
import org.junit.jupiter.api.*;
import java.time.LocalDateTime;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class PointsOrderStoreTest {
    private final PointsOrderMapper mapper = mock(PointsOrderMapper.class);
    private final IOrderService original = mock(IOrderService.class);
    private final PointsOrderStore store = new PointsOrderStore(mapper, original);
    private PointsTrade.Order order;
    @BeforeEach void setup() {
        order = new PointsTrade.Order(); order.setOrderId(5L); order.setUserId(1L); order.setRequestKey("request-key");
        order.setKind("COURSE"); order.setTargetId(10L); order.setTitle("专栏"); order.setPhase("CREATED");
        order.setTotalPoints(100); order.setDiscountPoints(20); order.setPayablePoints(80); order.setUserCouponId(7L);
        order.setCreatedAt(LocalDateTime.now()); order.setCoverUrl("https://example.test/cover.png");
        when(mapper.insert(order)).thenReturn(1); when(mapper.find(5L, 1L)).thenReturn(order); when(mapper.lock(5L, 1L)).thenReturn(order);
    }
    @Test void originalOrderAndDetailsHaveZeroCashAmountsAndRealCover() {
        store.create(order);
        verify(original).saveOrderAndDetails(argThat(value -> "POINTS".equals(value.getPayChannel())
                        && value.getTotalAmount() == 0 && value.getRealAmount() == 0 && value.getDiscountAmount() == 0),
                argThat(details -> details.size() == 1 && details.get(0).getPrice() == 0
                        && details.get(0).getRealPayAmount() == 0 && order.getCoverUrl().equals(details.get(0).getCoverUrl())));
    }
    @Test void couponOrderAlsoSatisfiesOriginalRequiredCoverColumn() {
        order.setKind("COUPON"); order.setCoverUrl(null); store.create(order);
        verify(original).saveOrderAndDetails(any(), argThat(details -> "".equals(details.get(0).getCoverUrl()) && details.get(0).getCourseId() == 0));
    }
    @Test void duplicateRequestDoesNotCreateOriginalOrderAgain() {
        when(mapper.insert(order)).thenReturn(0); when(mapper.request(1L, "request-key")).thenReturn(order);
        assertSame(order, store.create(order)); verifyNoInteractions(original);
    }
    @Test void paidTerminalStateDoesNotRewritePaymentTime() {
        order.setPhase("PAID"); store.finish(5L, 1L, true);
        verify(mapper, never()).phase(anyLong(), anyString()); verify(mapper, never()).orderStatus(anyLong(), anyInt(), anyString());
    }
    @Test void paidOrderCannotBeCancelled() {
        order.setPhase("PAID"); assertThrows(BadRequestException.class, () -> store.begin(5L, 1L, true));
    }
}
