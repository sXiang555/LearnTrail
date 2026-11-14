# v5 检索实验

实验日期：2026-11-13。数据版本：`learntrail-eval-5.0.0`。

[数据清单](../dataset-v5.json) · [150条问题](../cases-v5.jsonl) · [19篇语料](../corpus-v5.json) · [Agent介绍](../../README.md#评测与实验)

## 数据与指标

150条问题中，65条保留历史RAG问题和相关性标签，85条依据已发布文章整理；Development／Validation／Test为90／30／30，能力／回归为130／20。语料为Java确认可读的19篇已发布文章，论文资料采用本站导读。

三种方案共用一次隔离PostgreSQL／pgvector索引，各执行150题，共450次生产Retriever搜索，全部执行完成、调用错误为0。每题保存实际Chunk、来源位置、分数与排名。

Recall／MRR／分级NDCG按142条有相关来源的问题计算，8条完全零相关问题单独统计。K=5按Chunk排名计数，同文章重复Chunk不增加文档召回；检索条件通过数包含零相关题的判定。

| 方案 | Recall@5 | MRR@5 | NDCG@5 | 检索条件通过 | 零相关误召回 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Vector | 0.971831 | 0.903404 | 0.913139 | 144/150 | 1/8 |
| BM25 | 0.968310 | 0.928052 | 0.933031 | 137/150 | 8/8 |
| Hybrid · RRF | **0.996479** | **0.948592** | **0.954379** | 141/150 | 8/8 |

Hybrid在本次固定语料和问题上取得最高召回与排序指标；BM25分支带来的零相关误召回仍需优化。语料和题集已扩展，新旧版本分数分别报告。

## 实验参数

| 参数 | 值 |
| --- | --- |
| Embedding | `qwen3.7-text-embedding-flash`，1024维 |
| Chunk长度／重叠 | 1000／100 |
| Top-K／向量相似度阈值 | 5／0.35 |
| BM25 k1／b | 1.2／0.75 |
| RRF k | 60 |
| Reranker | 未启用 |
| 回答生成／Judge调用 | 0／0 |

数据指纹为数据清单、案例文件、语料文件依次拼接后的SHA-256：

```text
5a2adb43ae86fbd5ce8c784829f577a353be526ec08d4113f04d90296dbab640
```

## 复现

在 `learntrail-agent` 目录中准备Java授权文章、PostgreSQL／pgvector、Embedding配置，并通过私有环境变量设置 `LEARNTRAIL_EVAL_TOKEN`：

```bash
# 校验默认v5数据
uv run --locked python -m learntrail_agent.evaluation.runner \
  --mode validate --split all --dataset-version learntrail-eval-5.0.0

# 离线文章快照BM25和证据检查
uv run --locked --group evaluation python -m learntrail_agent.evaluation.runner \
  --mode deterministic --split all

# 生产Retriever三路对照，调用真实Embedding
uv run --locked --group evaluation python -m learntrail_agent.evaluation.runner \
  --mode live --live --split all --schemes vector bm25 hybrid
```

新实验的报告、逐题结果和追踪写入本机忽略目录；上述汇总来自已完成实验。v5的实验范围为检索，工具、Memory、Skills、MCP及HITL由功能回归和历史版本的相应实验覆盖。
