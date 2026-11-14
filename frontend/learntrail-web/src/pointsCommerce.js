export const orderPhases = { CREATED: '待支付', PAYING: '支付处理中', PAID: '已支付', CANCELLING: '取消处理中', CANCELLED: '已取消' }
export function couponRule(coupon) {
  const threshold = Number(coupon.thresholdAmount) || 0
  const value = Number(coupon.discountValue) || 0
  const cap = Number(coupon.maxDiscountAmount) || 0
  const prefix = threshold > 0 ? '满 ' + threshold + ' 积分，' : ''
  const rule = Number(coupon.discountType) === 2 ? prefix + value / 10 + ' 折' : prefix + '抵扣 ' + value + ' 积分'
  return rule + (Number(coupon.discountType) === 2 && cap > 0 ? '，最多抵扣 ' + cap + ' 积分' : '')
}
export const newOrderKey = () => Array.from(crypto.getRandomValues(new Uint32Array(4)), value => value.toString(16).padStart(8, '0')).join('')
