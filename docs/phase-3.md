# 第 3 阶段：企业事实库与内容改稿

本阶段在事实库和采购问题集基础上，增加受事实约束的内容草稿任务，并将草稿接入版本化变更记录和人工审核工作台。内容草稿不会自动覆盖站点页面，也不会自动发布。

## 企业事实

- `facts` 保存工作区、系列与父版本、主体、属性、值、单位、来源标识和定位、可见性、状态、版本及有效期等信息。版本追加保存，不覆盖历史值。
- 导入事实初始为 `proposed`。事实工作台支持确认或拒绝；只有当前 `confirmed`、`public` 且处于有效期内的事实，才可作为公开改稿的输入。确认事实本身不会更改可见性。
- 改稿记录绑定事实 ID 与版本。任务创建、生成中和发布预检均会检查工作区、版本、可见性和当前有效状态；条件变化时任务不会把该事实作为可用依据继续生成。
- 来源定位随事实版本保存。审核工作台会显示所引用的事实版本；存在 HTTP(S) 定位时可打开来源。

事实接口的 workspace 校验是数据归属检查，不是登录、成员角色或生产授权系统。

## 采购问题与页面绑定

- 采购问题集按站点和工作区管理，问题版本可编辑、冻结；每个版本最多 20 个问题，并保存问题到站点页面的显式映射。
- 内容任务只能基于当前冻结版本创建。所选问题必须属于该版本，目标页面必须是问题的映射页面，并属于同一站点和工作区。
- 创建任务时需绑定最新页面快照的 ID 与内容 hash。若快照已更新，需先刷新页面数据再创建任务。

## 内容生成与审核流程

1. 在“站点与审计”完成页面采集，并准备至少一条当前有效、已确认且可公开的企业事实。
2. 在“采购问题”维护问题及页面映射，冻结当前版本。
3. 在“内容改稿”选择站点、冻结问题版本和页面，为每个问题与页面组合填写本次改稿要求并选择事实依据。每个任务最多包含 5 个不重复组合，改稿要求最多 800 字，且每项至少选择一条事实。
4. 独立 Worker 处理队列。LangGraph 工作流读取事实、生成结构化字段差异、执行约束检查；有限的修复尝试仍未通过，或事实不可用时，任务会进入 `needs_information`，等待业务人员补充或刷新资料。
5. 工作台显示页面快照绑定、字段差异、引用事实版本和来源定位。业务人员检查后可提交人工审核；审核人填写标识并对当前版本作出通过或退回决定，结果保存在版本化变更记录和审批历史中。

生成结果仅是待审建议。审核人仍需核对产品规格、证书、交期、价格、法规与目标市场措辞等业务事实。当前确定性检查覆盖字段白名单、数据结构和数字声明与所选事实的匹配；它不构成对所有自然语言陈述的完整语义事实证明。

## 模型与运行方式

- 默认无网络路径：未设置 `MODEL_GATEWAY_API_KEY` 时，Worker 使用可重复的本地 Fixture gateway；该路径用于合成演示和测试，不调用外部模型。
- 可选模型路径：配置 `MODEL_GATEWAY_BASE_URL`、`MODEL_GATEWAY_API_KEY` 和 `MODEL_GATEWAY_MODEL` 后，Worker 使用 OpenAI-compatible Chat Completions 接口。超时、请求/响应大小和 token 数有配置上限；密钥不会随任务或前端请求传递。
- 内容任务由 `python -m backend.worker` 与页面审计任务共享 Worker 进程和数据库；内容任务有独立租约。LangGraph checkpoint 使用当前 SQLite 或 PostgreSQL 数据库对应的 saver。API 必须先完成迁移并健康，再启动 Worker。
- 主要实现位于 `backend/content_workflow.py`、`backend/content_worker.py`、`backend/model_gateway.py`；迁移 `0008_content_generation_tasks` 增加任务与条目记录。

## 验证入口

内容任务创建、工作流、Worker 恢复和模型网关分别由 `backend/tests/test_content_generation_tasks.py`、`backend/tests/test_content_workflow.py`、`backend/tests/test_content_worker_recovery.py` 和 `backend/tests/test_model_gateway.py` 覆盖。迁移测试位于 `backend/tests/test_migrations.py`。可从仓库根目录运行：

```powershell
python -m pytest backend/tests/test_content_generation_tasks.py backend/tests/test_content_workflow.py backend/tests/test_content_worker_recovery.py backend/tests/test_model_gateway.py backend/tests/test_migrations.py -q
npm --prefix apps/web run build
```

这些是自动化验证入口；具体最近一次完整套件、浏览器端到端和数据库后端结果应以各自最新运行记录为准，不以历史测试计数代替。

## 边界与未完成项

- 当前实现覆盖本地生成、差异查看和人工审批决定。审批通过只更新变更和审批记录，不等于发布。
- 未接入 CMS/API 写入、Git 分支/commit/PR、部署、上线后复查或自动回滚；当前没有自动发布流程。
- 模型输出的约束检查不能取代人工核实所有事实、声明或目标市场合规性。
- 工作区归属检查与审核人文本标识不提供身份认证、角色授权或客户级安全隔离。PostgreSQL 与生产多租户授权仍需单独验证。
