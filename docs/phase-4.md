# 第 4 阶段：版本化变更、审批与发布边界

当前版本包含版本化变更记录、审批接口、带租约的事务 outbox，以及仅面向隔离本地仓库的 Git 发布适配器。Phase 3 内容改稿工作台已接入变更提交、人工通过/退回和审批历史；CMS 写入与真实部署闭环仍未接入。

## 已实现

- Alembic `0005_change_management` 新增 workspace/site 作用域的 `change_requests`、追加式 `change_revisions`、`change_approvals`、`outbox_events` 和 `publication_attempts`。
- 修订字段限于 `title`、`meta_description`、`body`、`content`、`body_blocks`、`faq`、`internal_links`；其余字段返回 `422`。
- 修订绑定站点页面快照 ID、内容 hash、字段差异和事实版本摘要，并生成规范 SHA-256。客户端 hash 不匹配时返回 `409`。
- 修订仅可引用同一工作区内 `public`、`confirmed` 且当前有效的事实。提交发布预检时会重新检查事实版本、状态和可见性。
- 提交审批与审核决定要求当前变更版本；审核决定绑定当前 `revision_id` 与 `revision_hash` 并保存审核人。创建新修订后，旧审批保留作历史，但不再批准当前修订。
- `pending_approval` 与审批决定在数据库事务中写入 outbox；唯一 `idempotency_key` 支持相同事件的顺序重试去重。
- Alembic `0009_publication_outbox_leases` 为 outbox 增加租约、尝试次数和错误记录，并为发布尝试增加 target、远端标识和唯一幂等键。
- Alembic `0010_deployment_tracking` 为发布尝试保存部署回调、部署 commit、部署时间和复查时间；部署回调必须匹配已提交 commit，复查只在配置的隔离本地 Git 仓库确认对象存在后进入 `verified`。
- Alembic `0011_rollback_attempts` 为发布尝试保存回滚来源、预期当前 commit SHA 和原因；回滚接口只接受已验证部署及匹配的当前 SHA，创建幂等的本地回滚尝试和 outbox 事件，并将来源标记为 `rollback_pending`。
- Phase 3 的 LangGraph 内容任务将结构化草稿保存为上述变更修订。`ContentReviewWorkspace` 显示页面快照、字段差异和事实来源，并可提交审核、通过或退回该版本。
- 发布预检会检查绑定快照仍是该页面最新快照且 hash 未变，并再次检查绑定事实；通过预检仍不会实际写入站点。
- `GitPublisher` 只接受显式登记的本地 Git 仓库和授权相对路径，使用临时 Git worktree、确定性分支名与 commit marker；同一变更、版本和 target 重试会复用已有 commit，revision hash 改变则拒绝复用，不切换调用者的主工作树。
- `POST /api/changes/{id}/publish` 在所有发布前置条件通过后创建唯一幂等键、`PublicationAttempt(status="queued")` 和 `change.publish_requested` outbox 事件。独立 `PublicationWorker` 负责租约、重试和结果落库；未配置 `GIT_PUBLISH_REPOSITORY` 时记录 `not_configured`，不执行外部写入。
- 本地 Git 适配器把受限的变更证据写入 `.trade-visibility/changes/<change_id>/revision-<revision>.json`，作为隔离演示中的可审查 commit；它不是 CMS 页面写入。
- 部署回调、commit 复查和回滚提案均是本地状态接口；隔离本地 Git 仓库中的回滚事件会生成带来源标记的 revert commit，但不会触发远程部署。

## 验证入口

变更字段、修订 hash、审批版本冲突、事实条件和发布预检的测试位于 `backend/tests/test_phase4_changes.py`。内容生成与修订集成另由 `backend/tests/test_content_workflow.py`、`backend/tests/test_content_generation_tasks.py` 覆盖。运行后端完整测试：

```powershell
python -m pytest backend/tests -q
```

验证结果应引用最近一次命令输出；此前的阶段测试计数不是当前代码的验收证据。

## 明确未完成

- 当前没有 CMS/API 页面写入、远程 Git push/PR、真实部署系统回调、线上快照复查或上线后线上冲突处理。`publishing` 只表示本地发布适配器已排队或提交，不能解释为线上 `published`。
- 当前提供的是显式的本地部署回调、Git 对象复查、带 SHA 保护的回滚提案和隔离本地 revert commit；它不连接真实部署系统，也不代表真实网页已上线。线上回滚执行、revert PR 和线上内容 hash 复查仍未实现。
- outbox 已有 worker lease、有限重试和本地 Git 幂等复用；尚未在 PostgreSQL 并发环境或真实远端网络超时中验收，因此不宣称外部 exactly-once。
- 已有的是 Phase 3 内容任务内的审核视图，不是通用变更队列或生产发布控制台。
- 工作区范围的业务读写路由已使用 Membership 角色矩阵；`X-Local-User` 仅用于本地显式 actor assertion，不能替代生产登录或可信 IdP/gateway。生产级多租户隔离和身份系统仍未验收，公开/引导接口保持公开。
- 内容任务具备字段结构、白名单和数字声明检查，但没有覆盖所有自然语言声明的完整语义事实审查。生成草稿与人工审批都不代表发布授权。

后续需在隔离 Git 仓库中验证发布 Worker 的进程崩溃恢复，并单独实现远端 PR、真实部署回查、revert PR 和线上回滚执行；任何真实站点写入仍需独立授权。
