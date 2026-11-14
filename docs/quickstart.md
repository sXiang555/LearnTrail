# 快速开始

[平台总览](../README.md) · [Java 后端](backend-architecture.md) · [AI 助手](../learntrail-agent/README.md)

## 0. 下载源码与选择运行方式

```bash
git clone https://github.com/sXiang555/LearnTrail.git
cd LearnTrail
```

也可以在仓库页面点击 **Code → Download ZIP** 下载源码，或使用 **Fork** 在自己的 GitHub 账号下维护修改。

| 目标 | 操作 |
| --- | --- |
| 阅读代码和架构 | 克隆后从根 README、Java 架构和 Agent README 进入对应模块 |
| 本机开发 | 在自己的电脑准备基础设施，启动 Java、Agent 和 Vue 开发服务 |
| 虚拟机联调 | 将数据库、Nacos、Redis、MQ 等部署到自己的虚拟机，配置服务地址与后端注册地址 |
| 部署演示站 | 构建前端，设置 Nginx、域名解析及实际 Java／Agent 上游地址 |

运行位置通过配置决定。使用自己的基础设施地址、数据库和模型凭据；前端开发访问 `http://localhost:5173`，使用 Nginx 时将 LearnTrail 域名解析到自己的 Nginx 地址。

首次部署先准备业务基础表和 Nacos 共享配置，再应用本仓库的增量迁移；下面按 Java、AI、前端的顺序完成配置和启动。

## 1. 准备环境

| 环境 | 版本 / 用途 |
| --- | --- |
| JDK / Maven | JDK11、Maven3.9，构建 Java 服务 |
| Node / npm | Node24，开发与构建 Vue 前端 |
| Python / uv | Python3.12、uv，锁定 AI 运行依赖 |
| MySQL | 业务数据与积分交易 |
| Redis / RabbitMQ | 高频状态、互动聚合与业务事件 |
| Nacos | Java 服务注册与共享配置 |
| PostgreSQL / pgvector | AI 会话、索引、记忆与工作流 |
| Elasticsearch / XXL-JOB | 搜索和调度相关服务 |
| FFmpeg | 使用本地 MP4 上传与试看时需要 |

先准备业务基础库与 Nacos 配置。各服务在 `bootstrap.yml` 中声明数据库、服务名和 `shared-configs`；共享配置通常包括 `shared-spring.yaml`、`shared-redis.yaml`、`shared-mybatis.yaml`、`shared-mq.yaml`、`shared-feign.yaml` 和 `shared-logs.yaml`，按启动模块补齐。

## 2. Java 配置与数据

从仓库根目录创建私有配置：

```bash
cp -n .env.example .env.local
```

填写认证密钥库位置／别名／口令以及需要的媒资配置。数据库、Redis、MQ连接由已有 Nacos共享配置提供。启动器会显式注入 `.env.local`；在 IDE运行时通过对应运行配置注入环境变量。

认证使用外部 JKS。自己的环境可生成独立密钥库，再配置 `.env.local`：

```bash
mkdir -p .local/keys
keytool -genkeypair -alias learntrail-auth -keyalg RSA -keysize 2048 \
  -validity 3650 -storetype JKS -keystore .local/keys/learntrail-auth.jks
```

在已有业务基础表上，备份后按 [database/migrations](../database/migrations/) 的版本顺序应用增量迁移。现有环境先检查实际结构：创建者领域、文章、专栏生命周期、Markdown来源、文章专栏关联、积分交易与内容统计分别对应独立 SQL；已存在的 ALTER结构不重复执行。

本地视频模式通过环境变量指定目录及可执行程序：

```text
LEARNTRAIL_LOCAL_VIDEO_DIR=/absolute/path/to/private-videos
LEARNTRAIL_FFMPEG=/absolute/path/to/ffmpeg
```

## 3. 启动 Java

先确认 `java -version` 与 `mvn -version` 使用 JDK11，再执行：

```bash
mvn -DskipTests package
python3 scripts/run_local.py
```

启动器默认连接 `192.168.150.101` 的基础设施，也可显式指定：

```bash
python3 scripts/run_local.py --infra-host YOUR_INFRA_HOST --register-ip YOUR_BACKEND_IP
```

网关端口10010；课程8086、学习8090、点赞8091、认证8081、用户8082、媒资8084。完整模块与端口映射见 [run_local.py](../scripts/run_local.py)。启动器检查端口并仅管理自己创建的进程。

## 4. 配置并启动 AI

在 PostgreSQL业务实例中准备 AI专用库和账号，由管理员安装pgvector扩展：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

然后在 Python工程中准备环境：

```bash
cd learntrail-agent
uv sync --locked
cp -n .env.example .env
```

配置以下项目，完整参数见 [AI 环境模板](../learntrail-agent/.env.example)：

- `DATABASE_URL`：AI专用 PostgreSQL连接。
- `JAVA_GATEWAY_BASE_URL`：Java网关地址。
- `JWT_PUBLIC_KEY_FILE`：与 Java签名密钥配套的公钥文件。
- `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`：聊天模型。
- `EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL / EMBEDDING_DIMENSIONS`：独立向量模型配置。
- `CHECKPOINT_KEY_FILE`：已有加密Checkpoint对应密钥；新环境首次启动生成。

备份后按 V001–V006顺序执行 [AI migrations](../learntrail-agent/migrations/)；迁移由运行者显式应用。启动：

```bash
# 本机开发
uv run --locked python -m learntrail_agent

# Nginx 访问的部署入口
uv run --locked uvicorn learntrail_agent.deployment:app --host 0.0.0.0 --port 8100 --no-access-log
```

部署入口开放受认证的会话、技能、记忆与MCP路径。LLM、Embedding和可选Judge分开配置；Reranker、Langfuse、OTel按需启用。

## 5. 启动前端

在仓库根目录打开另一个终端：

```bash
cd frontend/learntrail-web
npm ci
npm run dev
```

开发地址为 `http://localhost:5173`；Vite代理 `/api` 至10010、`/ai` 至8100，并使用 `api.learntrail.com` / `www.learntrail.com` 作为认证代理头。

## 6. 域名与 Nginx

默认业务域名：

```text
www.learntrail.com       主站
api.learntrail.com       Java 网关
manage.learntrail.com    创作台入口
```

本机演示时将这些域名解析至 Nginx所在地址；公网部署时配置相应 DNS及HTTPS。站点示例见 [deploy/nginx/learntrail.conf](../deploy/nginx/learntrail.conf)。示例中的上游默认使用loopback，分机部署时替换为实际Java／Agent地址。

生产构建：

```bash
cd frontend/learntrail-web
npm run build
```

将 `dist/` 内容复制至配置的静态目录。Nginx 同源 `/api/` 转发 Java、`/ai/v1/` 转发 AI，SSE关闭代理缓冲。修改配置前保留旧静态文件及配置，`nginx -t`通过后reload。

## 7. 验证

```bash
# 仓库与脚本
python3 scripts/verify_repository.py
python3 -m unittest discover -s scripts/tests -p 'test_*.py'

# Java 源码与测试编译
mvn -DskipTests test-compile

# 前端，在 frontend/learntrail-web 中
npm test
npm run build

# AI，在 learntrail-agent 中
uv run --locked --group evaluation pytest -q
```

AI真实数据库测试通过 `LEARNTRAIL_TEST_DATABASE_URL`使用隔离Schema；Java真实业务测试使用独立测试账号。评测数据和实际模型实验使用独立CLI与预算，详见 [Agent评测](../learntrail-agent/README.md#评测与实验)。
