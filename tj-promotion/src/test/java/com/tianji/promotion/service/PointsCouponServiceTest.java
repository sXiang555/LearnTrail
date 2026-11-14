package com.tianji.promotion.service;

import com.tianji.api.client.course.CourseClient;
import com.tianji.api.client.user.UserClient;
import com.tianji.api.client.trade.PointsOrderClient;
import com.tianji.api.client.learning.PointsSettlementClient;
import com.tianji.api.dto.points.PointsTrade;
import com.tianji.common.exceptions.*;
import com.tianji.common.utils.UserContext;
import com.tianji.promotion.domain.po.*;
import com.tianji.promotion.enums.*;
import com.tianji.promotion.mapper.*;
import org.junit.jupiter.api.*;
import java.time.LocalDateTime;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class PointsCouponServiceTest {
    private final PointsCouponMapper mapper = mock(PointsCouponMapper.class);
    private final CouponMapper coupons = mock(CouponMapper.class);
    private final UserCouponMapper userCoupons = mock(UserCouponMapper.class);
    private final CouponScopeMapper scopes = mock(CouponScopeMapper.class);
    private final PointsOrderClient orders = mock(PointsOrderClient.class);
    private final PointsSettlementClient wallet = mock(PointsSettlementClient.class);
    private final PointsCouponService service = new PointsCouponService(mapper, coupons, userCoupons, scopes, orders, wallet, mock(CourseClient.class), mock(UserClient.class));
    private UserCoupon owned;
    private Coupon coupon;
    private PointsTrade.Coupon template;
    private PointsTrade.Order order;
    private PointsTrade.Quote quote;
    @BeforeEach void setup() {
        UserContext.setUser(1L);
        owned = new UserCoupon().setId(7L).setUserId(1L).setCouponId(100L).setStatus(UserCouponStatus.UNUSED)
                .setTermBeginTime(LocalDateTime.now().minusDays(1)).setTermEndTime(LocalDateTime.now().plusDays(1));
        coupon = new Coupon().setId(100L).setDiscountType(DiscountType.PRICE_DISCOUNT).setDiscountValue(20)
                .setThresholdAmount(100).setMaxDiscountAmount(0).setSpecific(false).setStatus(CouponStatus.ISSUING)
                .setIssueBeginTime(LocalDateTime.now().minusDays(1)).setIssueEndTime(LocalDateTime.now().plusDays(1))
                .setIssueNum(0).setTotalNum(10).setUserLimit(1).setTermDays(30);
        template = new PointsTrade.Coupon(); template.setExchangePoints(0);
        when(userCoupons.selectById(7L)).thenReturn(owned); when(mapper.lockUserCoupon(7L, 1L)).thenReturn(owned);
        when(mapper.lockTemplate(100L)).thenReturn(coupon); when(mapper.template(100L)).thenReturn(template);
        when(mapper.reservedBy(anyLong())).thenReturn(null);
        order = new PointsTrade.Order(); order.setOrderId(5L); order.setUserId(1L); order.setKind("COURSE");
        order.setTargetId(10L); order.setUserCouponId(7L); order.setPhase("PAYING");
        order.setTotalPoints(100); order.setDiscountPoints(20); order.setPayablePoints(80);
        when(orders.snapshot(5L)).thenReturn(order);
        quote = new PointsTrade.Quote(); quote.setUserCouponId(7L); quote.setCourseId(10L); quote.setPoints(100);
    }
    @AfterEach void cleanup() { UserContext.removeUser(); }
    @Test void thresholdCouponUsesIntegerPointsWithoutCurrencyScaling() { assertEquals(20, service.quote(quote)); }
    @Test void rateCouponReusesStrategyAndMaximumDiscount() {
        coupon.setDiscountType(DiscountType.RATE_DISCOUNT).setDiscountValue(80).setMaxDiscountAmount(10);
        assertEquals(10, service.quote(quote));
    }
    @Test void foreignCouponCannotBeQuoted() {
        owned.setUserId(9L); assertThrows(BadRequestException.class, () -> service.quote(quote));
    }
    @Test void expiredAndUsedCouponCannotBeQuoted() {
        owned.setTermEndTime(LocalDateTime.now().minusSeconds(1)); assertThrows(BadRequestException.class, () -> service.quote(quote));
        owned.setTermEndTime(LocalDateTime.now().plusDays(1)).setStatus(UserCouponStatus.USED);
        assertThrows(BadRequestException.class, () -> service.quote(quote));
    }
    @Test void insufficientThresholdAndReservedCouponAreRejected() {
        quote.setPoints(99); assertThrows(BadRequestException.class, () -> service.quote(quote));
        quote.setPoints(100); when(mapper.reservedBy(7L)).thenReturn(99L);
        assertThrows(BadRequestException.class, () -> service.quote(quote));
    }
    @Test void paidCouponCannotUseFreeReceivePath() {
        template.setExchangePoints(10); assertThrows(BadRequestException.class, () -> service.receive(100L));
        verify(coupons, never()).incrIssueNum(anyLong());
    }
    @Test void soldOutCouponDoesNotIssueUserCoupon() {
        coupon.setIssueNum(10); assertThrows(BadRequestException.class, () -> service.receive(100L));
        verify(userCoupons, never()).insert(any());
    }
    @Test void cancelledReservationFencesLatePaymentReservation() {
        PointsTrade.Reservation released = new PointsTrade.Reservation(); released.setStatus("RELEASED");
        when(mapper.reservation(5L, 1L)).thenReturn(released);
        assertThrows(BadRequestException.class, () -> service.reserve(5L));
        verify(mapper, never()).insertReservation(any());
    }
    @Test void cancelBeforeReserveWritesTerminalReservation() {
        PointsTrade.Receipt receipt = new PointsTrade.Receipt(); receipt.setUserId(1L); receipt.setStatus("CANCELLED");
        when(wallet.receipt(5L)).thenReturn(receipt); service.release(5L);
        verify(mapper).insertReservation(argThat(value -> "RELEASED".equals(value.getStatus())));
    }
    @Test void capturedPaymentCannotReleaseCoupon() {
        PointsTrade.Receipt receipt = new PointsTrade.Receipt(); receipt.setUserId(1L); receipt.setStatus("CAPTURED"); receipt.setAmount(80);
        when(wallet.receipt(5L)).thenReturn(receipt);
        assertThrows(BadRequestException.class, () -> service.release(5L)); verify(mapper, never()).reservationStatus(anyLong(), anyString());
    }
    @Test void legacyCurrencyCouponCannotBeUsedAsPointsCoupon() {
        when(mapper.template(100L)).thenReturn(null); assertThrows(BadRequestException.class, () -> service.quote(quote));
    }
    @Test void anonymousRequestsAreDenied() {
        UserContext.removeUser(); assertThrows(UnauthorizedException.class, () -> service.catalog(1, 10));
    }
    @Test void duplicateReservationAfterConcurrentPaymentReturnsWithoutChangingCoupon() {
        order.setPhase("PAID"); service.reserve(5L);
        verifyNoInteractions(mapper, coupons, userCoupons);
    }
}
