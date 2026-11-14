-- KEYS[1]：优惠券信息
-- KEYS[2]：用户领取记录
-- ARGV[1]：用户ID

-- 1.检查是否存在可以补偿的用户领取次数
local userClaimed =
tonumber(redis.call('hget', KEYS[2], ARGV[1]) or '0')

if userClaimed <= 0 then
    return 1
end

-- 2.优惠券缓存存在时恢复库存
-- 暂停或结束后缓存可能已经删除，此时不用恢复Redis库存
if redis.call('exists', KEYS[1]) == 1 then
    redis.call('hincrby', KEYS[1], 'totalNum', 1)
end

-- 3.回退用户领取次数
local remainClaimed =
redis.call('hincrby', KEYS[2], ARGV[1], -1)

-- 4.领取次数归零后删除字段
if remainClaimed == 0 then
    redis.call('hdel', KEYS[2], ARGV[1])
end

return 0