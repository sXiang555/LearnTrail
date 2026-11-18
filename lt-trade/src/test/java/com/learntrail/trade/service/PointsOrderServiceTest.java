package com.learntrail.trade.service;

import com.learntrail.api.client.course.CourseClient;
import com.learntrail.api.client.learning.*;
import com.learntrail.api.client.promotion.PointsCouponClient;
import com.learntrail.api.dto.points.PointsTrade;
import com.learntrail.common.exceptions.*;
import com.learntrail.common.utils.UserContext;
import com.learntrail.trade.mapper.PointsOrderMapper;
import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import org.mockito.InOrder;

class PointsOrderServiceTest {
    private final PointsOrderMapper mapper = mock(PointsOrderMapper.class);
    private final PointsOrderStore store = mock(PointsOrderStore.class);
    private final CourseClient courses = mock(CourseClient.class);
    private final LearningClient learning = mock(LearningClient.class);
    private final PointsSettlementClient wallet = mock(PointsSettlementClient.class);
    private final PointsCouponClient coupons = mock(PointsCouponClient.class);
    private final PointsOrderService service = new PointsOrderService(mapper, store, courses, learning, wallet, coupons);
    private PointsTrade.Order order;
    @BeforeEach void setup() {
        UserContext.setUser(1L); order = new PointsTrade.Order(); order.setOrderId(5L); order.setUserId(1L);
        order.setKind("COURSE"); order.setTargetId(10L); order.setUserCouponId(7L); order.setPayablePoints(80); order.setPhase("PAYING");
        when(store.begin(eq(5L), eq(1L), anyBoolean())).thenReturn(order); when(mapper.find(5L, 1L)).thenReturn(order);
        PointsTrade.Receipt receipt = new PointsTrade.Receipt(); receipt.setStatus("CAPTURED"); receipt.setAmount(80);
        when(wallet.pay(5L)).thenReturn(receipt); when(wallet.cancel(5L)).thenReturn(receipt);
    }
    @AfterEach void cleanup() { UserContext.removeUser(); }
    @Test void paymentOrdersReservationDebitAndWriteOffBeforeFinalState() {
        service.pay(5L);
        InOrder in = inOrder(coupons, wallet, store);
        in.verify(store).begin(5L, 1L, false); in.verify(coupons).reserve(5L);
        in.verify(wallet).pay(5L); in.verify(coupons).complete(5L); in.verify(store).finish(5L, 1L, true);
    }
    @Test void timeoutAfterDebitDoesNotFalselyCloseOrReleaseCoupon() {
        doThrow(new IllegalStateException("timeout")).when(coupons).complete(5L);
        assertThrows(IllegalStateException.class, () -> service.pay(5L));
        verify(store, never()).finish(anyLong(), anyLong(), anyBoolean()); verify(coupons, never()).release(anyLong());
    }
    @Test void debitFailureLeavesRecoverableOrderWithoutFalseSuccess() {
        when(wallet.pay(5L)).thenThrow(new BadRequestException("积分不足"));
        assertThrows(BadRequestException.class, () -> service.pay(5L));
        verify(coupons, never()).complete(anyLong()); verify(store, never()).finish(anyLong(), anyLong(), anyBoolean());
    }
    @Test void cancellationAfterCaptureRecoversPaidOrderInsteadOfReleasingCoupon() {
        service.cancel(5L); verify(coupons).complete(5L); verify(store).finish(5L, 1L, true);
        verify(coupons, never()).release(anyLong());
    }
    @Test void unpaidCancellationFencesWalletBeforeReleasingCoupon() {
        PointsTrade.Receipt receipt = new PointsTrade.Receipt(); receipt.setStatus("CANCELLED"); when(wallet.cancel(5L)).thenReturn(receipt);
        service.cancel(5L); InOrder in = inOrder(wallet, coupons, store);
        in.verify(store).begin(5L, 1L, true); in.verify(wallet).cancel(5L);
        in.verify(coupons).release(5L); in.verify(store).finish(5L, 1L, false);
    }
    @Test void repeatedPaidOrderDoesNotCallWalletAgain() {
        order.setPhase("PAID"); service.pay(5L); verifyNoInteractions(wallet, coupons);
    }
    @Test void recoveryFinishesCapturedOrderWithoutReservingOrDebitingAgain() {
        when(wallet.receipt(5L)).thenReturn(receipt("CAPTURED", 80));
        service.resume(order);
        InOrder calls = inOrder(wallet, coupons, store);
        calls.verify(wallet).receipt(5L);
        calls.verify(coupons).complete(5L);
        calls.verify(store).finish(5L, 1L, true);
        calls.verifyNoMoreInteractions();
    }
    @Test void recoveryReleasesCouponAfterReadingCancellationReceipt() {
        order.setPhase("CANCELLING");
        when(wallet.receipt(5L)).thenReturn(receipt("CANCELLED", 0));
        service.resume(order);
        verify(coupons).release(5L);
        verify(store).finish(5L, 1L, false);
        verify(wallet, never()).cancel(5L);
    }
    @Test void recoveryLeavesOrderPendingWhenReceiptDoesNotMatch() {
        when(wallet.receipt(5L)).thenReturn(receipt("CAPTURED", 79));
        assertThrows(IllegalStateException.class, () -> service.resume(order));
        verifyNoInteractions(store, coupons);
    }
    private PointsTrade.Receipt receipt(String status, int amount) {
        PointsTrade.Receipt receipt = new PointsTrade.Receipt();
        receipt.setOrderId(order.getOrderId());
        receipt.setUserId(order.getUserId());
        receipt.setKind(order.getKind());
        receipt.setTargetId(order.getTargetId());
        receipt.setStatus(status);
        receipt.setAmount(amount);
        return receipt;
    }
    @Test void requestKeyCannotBeReusedForDifferentContent() {
        order.setRequestKey("request-key"); when(mapper.request(1L, "request-key")).thenReturn(order);
        PointsTrade.Create form = new PointsTrade.Create(); form.setRequestKey("request-key"); form.setKind("COURSE"); form.setTargetId(99L);
        assertThrows(BadRequestException.class, () -> service.create(form)); verifyNoInteractions(courses, coupons, wallet);
    }
    @Test void anonymousOrderRequestsFailBeforePersistence() {
        UserContext.removeUser(); assertThrows(UnauthorizedException.class, () -> service.snapshot(5L)); verifyNoInteractions(mapper);
    }
}
