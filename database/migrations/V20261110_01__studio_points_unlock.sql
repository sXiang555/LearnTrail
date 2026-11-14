USE tj_course;
ALTER TABLE learntrail_series ADD COLUMN unlock_points INT UNSIGNED NOT NULL DEFAULT 100;

USE tj_learning;
CREATE TABLE IF NOT EXISTS learntrail_article (
    id BIGINT NOT NULL PRIMARY KEY,
    slug VARCHAR(80) NOT NULL UNIQUE,
    title VARCHAR(100) NOT NULL,
    summary VARCHAR(240) NOT NULL,
    body TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learntrail_point_wallet (
    user_id BIGINT NOT NULL PRIMARY KEY,
    balance INT UNSIGNED NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learntrail_point_entry (
    user_id BIGINT NOT NULL,
    kind VARCHAR(12) NOT NULL,
    target_id BIGINT NOT NULL,
    title VARCHAR(120) NOT NULL,
    amount INT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, kind, target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO learntrail_article (id, slug, title, summary, body) VALUES
(1, 'trace-a-request', '跟踪一次微服务请求', '用一个请求 ID 找出请求经过的服务和真正的失败点。',
'排查跨服务请求时，先记录请求 ID、发生时间和入口路径。网关日志告诉你请求有没有被拦截，以及它被转发到了哪个服务。

接着沿同一个请求 ID 检查业务服务的入参校验和数据库日志。如果请求依赖另一个服务，区分连接失败、超时与对方返回的业务错误。相同的前端提示可能对应完全不同的原因。

最后缩小复现范围。在相同账号下重试最短的操作链路，确认问题出现在哪一跳，再决定改代码、配置还是数据。'),
(2, 'reliable-async-events', '异步消息失败时怎么排查', '分清生产、路由和消费三个阶段，各自需要什么证据。',
'业务数据库更新成功，只说明本地事务已经完成。要定位异步消息问题，先确认生产者有没有发送记录，再核对交换机、路由键和队列绑定。

如果消息已经到队列，查看消费者的确认、异常和重试日志。以订单号等业务标识关联记录，区分消息根本没有消费和消息消费后下游写入失败。

设计补偿时，要让重复执行同一笔业务产生同一个结果。用数据库唯一约束或带状态条件的更新保护写入，再通过可查询的记录决定是否需要重试。'),
(3, 'design-access-control', '从试看走到完整观看', '理解试看、购买权益与播放凭据之间的关系。',
'看得到专栏标题、封面和分集目录，并不代表用户拥有完整播放权。服务端需要先确认当前账号和分集的归属，再检查专栏是否开放或账号是否有有效权益。

试看应该签发指向试看资源的短期凭据。把原片地址交给浏览器再用界面倒计时，会让客户端获得超出试看范围的内容。

测试时可以准备两个账号。让一个账号有完整权益，另一个只允许试看。除了验证页面按钮，还要直接请求非试看分集，确认服务端会拒绝。'),
(4, 'ship-a-small-change', '上线前怎么验证一个小改动', '用一份可复验的记录把本机通过和部署通过分开。',
'在开发环境先写出用户动作和可观察的结果。以积分解锁为例，可以记录初始余额、首次阅读后的余额，以及扣分后能否打开原先锁定的分集。

部署前确认后端实例、数据库版本和前端静态文件来自同一轮构建。新的页面如果仍连到旧后端，按钮看起来正常，但请求可能命中不存在的接口。

最后从实际入口用独立账号再走一遍流程。记录入口、使用的数据、状态变化和异常分支，方便别人按相同步骤复验。');
