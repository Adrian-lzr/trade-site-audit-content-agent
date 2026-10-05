# 第 1 阶段：运行与验收状态

本文按当前仓库实现记录可用操作、已完成的针对性验证及尚未验收的内容（最后核对：2026-10-01）。进程拆分为 API、Worker、可选 Fixture 和 Web；本地任务消费要求 API 与 Worker 连接同一个数据库。

## 当前能力

- `apps/web` 是 React/Vite 工作台，默认端口 `5173`，将 `/api` 代理到 `127.0.0.1:8000`。
- FastAPI 入口为 `uvicorn backend.app:app --reload --port 8000`；启动时由 `init_db()` 应用 Alembic 迁移并确保 `demo-workspace` 存在。
- 持久任务由独立命令 `python -m backend.worker` 消费。先等待 API 完成迁移并通过 health 检查，再启动 Worker；API 不在 lifespan 中启动 Worker，Worker 也不执行数据库迁移。
- `backend/models.py` 定义 Workspace、Site、Page、PageSnapshot、Job、审计结果、版本化 Fact、变更审批、版本化采购问题集、内容生成任务、发布 outbox、Visibility 数据，以及 workspace Membership 和追加式 AuditEvent；首次迁移为 `backend/migrations/versions/0001_initial.py`，当前迁移 head 以仓库 Alembic migration graph 为准（当前为 `0019_workflow_review_events`）。
- Fixture 命令为 `python -m backend.fixture_server --port 8765`，只监听本机 loopback；注册 loopback 站点需要在 API 与 Worker 进程显式设置 `ALLOW_LOOPBACK=true`。
- 默认数据库为 SQLite。根 `docker-compose.yml` 只启动 PostgreSQL，不启动 API、Worker 或 Web。
- 根 `Makefile` 提供 `api`、`worker`、`fixture`、`web`、`db-migrate`、`test`、`web-build` 和 `health` 入口；长运行目标仍需分别在终端启动。
- 健康检查同时提供直接端点 `/health` 和兼容端点 `/api/health`；页面摘要接口返回 `rule_count`、`rule_problem_count`、`rule_review_count`、`rule_unknown_count`，分别表示规则总数、失败、需人工复核和未知结果数量。

## 阶段验收

| 项目 | 已有证据 | 尚未验收 |
| --- | --- | --- |
| Python 测试 | Python 3.12 venv 使用 `backend/requirements.lock`；当前完整运行 `backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider backend/tests -q` 为 **248 passed**（2026-10-05 复跑）。此前阶段的 **144 passed** 为历史基线，覆盖审计、工作区作用域路由、多页进度统计、事实可见性、CSV 批量导入整批回滚、知识库来源边界、英中双语主题检索与改稿要求驱动的来源选择、审批版本冲突、采购问题集、内容生成、页面快照上下文、快照 hash 漂移、过期内容租约、租约失效时禁止 revision 写入、隔离 Git 发布器、发布/回滚 outbox Worker、revert 冲突保护、可见性 Provider 边界、预算条件预占/固定精度结算、HTTP request ID/脱敏、容器静态门禁、全 API workspace 角色检查、事实/采购问题集/revision 的身份绑定审计和 Worker 生命周期审计 | 测试通过不代表 PostgreSQL 生产并发压力或自然租约过期已验收 |
| SQLite / PostgreSQL 迁移 | 空库可从 `0001` 升到当前 repository head（目前为 `0019_workflow_review_events`）；旧事实由迁移保守回填为 `internal_only`，另含租约、合成来源标记、版本化规则、事实、带租约 outbox、部署状态、回滚尝试元数据、版本化采购问题集、页面映射、内容生成任务、Visibility 预算快照字段、workspace 角色约束、追加式审计事件及快照 artifact/parser 元数据。历史 PostgreSQL 迁移记录仅覆盖 `0001→0008`；此前 `0001→0014` 隔离迁移/备份演练为历史证据，不能替代当前 head 验证 | PostgreSQL 生产并发压力和生产备份策略仍未验收 |
| 本地演示 | 2026-09-28 站点审计演示由独立 API、Worker、Fixture、Web 进程完成；任务 succeeded，页面快照带 hash 与 `is_synthetic=true`，并产生 `TITLE_MISSING`。2026-09-29 内容改稿 E2E 使用 loopback Fixture 和 `%TEMP%` SQLite；无主题匹配的 comparison 项进入 `needs_information` 且不保存 revision，复数 `materials` 命中事实后进入 `awaiting_review` | 仅为本机隔离 SQLite 演示，不代表生产部署验收。审核后 `content_generation_items.status` 保留生成时的 `awaiting_review`，即使关联变更已 rejected；UI 会同时显示待审标记和退回记录 |
| 真实 HTTPS 只读检测 | 在已获站点访问授权、限定采集范围和遵守访问频率的前提下，2026-09-28 对 `https://example.com` 完成一次只读检测：job `succeeded`、HTTP `200`、内容 hash `ff67a9d764d6a2367a187734e697f6a53217db9a21c101d410a113ca871a299d`，12 条规则均执行，其中 1 条为 `needs_review` | 这是一次授权范围内的可复现采样，不代表搜索引擎收录、排名、AI 引用或业务增长；真实客户站点需重新确认授权和范围 |
| API 兼容入口与页面摘要 | 2026-09-29 隔离演示中 `/health` 返回 `status: ok`，并通过 loopback TCP 探测动态报告 `fixture_available: true`；Web 内容改稿页保持 API 已连接；`/api/health` 与页面列表摘要的兼容检查为 2026-09-28 记录 | 未在 PostgreSQL 浏览器演示中复核 |
| Worker 租约 | 单元测试覆盖未过期租约保留、过期任务重排、旧 token 无法提交快照；执行审计时会在 robots、sitemap 和页面请求前阶段续租；进程级测试在抓取中强制终止 Worker、将租约标记为过期，再启动独立 Worker 恢复并确认只产生一份快照。页面 upsert 回归测试仅在 SQLite 上验证 | 未等待真实 5 分钟租约自然过期；续租发生在阶段请求之间，单次超长网络请求仍可能超过租约。PostgreSQL upsert 分支及并发压力仍未运行验证 |
| SSRF 边界 | scheme/host/port 校验、编码 traversal、越界或策略不允许的重定向拒绝、已解析 IP 固定连接测试通过；允许范围内的重定向会继续跟随 | DNS rebinding 与完整 SSRF 安全验证未完成；transport 使用受锁文件约束的 httpcore 网络后端接口 |
| Web 构建与浏览器 | 2026-09-30 `npm --prefix apps/web run build` 通过。隔离 API `8003` 与 Web `5176` 的 `/api/knowledge` 均返回 29 条唯一记录，其中 4 条无外部来源；面板显示 `29/29`，筛选 `阀门` 后为 `5/29`，展开内部执行器清单可见“无外部来源”，不显示来源链接。桌面 1440×900 的 `scrollWidth=1440`，移动 390×844 的 `scrollWidth=390`，均无横向溢出；控制台 0 error / 0 warning。全 API 25 个非空 `source_url`（24 个唯一 URL）逐条 GET 均为 HTTP `200`、最终 URL 未变化，未发现明显 404 标题；`https://schema.org/Product` 未提供可读页面标题。截图：[阀门资料桌面](../output/playwright/knowledge-review-valve-29entries-desktop-20260930.png)、[阀门资料移动](../output/playwright/knowledge-review-valve-29entries-mobile-20260930.png)。同日较早的英国筛选截图记录 21 条版本，属于历史状态：[桌面](../output/playwright/knowledge-review-desktop-final-20260930.png)、[移动](../output/playwright/knowledge-review-mobile-final-20260930.png)。2026-09-29 的内容审核与 11 条知识面板检查也为历史记录：当时来源链接 HEAD 均返回 `200`、移动视口无溢出且控制台无错误或警告；截图：[扩展知识面板桌面](../output/playwright/phase1-knowledge-expanded-desktop.png)、[扩展知识面板移动](../output/playwright/phase1-knowledge-expanded-mobile.png)、[历史内容审核桌面](../output/playwright/phase1-python-review-desktop-20260929.png)、[历史内容审核移动](../output/playwright/phase1-python-review-mobile-20260929.png)。1440px/390px 通用响应式检查和事实导入滚动检查为 2026-09-28 历史结果 | 窄屏快照表格独立横向滚动；PostgreSQL 未参与浏览器演示 |
| 运行入口与基础设施 | 根 `Makefile` 已提供 API、Worker、Fixture、Web、迁移、测试、构建和 health 命令；`.env.example` 列出 SQLite/PostgreSQL 配置边界；README 包含 PostgreSQL/SQLite 备份恢复步骤 | 本机未发现 `make`/`gmake`，且没有可用 WSL 发行版，因此 Make target 未执行；需在安装 GNU Make 的目标开发机验证 shell 解析 |
| PostgreSQL | Docker 29.8.1、Compose 5.5.1 可用；设置 `POSTGRES_PASSWORD` 后默认和 `demo` profile 的 Compose 配置渲染均通过。独立无卷临时库已真实完成 `0001→0014` 迁移，并完成 `pg_dump` 到空目标库的恢复和版本、marker、0014 字段校验；现有项目容器和命名卷未触碰 | PostgreSQL 生产并发压力、生产备份策略、生产身份和批准的生产镜像来源仍未验收；本地镜像构建与隔离 Compose consumption 已通过，Docker Hub token 失败记录保留 |

上表是阶段记录，不代表尚未复测的内容已经通过。遇到过期记录时，应以相应代码当前状态和最近一次验证结果为准。

2026-09-28 历史只读复核：API `8001` 的 `/health` 返回 `status: ok`；工作台 `5174` 的 API 代理请求成功。已登记的 `https://example.com` 有 2 个既有审计 job，均为 `succeeded`，各采集 1 页、HTTP `200`，执行 12 条规则并各有 1 条 `needs_review`；两份快照内容 hash 相同。当日未发起新的公网采集请求，也未调用发布接口。演示工作区的事实 API 返回 1 条已确认事实，其 `visibility` 为 `internal_only`。

2026-09-29 后端完整测试为 **70 passed、0 warnings**；同日 `npm --prefix apps/web run build` 通过。Playwright 通过真实 API 和 Web 进程验证 CSV 上传：桌面/移动事实入口截图为 [桌面图](../output/playwright/phase1-fact-import-desktop-20260929.png)、[移动图](../output/playwright/phase1-fact-import-upload-mobile-20260929.png)，上传后新增 2 条 `proposed` 事实且移动视口无横向溢出。随后重新加载 API 验证 `/api/knowledge` 返回 11 条带市场范围和复核时效的外部指导；扩展知识面板截图见上表。测试使用临时 SQLite；PostgreSQL 迁移和页面 upsert 曾在独立 `trade-visibility` 容器通过，当前 Docker daemon 状态见上表。本轮内容审核截图为 [桌面图](../output/playwright/phase1-python-review-desktop-20260929.png) 与 [移动图](../output/playwright/phase1-python-review-mobile-20260929.png)。

2026-09-30 当前工作树复核已同步工作区必填参数、兼容路由归属校验、内容 Worker revision 持久化前租约校验及前端对应 API 调用；知识库扩展至 29 条，Worker 会把本次改稿要求用于检索。随后新增本地 Git 发布器、发布/回滚 outbox 租约与 Worker、部署回调/本地 commit 复查和带 SHA 保护的 revert commit，并为隔离仓库提交路径补充测试。完整后端命令复跑为 **105 passed、0 warnings**，`npm --prefix apps/web run build` 通过，`git diff --check` 通过。Playwright 在隔离临时 SQLite API 上复核 29 条知识、阀门筛选和无外链内部条目，以及桌面/移动布局；当前视口无横向溢出、控制台无错误或警告。对当前 25 个非空来源 URL 的只读 GET 均返回 HTTP 200；真实站点仍未执行发布。该记录未把 PostgreSQL `0009`、`0010`、`0011` 迁移、并发压力和生产备份恢复写成通过证据。

2026-10-01 历史门禁复跑为 **113 passed**；当时 `0013_visibility_budget_snapshots` 为 Alembic head。随后阶段性门禁为 **118 passed**、**123 passed**、**127 passed**、**129 passed**、**140 passed**、**144 passed**，当前完整门禁为 **248 passed**（2026-10-05 复跑），当前 head 为 `0019_workflow_review_events`；新增模型边界、本地 readiness、HTTP 可观测性、运行治理、容器静态门禁、生产验收闸门、全 API workspace 角色授权、关键业务操作身份绑定审计和 Worker 生命周期审计针对性测试通过。Web 构建、评测测试、Compose 默认/demo profile 配置渲染、当前 head 的 PostgreSQL API/checkpoint/concurrency smoke、隔离备份恢复演练和 `git diff --check` 均有记录；本地应用镜像构建和隔离 Compose API/Worker/Web/Fixture consumption 已通过；批准的生产镜像来源、生产身份、生产运行和备份策略仍未验收。两个站点的只读采样及规则证据分别记录在 Phase 6 报告和 `output/online-audit/` 中。

## 本地启动

从仓库根目录创建虚拟环境并安装 Python 依赖（推荐使用精确锁文件）：

```powershell
uv venv --python 3.12 .venv
.venv\Scripts\Activate.ps1
uv pip install --python .venv\Scripts\python.exe -r backend\requirements.lock
```

终端 A 启动 API，终端 B 启动 Worker；两者应使用相同 `DATABASE_URL`：

```powershell
uvicorn backend.app:app --reload --port 8000
```

```powershell
python -m backend.worker
```

默认 SQLite 文件为仓库目录下的 `backend.db`，API startup 会运行迁移。独立执行迁移可用 `python -m alembic -c backend/alembic.ini upgrade head`；根目录也提供 `make db-migrate`。

API 启动后执行 `Invoke-RestMethod http://127.0.0.1:8000/health`（或 `make health`），确认 `status: ok` 后再启动 Worker。API 和 Worker 必须使用同一个 `DATABASE_URL`，否则任务会停留在 API 所连接数据库的队列中。

在 `apps/web` 目录启动工作台：

```powershell
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173`。Web `/api` 代理到 `127.0.0.1:8000`。

停止 API 和 Worker 后，SQLite 演示可直接复制 `backend.db` 备份；恢复时替换文件并重新启动 API。PostgreSQL 的 `pg_dump`/`psql` 命令见根目录 README。备份可能包含 HTML 和审计证据，不要提交到仓库。

## Fixture 流程

终端 A 启动本机静态 Fixture（该进程不读取 `ALLOW_LOOPBACK`）：

```powershell
python -m backend.fixture_server --port 8765
```

终端 B 在开启 loopback 的环境启动 API：

```powershell
$env:ALLOW_LOOPBACK = "true"
uvicorn backend.app:app --reload --port 8000
```

终端 C 使用同一数据库并开启 loopback 启动 Worker：

```powershell
$env:ALLOW_LOOPBACK = "true"
python -m backend.worker
```

创建站点、审计并检查结果：

```powershell
$site = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/sites -ContentType 'application/json' -Body (@{ workspace_id = 'demo-workspace'; name = 'local fixture'; origin = 'http://127.0.0.1:8765'; allowed_paths = @('/'); is_synthetic = $true } | ConvertTo-Json)
$run = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/sites/$($site.id)/audit-runs?workspace_id=demo-workspace" -ContentType 'application/json' -Body '{}'
do { Start-Sleep -Seconds 1; $run = Invoke-RestMethod "http://127.0.0.1:8000/api/audit-runs/$($run.id)?workspace_id=demo-workspace" } while ($run.status -in @('queued', 'running'))
Invoke-RestMethod "http://127.0.0.1:8000/api/sites/$($site.id)/pages?workspace_id=demo-workspace"
```

Fixture 首页是合成样本，刻意没有 `<title>`；演示预期为 `title: null` 并产生一条 `TITLE_MISSING` finding。浏览器工作台也可以执行相同的站点登记和审计操作。

## 真实 HTTPS 只读检测

在获得站点所有者或运营方明确授权、确认允许采集的路径和访问频率后，可以将公开 HTTPS 站点作为只读审计目标。审计只读取 HTML、robots.txt、sitemap 和有界站内链接，不执行发布、写入或内容修改。

2026-09-28 的授权演示对 `https://example.com` 成功完成一个 job：状态为 `succeeded`，目标页面 HTTP 状态为 `200`，页面内容 hash 为 `ff67a9d764d6a2367a187734e697f6a53217db9a21c101d410a113ca871a299d`；冻结的规则结果共 12 条，其中 1 条状态为 `needs_review`。该记录证明一次只读采样链路可运行，不证明搜索引擎收录、排名、AI 引用或询盘增长。

## 针对性检查

```powershell
backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider backend/tests -q
npm --prefix apps/web run build
```

越界 URL 与 IP 固定连接的针对性测试位于 `backend/tests/test_crawler.py`。该测试范围不等价于完整 SSRF 安全保证。`backend/tests/test_worker_process_recovery.py` 会在抓取中终止独立 Worker 进程，并通过推进租约到期时间加速恢复验证；真实 5 分钟自然过期和 PostgreSQL 环境仍未验证。Worker 在阶段请求之间续租；若单次请求本身超过租约，旧 Worker 的提交仍会因 token 失效而被拒绝，任务随后可由新 Worker 恢复。

## 第 1 阶段边界

当前工作区范围的业务读写路由已启用本地 Membership/角色检查：`read`、`operate`、`review`、`admin` 权限由数据库中的角色决定，跨工作区身份键会被拒绝；`LOCAL_AUTH_MODE=required`（或 `strict`）时受检查路由缺少身份上下文返回 `401`。默认 `demo` 模式仍允许匿名本地演示；健康检查、知识库和创建工作区等公开/引导接口保持公开。

`X-Local-User` 等请求头只是本地 actor 断言，不是身份认证凭据；Membership 的 `user_id` 与 AuditEvent 的 `actor` 在没有受信任身份系统时仍不是认证结果。生产认证、授权令牌和用户目录仍需后续接入受信任的 IdP 或网关。PageSnapshot 的 `artifact_uri` 和 `parser_version` 只记录外部 artifact/parser 元数据，HTML 仍受现有快照生命周期约束。

本阶段交付站点登记、受限 HTTP 采集、持久化审计任务、HTML 快照、确定性规则证据和本地工作台。事实库已在第 3 阶段以独立纵向完成，详见 [第 3 阶段状态](phase-3.md)。第 4 阶段现有变更草稿、版本化审批、带租约 outbox、隔离本地 Git 提交适配器，以及带当前部署 SHA 校验的本地回滚提案；CMS/远程 PR、真实部署、线上回滚执行和可见性监测仍未验收，详见[第 4 阶段状态](phase-4.md)。真实站点继续保持只读，不把本地 `submitted` 或 `not_configured` 入口当成线上已发布能力。
