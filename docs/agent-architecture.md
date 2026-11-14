# LearnTrail AI 架构

[Agent 专属介绍](../learntrail-agent/README.md) · [平台总览](../README.md)

## 分层与数据归属

| 层次 | 位置 | 职责 |
| --- | --- | --- |
| 部署入口 | `deployment.py` | 对外 API 白名单、生命周期透传 |
| HTTP API | `api_sessions.py`、`api_agent.py`、`api_capabilities.py` | 请求模型、JWT 身份、SSE 与响应协议 |
| 服务 | `services/` | 会话、任务、来源复核、取消与恢复 |
| Agent | `agent/` | StateGraph、工具注册与执行、Skills、策略 |
| RAG | `rag/` | 清洗、分块、增量索引、召回、融合与引用问答 |
| 外部客户端 | `clients/` | 模型、Embedding、Reranker、Java 与 MCP |
| 数据访问 | `storage/` | 会话、索引、Checkpoint、审批、记忆 |
| 评测 | `evaluation/` | 数据契约、执行、指标、Judge 与实验报告 |

上述路径均位于 [learntrail-agent/src/learntrail_agent](../learntrail-agent/src/learntrail_agent/)。Java 管理业务事实，AI PostgreSQL 管理 AI 状态。身份由已验签令牌和 Java 账号状态确定，不从工具参数接收用户身份。

## 工作流与持久化

```mermaid
stateDiagram-v2
    [*] --> select_skill
    select_skill --> memory
    memory --> agent
    agent --> gate: tool_calls
    agent --> verify: answer
    gate --> tools: read_only / approved
    gate --> paused: approval_required
    paused --> gate: decision + resume
    tools --> agent: tool results
    verify --> completed
    completed --> [*]
```

`WorkflowState` 保存消息、选中技能、工具调用与执行状态。LangGraph 使用服务端生成的 thread 映射关联会话；Checkpoint 加密后写入 PostgreSQL。恢复时读取原状态并重新检查归属、审批有效期与资源快照，再继续执行。

模型流、图节点、工具事件和终态通过 SSE 发送。临时内容用于即时呈现，完成事件对应持久化结果；取消与断连进入统一清理逻辑。

## RAG 数据流

```mermaid
flowchart TB
    Article[Java 已授权文章] --> Clean[正文清洗与内容哈希]
    Clean --> Chunk[标题 / 长度分块 · 原文位置]
    Chunk --> Embed[独立 Embedding Client]
    Embed --> Index[(pgvector + Chunk 元数据)]
    Query[用户问题] --> Vector[Vector 检索]
    Query --> BM25[BM25 检索]
    Index --> Vector & BM25
    Vector & BM25 --> RRF[RRF 融合]
    RRF --> Rank[可插拔 Reranker]
    Rank --> Auth[实时 Java 权限复核]
    Auth --> Answer[带引用 JSON 回答]
    Answer --> Verify[原文 / 哈希 / 位置复核]
```

索引指纹绑定正文与 Embedding／Chunk 配置。内容更新切换 generation，重复内容跳过；归档、屏蔽及当前权限变化会影响可返回的证据。引用记录标题、Chunk、Unicode字符位置和内容哈希，连接到站内阅读页及原始来源。

## 工具、Skills 与记忆

工具由 Registry 描述名称、参数模型、权限和超时；Executor 执行参数校验、并发限制、预算和输出截断。工具使用服务端 `ToolContext`，携带经过验证的身份和来源复核能力。

Skills 以 Markdown 元数据声明用途和允许工具，按任务选择后按需加入提示词；长期记忆以当前用户和来源为边界，结合版本、过期和冲突检查检索相关条目。短期记忆按整轮历史控制上下文预算。

MCP Server 提供第一方业务工具，复用相同权限；MCP Client 从显式可信配置加载 stdio／HTTP 服务与允许工具。需要人工确认的调用先持久化审批，再由图暂停，批准后复核资源并恢复。

## 观测与评测闭环

HTTP → Run → 模型／检索／工具／审批通过 Trace 串联。OTel 记录 Span 与指标，Langfuse 接收模型与任务观测；日志和 Trace 使用元数据，不携带完整令牌、密钥或原始私密上下文。

评测 CLI 保存数据集与代码指纹、实验配置、逐题结果和汇总指标，区分数据校验、确定性功能场景、真实检索与真实回答评分。方案选择依据同一评测语料和明确指标口径，详见 [实验与结果](../learntrail-agent/README.md#评测与实验)。
