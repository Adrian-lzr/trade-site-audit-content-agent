# 第 4 阶段：版本化变更、审批与发布边界

当前版本包含版本化变更记录、审批接口和事务 outbox。Phase 3 内容改稿工作台已接入变更提交、人工通过/退回和审批历史；Git 发布器、CMS 写入与部署闭环仍未接入。

## 已实现

- Alembic `0005_change_management` 新增 workspace/site 作用域的 `change_requests`、追加式 `change_revisions`、`change_approvals`、`outbox_events` 和 `publication_attempts`。
- 修订字段限于 `title`、`meta_description`、`body`、`content`、`body_blocks`、`faq`、`internal_links`；其余字段返回 `422`。
- 修订绑定站点页面快照 ID、内容 hash、字段差异和事实版本摘要，并生成规范 SHA-256。客户端 hash 不匹配时返回 `409`。
- 修订仅可引用同一工作区内 `public`、`confirmed` 且当前有效的事实。提交发布预检时会重新检查事实版本、状态和可见性。
- 提交审批与审核决定要求当前变更版本；审核决定绑定当前 `revision_id` 与 `revision_hash` 并保存审核人。创建新修订后，旧审批保留作历史，但不再批准当前修订。
- `pending_approval` 与审批决定在数据库事务中写入 outbox；唯一 `idempotency_key` 支持相同事件的顺序重试去重。
- Phase 3 的 LangGraph 内容任务将结构化草稿保存为上述变更修订。`ContentReviewWorkspace` 显示页面快照、字段差异和事实来源，并可提交审核、通过或退回该版本。
- 发布预检会检查绑定快照仍是该页面最新快照且 hash 未变，并再次检查绑定事实；通过预检仍不会实际写入站点。

## 验证入口

变更字段、修订 hash、审批版本冲突、事实条件和发布预检的测试位于 `backend/tests/test_phase4_changes.py`。内容生成与修订集成另由 `backend/tests/test_content_workflow.py`、`backend/tests/test_content_generation_tasks.py` 覆盖。运行后端完整测试：

```powershell
python -m pytest backend/tests -q
```

验证结果应引用最近一次命令输出；此前的阶段测试计数不是当前代码的验收证据。

## 明确未完成

- `GitPublisher` 未配置。`POST /api/changes/{id}/publish` 仅校验批准状态、当前快照与事实条件，并记录 `PublicationAttempt(status="not_configured")`；不会创建分支、commit、PR，也不会写入站点或 CMS。
- outbox 当前只有事务落库和顺序幂等键；没有 dispatcher/Worker 消费、lease、崩溃恢复或发布端去重，因此不是并发 exactly-once 发布流程。
- 已有的是 Phase 3 内容任务内的审核视图，不是通用变更队列或发布控制台。尚无部署成功回调、线上快照复查、rollback/revert PR 或上线后冲突处理。
- 审核人由表单填写标识；当前没有登录、角色权限或生产级工作区授权。数据归属检查不等于认证或成员授权。
- 内容任务具备字段结构、白名单和数字声明检查，但没有覆盖所有自然语言声明的完整语义事实审查。生成草稿与人工审批都不代表发布授权。

后续需在隔离 Git 仓库中单独实现并验证 Publisher/outbox Worker、远端超时核对、崩溃恢复和部署回查；任何真实站点写入仍需独立授权。
