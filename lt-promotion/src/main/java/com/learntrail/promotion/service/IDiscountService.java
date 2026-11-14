package com.learntrail.promotion.service;

import com.learntrail.api.dto.promotion.CouponDiscountDTO;
import com.learntrail.api.dto.promotion.OrderCouponDTO;
import com.learntrail.api.dto.promotion.OrderCourseDTO;

import java.util.List;

public interface IDiscountService {
    List<CouponDiscountDTO> findDiscountSolution(List<OrderCourseDTO> orderCourses);

    CouponDiscountDTO queryDiscountDetailByOrder(OrderCouponDTO orderCouponDTO);
}
