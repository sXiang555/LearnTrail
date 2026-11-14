-- KEYS[1]：优惠券信息，例如 prs:coupon:10
-- KEYS[2]：用户领取记录，例如 prs:user:coupon:10
-- ARGV[1]：用户ID

-- 1.优惠券缓存是否存在
if redis.call('exists', KEYS[1]) == 0 then
    return 1
end

-- 2.获取Redis服务器当前时间，转换成毫秒
local redisTime = redis.call('time')
local now = tonumber(redisTime[1]) * 1000
        + math.floor(tonumber(redisTime[2]) / 1000)

-- 3.校验发放开始时间
local issueBeginTime =
tonumber(redis.call('hget', KEYS[1], 'issueBeginTime') or '0')

if now < issueBeginTime then
    return 1
end

-- 4.校验库存
local totalNum =
tonumber(redis.call('hget', KEYS[1], 'totalNum') or '0')

if totalNum <= 0 then
    return 2
end

-- 5.校验发放结束时间
local issueEndTime =
tonumber(redis.call('hget', KEYS[1], 'issueEndTime') or '0')

if now > issueEndTime then
    return 3
end

-- 6.校验用户限领数量
local userLimit =
tonumber(redis.call('hget', KEYS[1], 'userLimit') or '0')

local userClaimed =
tonumber(redis.call('hget', KEYS[2], ARGV[1]) or '0')

if userClaimed >= userLimit then
    return 4
end

-- 7.扣减库存
redis.call('hincrby', KEYS[1], 'totalNum', -1)

-- 8.增加用户领取次数
redis.call('hincrby', KEYS[2], ARGV[1], 1)

return 0
