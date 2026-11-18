# LearnTrail · 学途

**把内容创作、知识学习与 AI 助手连接起来的创作者知识平台。**

LearnTrail 以文章和视频专栏为内容载体，支持创作、积分解锁、进度续播与互动。平台提供学习、创作者和运营三种 AI 助手，共用 Java 登录身份和业务权限。用户可以切换助手，运营人员还可以让运营和创作者助手协作。

![Java](https://img.shields.io/badge/Java-11-437291?logo=openjdk&logoColor=white)
![Spring Boot](https://img.shields.io/badge/Spring_Boot-2.7-6DB33F?logo=springboot&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)
![LangGraph](https://img.shields.io/badge/Agent-LangGraph-1C3C3C)
![PostgreSQL](https://img.shields.io/badge/RAG-pgvector-4169E1?logo=postgresql&logoColor=white)

## 两个技术方向，一个完整系统

| 模块 | 解决的问题 | 阅读入口 |
| --- | --- | --- |
| **LearnTrail Backend** | 身份认证、内容发布、可恢复的分片上传、全文搜索、积分结算与学习权益 | [Java 后端架构](docs/backend-architecture.md) |
| **LearnTrail AI** | 三种业务助手、混合检索、工具调用、工作流恢复与人工审批 | [Agent 专属介绍](learntrail-agent/README.md) |
| **LearnTrail Web** | 统一登录、文章与视频、创作台、个人中心和 AI 助手 | [Vue 3 源码](frontend/learntrail-web/) |

## 整体架构

```mermaid
flowchart TB
    Web[Vue 3 · 统一业务界面] --> Nginx[Nginx · 同源入口]
    Nginx -->|/api| Gateway[Spring Cloud Gateway]
    Nginx -->|/ai| AI[FastAPI · LearnTrail AI]
    Gateway --> Auth[身份与用户]
    Gateway --> Content[文章与视频专栏]
    Gateway --> Media[媒资与分片上传]
    Gateway --> Search[站内全文搜索]
    Gateway --> Learning[学习与积分]
    Gateway --> Trade[交易与优惠券]
    Gateway --> Remark[点赞与互动]
    AI -->|JWT 身份复用 · 业务 API| Gateway
    AI --> Graph[LangGraph · 助手分派与工具调用]
    Graph --> RAG[Vector + BM25 + RRF]
    Graph --> Capabilities[Skills · Memory · MCP · HITL]
    RAG --> PG[(PostgreSQL + pgvector)]
    Capabilities --> PG
    Content & Learning & Trade --> MySQL[(MySQL)]
    Content & Learning & Remark --> Redis[(Redis)]
    Media --> MinIO[(MinIO · 私有媒资)]
    MySQL -->|公开内容 Outbox| Canal[Canal]
    Canal & Remark --> MQ[RabbitMQ · 异步事件]
    MQ --> Content & Learning
    MQ --> Search --> ES[(Elasticsearch)]
    Graph --> Telemetry[Langfuse · OpenTelemetry]
```

Java 负责业务事实与资源权限，Python 通过业务 API 获取内容和学习数据，Vue 统一呈现两套服务。代码、接口契约、数据库迁移与测试在同一仓库维护。

## 平台能力

| 场景 | 实现 |
| --- | --- |
| 内容发现 | 文章和专栏全文搜索、创作者榜单、点赞与浏览／播放量排序 |
| 创作者工作台 | Markdown 文章、视频分片上传与断点续传、异步转码、试看和专栏修订 |
| 积分交易 | 独立钱包、有效学习奖励、积分订单、单张积分券、权益发放和结算恢复 |
| 连续学习 | 视频播放授权、试看校验、进度续播、课表、收藏与讨论 |
| 内容互动 | 文章／专栏点赞与取消、公开计数、版本化异步聚合、浏览／播放去重 |
| AI 助手 | 知识问答与学习进度、稿件润色与大纲、公开内容盘点与选题。运营和协作模式仅对运营人员开放 |
| 可观测与评测 | 请求／模型／工具 Trace、Token 与耗时、检索对照实验、答案与功能契约评测 |

## 界面预览

开发环境真实界面：内容发现、创作者榜单与文章阅读。

<details>
<summary>内容发现与创作者</summary>

![内容发现与创作者榜单](docs/screenshots/discover.png)

</details>

<details>
<summary>文章阅读与点赞</summary>

![文章阅读、来源与点赞](docs/screenshots/article.png)

</details>

## 工程亮点

- **积分交易一致性**：钱包行锁与唯一业务事件控制并发、幂等；扣分、流水、结算凭证和学习权益在同一事务提交，跨服务订单与优惠券通过状态机和恢复任务协调。
- **可靠的互动聚合**：Redis Lua 原子维护点赞关系与统计版本，RabbitMQ 分发快照，MySQL 条件更新防止乱序覆盖，写库完成后按版本确认。
- **可恢复的媒资和搜索链路**。分片状态可从 MinIO 找回，媒体处理任务支持租约恢复。MySQL Outbox 经 Canal、RabbitMQ 同步公开内容到 Elasticsearch。
- **可恢复的 Agent**。LangGraph 按模式分派任务，复用工具执行器。学习助手的审批暂停和恢复以 PostgreSQL 加密 Checkpoint 为基础，Vue 流式显示 Markdown 回答。
- **共享业务权限的 RAG**：发布文章增量索引，向量／BM25 双路召回与 RRF 融合，检索及回答阶段复核 Java 内容权限与来源快照。
- **用实验选择方案**：v5 的 150 条检索题覆盖 19 篇已发布文章，在同一索引上完成 Vector、BM25 与 Hybrid 的 450 次真实搜索，比较召回、排序与零相关误召回。

## 检索实验

当前使用 **v5 / 150 题 / 19 篇文章**，三种方案共享同一隔离索引。以下指标按 142 条有相关来源的问题计算，以 Chunk 为排名单位、K=5，使用 Java 授权正文、1024 维 Embedding 和生产 Retriever；另外 8 条零相关问题单独统计：

| 方案 | Recall@5 | MRR@5 | NDCG@5 |
| --- | ---: | ---: | ---: |
| Vector | 0.9718 | 0.9034 | 0.9131 |
| BM25 | 0.9683 | 0.9281 | 0.9330 |
| Hybrid · RRF | **0.9965** | **0.9486** | **0.9544** |

本轮 Hybrid 的召回与排名指标均高于两条单路。150题×3方案的450次生产检索全部完成，零相关误召回按方案单列。数据、实验范围与复现入口见 [Agent 评测](learntrail-agent/README.md#评测与实验)。

## 代码导航

```text
LearnTrail/
├── README.md                    平台总览
├── docs/
│   ├── backend-architecture.md  Java 后端架构与源码入口
│   ├── agent-architecture.md    Agent 工作流与数据流
│   └── quickstart.md            环境、配置与启动
├── lt-gateway/ lt-auth/ ...      Java 微服务
├── frontend/learntrail-web/      Vue 3 统一前端
├── learntrail-agent/
│   ├── README.md                AI 专属介绍
│   ├── src/ tests/              运行代码与测试
│   ├── migrations/              PostgreSQL 迁移
│   ├── evaluation/              评测数据与契约
│   └── pyproject.toml uv.lock   Python 依赖与锁定
├── database/                    Java 业务增量迁移
└── scripts/                     启动、检查与发布工具
```

## 运行与开发

使用 Java 11、Maven、Node.js 24 和 Python 3.12／uv。Java 业务依赖 MySQL、Redis、RabbitMQ 和 Nacos，AI 使用 PostgreSQL／pgvector。按部署需要启用 MinIO 分片上传及 Elasticsearch／IK 与 Canal 全文索引。

```bash
# Java：显式使用 JDK 11
mvn -DskipTests package
python3 scripts/run_local.py

# AI：在另一个终端配置并启动
cd learntrail-agent
uv sync --locked
uv run --locked uvicorn learntrail_agent.deployment:app --host 0.0.0.0 --port 8100 --no-access-log

# Web：在另一个终端，从仓库根目录进入
cd frontend/learntrail-web
npm ci
npm run dev
```

前端开发入口为 `http://localhost:5173`；默认部署域名为 `www.learntrail.com`，通过同源 `/api`、`/ai` 访问服务。配置模板、基础设施准备、增量迁移顺序和 Nginx 示例见 [快速开始](docs/quickstart.md)。

## 持续演进

平台已打通内容、积分交易、学习记录和 AI 助手。后续围绕检索精排、无答案判断、业务工具扩展、自动化交付与运行监控继续演进。

[参与开发](CONTRIBUTING.md)
