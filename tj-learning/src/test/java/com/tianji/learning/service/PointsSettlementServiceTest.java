package com.tianji.learning.service;

import com.tianji.api.client.course.CourseClient;
import com.tianji.api.client.trade.PointsOrderClient;
import com.tianji.api.client.promotion.PointsCouponClient;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.common.exceptions.*;
import com.tianji.common.utils.UserContext;
import com.tianji.learning.mapper.*;
import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class PointsSettlementServiceTest {
    private final PointsSettlementMapper mapper = mock(PointsSettlementMapper.class);
    private final StudioPointsMapper points = mock(StudioPointsMapper.class);
    private final StudioPointsService wallet = mock(StudioPointsService.class);
    private final PointsOrderClient orders = mock(PointsOrderClient.class);
    private final PointsCouponClient coupons = mock(PointsCouponClient.class);
    private final CourseClient courses = mock(CourseClient.class);
    private final PointsSettlementService service = new PointsSettlementService(mapper, points, wallet, orders, coupons, courses);
    private PointsTrade.Order order;
    private PointsTrade.Reservation reserved;
    @BeforeEach void setup() {
        UserContext.setUser(1L);
        order = new PointsTrade.Order(); order.setOrderId(5L); order.setUserId(1L); order.setKind("COURSE");
        order.setTargetId(10L); order.setTitle("专栏"); order.setPhase("PAYING");
        order.setTotalPoints(100); order.setDiscountPoints(20); order.setPayablePoints(80); order.setUserCouponId(7L);
        reserved = new PointsTrade.Reservation(); reserved.setOrderId(5L); reserved.setUserId(1L);
        reserved.setUserCouponId(7L); reserved.setDiscountPoints(20); reserved.setStatus("RESERVED");
        when(orders.snapshot(5L)).thenReturn(order); when(coupons.reservation(5L)).thenReturn(reserved);
        when(wallet.lockWallet(1L)).thenReturn(1000); when(courses.studioUnlockCost(10L)).thenReturn(100);
    }
    @AfterEach void cleanup() { UserContext.removeUser(); }
    private PointsTrade.Receipt receipt(String status) {
        PointsTrade.Receipt receipt = new PointsTrade.Receipt(); receipt.setOrderId(5L); receipt.setUserId(1L);
        receipt.setAmount(80); receipt.setStatus(status); return receipt;
    }
    @Test void serverPriceAndCouponProduceOneDebitAndRealEntitlement() {
        when(points.validLesson(1L, 10L)).thenReturn(null, 11L);
        assertEquals(80, service.pay(5L).getAmount());
        verify(wallet).changeBalance(1L, -80); verify(points).addEntry(1L, "UNLOCK", 10L, "专栏", -80);
        verify(points).grantLesson(anyLong(), eq(1L), eq(10L)); verify(mapper).insert(any());
    }
    @Test void duplicatePaymentReturnsReceiptWithoutDebiting() {
        when(mapper.receipt(5L, 1L)).thenReturn(receipt("CAPTURED"));
        assertEquals("CAPTURED", service.pay(5L).getStatus());
        verify(wallet, never()).changeBalance(anyLong(), anyInt()); verify(mapper, never()).insert(any());
        verifyNoInteractions(coupons);
    }
    @Test void cancelledReceiptFencesLatePayment() {
        when(mapper.receipt(5L, 1L)).thenReturn(receipt("CANCELLED"));
        assertThrows(BadRequestException.class, () -> service.pay(5L));
        verify(wallet, never()).changeBalance(anyLong(), anyInt());
    }
    @Test void insufficientBalanceCannotCreateLedgerOrEntitlement() {
        when(wallet.lockWallet(1L)).thenReturn(79);
        assertThrows(BadRequestException.class, () -> service.pay(5L));
        verify(wallet, never()).changeBalance(anyLong(), anyInt()); verify(mapper, never()).insert(any());
        verify(points, never()).grantLesson(anyLong(), anyLong(), anyLong());
    }
    @Test void forgedOrMissingReservationCannotPay() {
        reserved.setUserId(9L);
        assertThrows(BadRequestException.class, () -> service.pay(5L));
        verify(wallet, never()).changeBalance(anyLong(), anyInt());
    }
    @Test void changedPriceCannotBePaidFromStaleOrder() {
        when(courses.studioUnlockCost(10L)).thenReturn(null);
        assertThrows(BadRequestException.class, () -> service.pay(5L));
        verify(wallet, never()).changeBalance(anyLong(), anyInt());
    }
    @Test void capturedPaymentWinsCancellationWithoutRefundOrLoss() {
        order.setPhase("CANCELLING"); when(mapper.receipt(5L, 1L)).thenReturn(receipt("CAPTURED"));
        assertEquals("CAPTURED", service.cancel(5L).getStatus());
        verify(wallet, never()).changeBalance(anyLong(), anyInt()); verify(mapper, never()).insert(any());
    }
    @Test void cancellingUnpaidOrderCreatesTerminalFence() {
        order.setPhase("CANCELLING"); assertEquals("CANCELLED", service.cancel(5L).getStatus());
        verify(mapper).insert(argThat(value -> value.getAmount() == 0 && "CANCELLED".equals(value.getStatus())));
    }
    @Test void foreignOrderAndAnonymousRequestsAreRejected() {
        order.setUserId(9L); assertThrows(BadRequestException.class, () -> service.pay(5L));
        UserContext.removeUser(); assertThrows(UnauthorizedException.class, () -> service.receipt(5L));
    }
}
