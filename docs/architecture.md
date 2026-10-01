# Trade Visibility Agent 架构图

这张图描述当前仓库已经实现的本地工作流和外部副作用边界。虚线外的真实
Provider、远程 Git/CMS、部署系统和业务数据都需要单独授权；图中不存在“自动
获得排名、引用或询盘”的路径。

```mermaid
flowchart LR
    Browser[React 工作台]
    API[FastAPI API]
    DB[(SQLite 演示 / PostgreSQL)]
    Worker[持久化 Worker\n租约与恢复]
    Audit[确定性审计规则]
    Content[LangGraph 内容工作流\n事实检索与结构化校验]
    Publish[审批与发布 outbox\n本地 Git 隔离适配器]
    Visibility[可见性采样\nfixture / structured HTTP / manual]
    Fixture[本地合成站点]
    Site[已授权 HTTP(S) 站点\n只读快照]
    Provider[已配置的外部 Provider]
    Git[隔离本地 Git 仓库]
    Evidence[快照、事实、diff、审批、原始回答与引用]

    Browser -->|同源 /api| API
    API <--> DB
    API -->|入队| DB
    Worker <--> DB
    Worker --> Audit
    Worker --> Content
    Worker --> Publish
    Worker --> Visibility
    Audit --> Evidence
    Content --> Evidence
    Publish --> Evidence
    Visibility --> Evidence
    Fixture -->|显式 loopback allowlist| Worker
    Site -->|GET、robots、sitemap、有界页面| Worker
    Provider -.->|需 endpoint、凭据与协议| Visibility
    Publish -->|仅隔离本地写入| Git
    DB --> Evidence
```

## 边界与不变量

- 规则只读取冻结页面快照；网络失败保存为 `unknown` 或 `needs_review`。
- 内容生成只能引用同工作区内当前、已确认且可公开的事实；审批绑定修订和
  hash，页面或事实变化会使旧审批失效。
- 外部写入按至少一次执行设计，使用业务幂等记录和发布尝试；checkpoint 不
  代替外部写入的幂等确认。
- 可见性结果区分 `model_api`、`consumer_search_surface` 和 `manual_capture`，
  并保存原始响应、引用、失败原因和成本。fixture 结果始终标记为合成数据。
- 当前发布适配器只写入显式配置的隔离本地 Git 仓库；远程 PR、CMS、部署和
  线上回滚仍是授权后的独立集成。
