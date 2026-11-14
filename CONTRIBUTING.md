# 参与 LearnTrail

从[平台总览](README.md)、[Java 后端架构](docs/backend-architecture.md)和[Agent 介绍](learntrail-agent/README.md)了解模块职责，按[快速开始](docs/quickstart.md)准备开发环境。

## 提交改动

围绕明确的业务场景提交，描述触发条件、原行为与改后结果。服务接口、DTO、消息事件和数据库结构变化需要同步更新相关调用方与版本化迁移。

使用 Java11、Vue3 Composition API与JavaScript、Python3.12和uv；依赖变更同时维护对应锁文件。

## 验证

```bash
# 仓库根目录
python3 scripts/verify_repository.py
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
mvn -DskipTests test-compile
```

在 `frontend/learntrail-web` 执行 `npm test`、`npm run build`；在 `learntrail-agent` 执行 `uv run --locked --group evaluation pytest -q`。按改动选择业务测试，涉及数据库语义时使用隔离测试环境。

## 配置与数据

通过环境变量和私有配置提供凭据。环境模板提交空值或示例变量；真实 `.env`、`.local`、认证密钥、数据备份、运行日志和模型实验产物保留在本机。

## 公开源码准备

```bash
python3 scripts/prepare_github.py --dry-run
python3 scripts/prepare_github.py --init-git
```

默认从已提交的 `HEAD` 生成 `.local/github/LearnTrail`，可用 `--ref` 指定版本；保留完整构建模块、测试、迁移与公开文档，排除开发交接资料和旧Git历史。不修改工作区、不覆盖已有目录、不自动提交或推送。
