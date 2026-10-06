# Trade Site Audit and Content Agent

本地单工作区工作台目前包含站点只读审计、企业事实确认、采购问题集版本管理，以及基于已确认公开事实生成改稿和人工审批。生成结果保存在版本化变更记录中；当前只支持显式隔离本地 Git 仓库中的审查提交和回滚演练，不包含远程 Git/CMS 发布、真实线上自动写入或持续监控。

## 功能概览

- 对获授权的网站进行只读采集与规则审计，保存页面快照和审计结果。
- 管理有来源的企业事实及版本化采购问题集，生成内容时只使用已确认且可公开的事实。
- 生成可审阅的页面改稿，由人工批准或退回；不会自动写回客户网站。
- 默认通过本地合成 Fixture 演示，不需要模型凭据；可选配置 OpenAI-compatible 模型网关。

## 界面预览

![Trade Visibility 桌面工作台](output/playwright/trade-visibility-desktop.png)

[移动端工作台截图](output/playwright/trade-visibility-mobile.png)

## 运行组件

- API：`backend.app:app`，默认 `http://127.0.0.1:8000`；启动时执行 Alembic 迁移并确保演示工作区存在。
- Worker：独立进程 `python -m backend.worker`，消费持久化的页面审计和内容改稿任务。等 API 完成启动迁移并通过 health 检查后再启动 Worker；两者需要使用同一个 `DATABASE_URL`。
- Fixture：仅供本机演示的静态站点服务，默认 `http://127.0.0.1:8765`。
- Web：Vite 工作台，默认 `http://127.0.0.1:5173`，将 `/api` 请求代理到本机 API。
- 数据库：默认 SQLite（`sqlite:///./backend.db`）；根目录 Compose 只启动 PostgreSQL 16。
- 内容改稿默认使用不访问网络的本地 Fixture gateway。启用 OpenAI-compatible 模型时，需在 Worker 环境显式设置 `MODEL_GATEWAY_BASE_URL`、`MODEL_GATEWAY_API_KEY` 和 `MODEL_GATEWAY_MODEL`；详见[第 3 阶段状态](docs/phase-3.md)。
- 可见性监测默认使用离线 fixture。启用真实的结构化可见性来源时，需在 Worker 环境显式设置 `VISIBILITY_PROVIDER_URL`、`VISIBILITY_PROVIDER_API_KEY` 和可选的 `VISIBILITY_PROVIDER_MODEL`；缺少 endpoint 或凭据时样本状态为 `unavailable`，不会伪装成线上成功。
- API 为每个请求返回 `X-Request-ID` 并输出脱敏的结构化 HTTP 事件日志；Provider request ID、运行 ID、成本和模型元数据会保留用于排查，凭据和原始正文不会进入日志。可通过 `VISIBILITY_MAX_RUN_BUDGET_USD`、`VISIBILITY_DAILY_BUDGET_USD`、`VISIBILITY_RAW_RETENTION_DAYS` 和 `VISIBILITY_RAW_MAX_BYTES` 配置单次/工作区预算及原始证据保留边界。

### 本地授权边界

工作区范围的业务读写路由支持 Membership 角色检查以及跨工作区校验；健康检查、知识库和创建工作区等公开/引导接口除外。默认 `LOCAL_AUTH_MODE=demo` 保留匿名本地演示；设置为 `required` 或 `strict` 时，受检查路由缺少身份上下文返回 `401`，角色不足返回 `403`。`X-Local-User` 等请求头只是本地 actor 断言，不是安全凭据；生产认证和用户目录仍需接入受信任的身份系统。

## 准备环境

需要 Python 3.11+、Node.js、npm 和 `uv`。推荐在仓库根目录创建虚拟环境并安装锁定依赖：

```powershell
uv venv --python 3.12 .venv
.venv\Scripts\Activate.ps1
uv pip install --python .venv\Scripts\python.exe -r backend\requirements.lock
```

`backend/pyproject.toml` 是 Python 包配置，`backend/requirements.lock` 是生成的精确版本依赖清单，包含运行依赖和 `pytest` 开发测试依赖。没有 `uv` 时，可先用可用的 Python 3.11+ 解释器运行 `python -m pip install uv`；或使用 `python -m pip install -e ./backend[dev]` 按 pyproject 中的兼容版本范围解析依赖。本机需使用 Python 3.12 的真实安装路径，默认 `python` 别名可能指向不可运行的 WindowsApps 占位程序。

`.env.example` 只列出配置项，应用不会自动读取该文件。SQLite 快速开始时可不设置 `DATABASE_URL`；使用 Compose PostgreSQL 时，按下文设置同一个 URL 后再启动 API 和 Worker。

## Make 运行入口

仓库根目录的 `Makefile` 为常用命令提供统一入口。每个长运行目标都应在单独终端启动；运行前先激活虚拟环境并执行一次 `npm ci`：

| 目标 | 作用 |
| --- | --- |
| `make api` | 启动 API，包含启动迁移 |
| `make worker` | 启动持久化任务 Worker |
| `make fixture` | 启动本机合成 Fixture（端口 8765） |
| `make web` | 启动 Vite 工作台（端口 5173） |
| `make db-migrate` | 显式执行 Alembic `head` 迁移 |
| `make test` | 运行后端测试 |
| `make web-build` | 构建 Web 静态产物 |
| `make health` | 请求 API `/health`，用于启动后检查 |

在不使用 Make 的 Windows 环境，可直接使用下方等价的 `uvicorn`、`python -m` 和 `npm` 命令。

## 启动本地工作台

从仓库根目录分别打开三个终端。API 自动应用数据库迁移；默认 SQLite 无需 Docker。启动 API 后先执行 `make health` 或 `Invoke-RestMethod http://127.0.0.1:8000/health`，确认直接 `/health` 健康检查返回 `status: ok` 后再启动 Worker。

终端 A：

```powershell
.venv\Scripts\Activate.ps1
uvicorn backend.app:app --reload --port 8000
```

终端 B：

```powershell
.venv\Scripts\Activate.ps1
python -m backend.worker
```

终端 C：

```powershell
cd apps/web
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173`。如果没有安装锁定的虚拟环境，也可以分别使用可用的 Python 环境运行前两个进程。API 和 Worker 都需要能访问同一个数据库。

### PostgreSQL（可选）

需要一次启动 PostgreSQL、API、Worker、Web 和可选本地 Fixture 时，参见[`容器运行说明`](docs/container-runtime.md)；Compose 要求通过环境变量提供 `POSTGRES_PASSWORD`，并默认只绑定本机回环地址。

```powershell
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg://trade_visibility:change-me-local@127.0.0.1:5434/trade_visibility"
python -m alembic -c backend/alembic.ini upgrade head
```

在以上环境变量仍有效的终端分别启动 API 与 Worker。也可以使用 `make db-up` 和 `make db-migrate` 代替对应命令。`docker compose ps` 应显示 PostgreSQL healthcheck 为 `healthy` 后再启动 API；当前仓库只用 Compose 启动数据库，不会自动启动 API、Worker 或 Web。`.env.example` 仅供参考，应用不会自动加载该文件。

PostgreSQL 备份和恢复（停止写入或安排维护窗口后执行）：

```powershell
docker compose exec -T postgres pg_dump -U trade_visibility -d trade_visibility > trade_visibility.sql
Get-Content .\trade_visibility.sql | docker compose exec -T postgres psql -U trade_visibility -d trade_visibility
```

默认 SQLite 演示可在 API 和 Worker 停止后复制 `backend.db` 作为备份；恢复时替换该文件并重新启动 API。备份文件可能包含站点内容和审计证据，不要提交到仓库。

## 本地 Fixture 演示

Fixture 仅绑定 loopback，用于无 key 的确定性演示。需在本地演示用的 API 与 Worker 进程中显式启用 `ALLOW_LOOPBACK`；不要把它当作生产服务。

### 企业事实批量导入

站点工作台的“企业事实”区域提供 CSV 模板下载和批量导入。模板字段为 `subject,predicate,value,unit,source_id,source_locator,visibility,valid_from,valid_until`；单批最多 200 行、文件不超过 1 MB。导入会先完整校验，成功后所有行仍是 `proposed`，不会自动确认或公开；任一行无效时整批回滚。内部资料可在 `source_locator` 中记录文件名和段落/表格定位，只有 HTTP(S) 定位会显示可点击链接。

外部 SEO、来源治理、阀门采购资料和目标市场边界见 [`docs/knowledge-base.md`](docs/knowledge-base.md)。当前 29 条只读 external guidance 可按市场/声明类型查询；Worker 会按采购问题、改稿要求和目标市场选择最多 8 条作为独立的政策/资料边界上下文传给模型，不把它们当企业 Fact。执行器、测试记录、图纸和维护清单属于内部资料完整性启发式，不是外部标准或工程结论；企业规格、证书、价格、交期和市场授权仍须由当前已确认且可公开的工作区事实支持，动态政策及贸易查询入口使用前要重新核验。

终端 A：

```powershell
.venv\Scripts\Activate.ps1
python -m backend.fixture_server --port 8765
```

Fixture 服务自身不读取 `ALLOW_LOOPBACK`。在演示用的 API 与 Worker 进程中分别设置 `$env:ALLOW_LOOPBACK = "true"`。

终端 B（Fixture 模式 API）：

```powershell
.venv\Scripts\Activate.ps1
$env:ALLOW_LOOPBACK = "true"
uvicorn backend.app:app --reload --port 8000
```

终端 C（Worker 使用同一数据库）：

```powershell
.venv\Scripts\Activate.ps1
$env:ALLOW_LOOPBACK = "true"
python -m backend.worker
```

终端 D：运行 Web，步骤同上。Fixture 首页故意缺少 `<title>`，审计预期产生 `TITLE_MISSING`。可在工作台登记 `http://127.0.0.1:8765`，或通过 API 操作：

```powershell
$site = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/sites -ContentType 'application/json' -Body (@{ workspace_id = 'demo-workspace'; name = 'local fixture'; origin = 'http://127.0.0.1:8765'; allowed_paths = @('/'); is_synthetic = $true } | ConvertTo-Json)
$run = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/sites/$($site.id)/audit-runs?workspace_id=demo-workspace" -ContentType 'application/json' -Body '{}'
do { Start-Sleep -Seconds 1; $run = Invoke-RestMethod "http://127.0.0.1:8000/api/audit-runs/$($run.id)?workspace_id=demo-workspace" } while ($run.status -in @('queued', 'running'))
Invoke-RestMethod "http://127.0.0.1:8000/api/sites/$($site.id)/pages?workspace_id=demo-workspace"
```

第 1 阶段的页面采集流程和历史验证记录见[第 1 阶段状态](docs/phase-1.md)；其中的测试计数和迁移版本不是当前验收结果，应以最近一次命令输出与当前代码为准。
企业事实、采购问题冻结、内容生成和人工审核的操作边界见[第 3 阶段状态](docs/phase-3.md)。

版本化变更、审批 outbox 与发布限制见[第 4 阶段状态](docs/phase-4.md)。审核通过后可排入 outbox；在显式配置隔离本地 Git 仓库时，Worker 会生成可审查的本地分支和 commit。该适配器不 push、不创建远程 PR、不写 CMS，也不代表线上部署成功。

## 内容改稿演示

下面的流程使用仓库内的合成站点。API 与 Worker 必须使用同一数据库；合成站点只供本地演示，不要把合成事实用于真实产品页面。

1. 按前文启动 Fixture、API、Worker 和 Web，并在 API 与 Worker 启动环境中设置 `$env:ALLOW_LOOPBACK = "true"`。
2. 在“站点与审计”登记 `http://127.0.0.1:8765`，将站点标记为合成站点，运行一次采集，并等待任务完成。
3. 在“站点与审计”的企业事实区导入演示用事实，明确标识其为合成数据，设置为可公开并确认。真实业务使用时，应从当前供应商资料导入并核对来源，不能用演示值替代。
4. 在“采购问题”创建或编辑问题集，为问题映射已采集的页面并冻结当前版本。内容任务只能使用当前冻结版本和仍然最新的页面快照。
5. 打开侧栏“内容改稿”，选择站点与冻结版本；将问题和映射页面加入任务，填写改稿要求，选择至少一条当前有效、已确认且可公开的事实，然后创建任务。每个任务最多 5 个问题与页面组合。
6. Worker 会在后台处理任务。刷新任务状态后，检查改稿字段差异、页面快照和事实来源；提交人工审核后，由审核人标识作出通过或退回决定。

无模型凭据时此演示由本地 Fixture gateway 生成确定性草稿，不发起模型网络请求。人工通过只记录审核决定，不会修改 Fixture 或真实网站。

## 验证命令

```powershell
backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider backend/tests -q
npm --prefix apps/web run build
```

The local adapter and unified accounting rehearsals are also reproducible:

```powershell
backend/.venv/Scripts/python.exe scripts/local_html_publication_e2e.py `
  --output output/optimization/O04/local-html-publication-e2e-20261006.json
backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider `
  backend/tests/test_content_worker_recovery.py `
  backend/tests/test_visibility_phase5.py `
  backend/tests/test_model_accounting.py `
  backend/tests/test_runtime_governance.py `
  backend/tests/test_worker_dependency_isolation.py -q
```

These commands exercise synthetic local evidence. They do not authorize
production site writes, real provider billing, or remote deployment.

Phase 3 内容任务的专项测试文件及其覆盖边界见[第 3 阶段状态](docs/phase-3.md)。不要用历史测试计数代替当前命令输出。

健康检查由 `/health` 与 `/api/health` 提供；`fixture_available` 会实际探测 `127.0.0.1:FIXTURE_PORT`，不会在 Fixture 未启动时误报可用。页面列表摘要 `/api/sites/{site_id}/pages` 返回规则总数、失败、待复核和未知结果数量。

## 公开站点只读检测

在已获授权的前提下，可登记一个公开 HTTP(S) 站点并运行只读审计；审计只抓取 HTML、robots.txt、sitemap 和有界站内链接，不执行发布或写入。仓库保留了一次 `https://example.com` 的历史检测记录；这不代表该站点当前状态。真实客户站点应先确认授权、采集范围和访问频率，再替换示例域名。

历史授权演示记录（2026-09-28）：job 状态为 `succeeded`，页面 HTTP 状态为 `200`，内容 hash 为 `ff67a9d764d6a2367a187734e697f6a53217db9a21c101d410a113ca871a299d`，共执行 12 条规则，其中 1 条为 `needs_review`。这只是一次只读采样证据，不代表当前线上状态、搜索引擎收录、排名、AI 引用或业务增长。

GitHub 参考项目、许可证核对和选择性借鉴边界见[参考项目选择记录](docs/reference-selection.md)。

## Phase 5/6 交付入口

- [Phase 5 可见性监测状态](docs/phase-5.md)：Provider 能力、原始回答/引用保存、预算和 fixture 边界。
- [Phase 6 评测与交付说明](docs/phase-6.md)：工程门禁、离线评测、ADR、演示脚本和已知限制。
- [Phase 6 实测报告](docs/phase-6-report.md)：本轮测试、两个公开站点的只读采样和未完成验收项。
- [生产验收闸门](docs/production-acceptance.md)：真实 Provider、站点授权、人工评测、备份恢复、镜像、身份、回滚、CRM 和远程发布证据的只读检查。
- [本地 readiness 与备份恢复说明](docs/local-readiness.md)：隔离 Git 发布/回滚、Compose 边界和临时 PostgreSQL 恢复演练。
- [O04 本地 HTML 发布闭环证据](output/optimization/O04/local-html-publication-e2e-20261006.json)：受控字段、fresh observer、验证和回滚冲突保护。
- [O08 统一账务证据](output/optimization/O08/evidence.json)：内容/Visibility `ModelCall` 台账、未知费用和重放边界。
- [评测结果](evals/artifacts/phase6-evaluation.json) 与 [人工标注格式](evals/annotations/README.md)：合成 fixture 与待人工复核数据明确分开。
- [架构图](docs/architecture.md)、[架构决策记录](docs/adr/) 与 [五分钟演示脚本](docs/demo-script.md)。
- [依赖与许可证边界](docs/licenses.md)、[许可证清单](docs/license-inventory.md) 与 [据实项目条目](docs/resume-entry.md)。
