# Phase 6 实测报告

更新时间：2026-10-01（Asia/Shanghai 环境；在线证据以 JSON 中的 UTC 时间为准）

这份报告把工程门禁、离线 fixture 评测和两个公开站点的有界只读采样分开记录。
任何一项都不等同于搜索排名、消费者 AI 引用、流量或询盘增长。

## 工程门禁

| 检查 | 本轮结果 | 证据 |
| --- | --- | --- |
| Python 后端测试 | `144 passed` | `backend\\.venv\\Scripts\\python.exe -m pytest -p no:cacheprovider backend/tests -q` |
| 评测工具测试 | `7 passed` | `backend\\.venv\\Scripts\\python.exe -m pytest -p no:cacheprovider -q evals\\tests` |
| Web 构建 | 通过，Vite 8.3.1 | `npm --prefix apps/web run build` |
| 数据库迁移 | 通过，当前 `0014_memberships_audit_events_snapshot_metadata (head)` | `backend\\.venv\\Scripts\\python.exe -m alembic -c backend\\alembic.ini upgrade head` 和 `current` |
| PostgreSQL API + checkpoint smoke | 通过；隔离 PostgreSQL 数据库上检查 migration head、`/health`、`/api/health`、site 注册/列表和 checkpoint 重开 | `backend\\.venv\\Scripts\\python.exe scripts\\postgres_smoke.py --database-url $env:DATABASE_URL` |
| PostgreSQL 隔离迁移与备份恢复 | 通过；独立无卷临时库完成 `0001 -> 0014`，`pg_dump` 经单事务 `psql` 恢复到空目标库后，Alembic head、marker 行、`0014` 字段和代表性行数校验通过；未删除现有 Compose volume | `docs/container-runtime.md` 的恢复门槛；`scripts/local_postgres_restore.py` 输出 |
| PostgreSQL 并发与租约恢复 smoke | 通过；localhost 隔离空库验证预算并发预占仅一笔成功、同一 outbox 事件仅一个 worker 领取，并验证 visibility/outbox 过期租约恢复；通过模拟租约过期，不代表进程崩溃或生产压力测试 | `scripts/postgres_concurrency_smoke.py`；`docs/local-readiness.md` |
| 本地五页发布 readiness | 通过；5 个合成页面均完成本地 Git 提交、部署回调和验证，首个版本完成 SHA 保护回滚；重复发布与回滚保持幂等，远程 Git/CMS/外部部署均为 false | `scripts/local_readiness.py`；`output/phase4-local-readiness.json` |
| 运行治理 | 新增验证；单次/工作区日预算上限、原始证据字节限制与保留期、Provider request ID 透传和敏感信息脱敏均有测试；真实 PostgreSQL 同工作区并发预算预占只允许一笔成功 | `backend/tests/test_runtime_governance.py`；临时 PostgreSQL 并发 smoke |
| HTTP 可观测性 | 新增验证；API 响应回传 `X-Request-ID`，HTTP 日志通过脱敏 helper 输出结构化事件 | `backend/observability.py`；`backend/tests/test_observability.py` |
| 容器镜像构建 | 未验证；静态 Dockerfile/Compose 门禁和配置渲染通过，Docker Hub OAuth token 请求超时阻断基础镜像 metadata 拉取 | `scripts/container_build_check.py`；`docs/container-runtime.md` |
| 生产验收闸门 | 当前为 `blocked`；闸门本身只读，不调用 Provider、站点、CRM、Docker、Git remote 或部署系统；缺少真实证据时逐项列出阻塞原因 | `scripts/production_readiness.py`；`docs/production-acceptance.md` |
| 本地 readiness 回归与 Compose 边界 | `4 passed`；Compose 默认/demo profile 静态检查通过，验证 loopback 端口、fixture 隔离、Worker 等待健康 API 和迁移 head | `backend/tests/test_local_readiness.py`；`scripts/local_compose_check.py` |
| Compose 配置 | 默认与 `demo` profile 均通过 | PowerShell 设置 `$env:POSTGRES_PASSWORD` 后运行 `docker compose [--profile demo] config --quiet` |
| 工作区差异检查 | 通过；仅有 Git 的 LF/CRLF 提示 | `git diff --check` |
| 工作区路由授权边界 | 通过；工作区范围业务读写路由使用 Membership 角色和跨工作区检查；`LOCAL_AUTH_MODE=required/strict` 缺少身份时返回 `401`，角色不足返回 `403`。健康检查、知识库和创建工作区等公开/引导接口保持公开 | `backend/authz.py`；`backend/tests/test_authz.py` |

新增的 Phase 6 测试覆盖评测报告的 P50/P95 字段和 fixture 零成本边界；新增的 sitemap
回归测试覆盖 WordPress `wp-sitemap.xml` 控制资源；`evals/tests/test_annotation_tool.py`
覆盖人工标注格式、未完成记录排除和策略汇总；`evals/tests/test_phase6_completion.py`
覆盖离线 fixture 门、人工门禁和矩阵的阻断条件。

## 离线评测

结果文件：[`evals/artifacts/phase6-evaluation.json`](../evals/artifacts/phase6-evaluation.json)

- 数据集：`phase6-frozen-v1`，40 个合成案例，30 个 dev、10 个 holdout。
- 模型/提示版本：`fixture-eval-v1` / `procurement-fact-constraint-v1`。
- 事实约束改写：holdout 平均分 `1.0`，事实一致性、采购问题覆盖和禁用声明缺失率均为 `1.0`。
- 原文与普通改写：holdout 平均分均为 `0.3333`；这反映 fixture 策略的受控差异，不是线上模型结论。
- fixture 成本：每种策略总成本 `0`，因为没有调用真实模型或检索 Provider。
- fixture 本地耗时：策略 P50/P95 约 `0.002 ms`；这是字符串 fixture 的本地执行时间，不是生产 P50/P95。

计划要求的独立人工标注 30-50 案例、可读性/可执行性盲评和真实模型三路对照尚未完成。
现在提供可运行的格式校验和汇总工具：
`backend\\.venv\\Scripts\\python.exe evals\\annotations\\annotation_tool.py validate <annotations.json>`
和 `summary <annotations.json>`。工具要求四项 1-5 评分、审核决定及必要理由，明确区分
`complete` 与未完成记录；缺失标签不会按零分或已采纳计入指标。格式、留出集冻结规则和
盲评流程见 [`evals/annotations/README.md`](../evals/annotations/README.md)。

Phase 6 门禁矩阵：
[`evals/artifacts/phase6-completion-matrix.json`](../evals/artifacts/phase6-completion-matrix.json)。
本轮 `offline_fixture.complete=true`、交付物检查为 true；人工标注为 0 个完整案例，
因此 `human_evaluation_claim_allowed=false`、`adoption_rate_claim_allowed=false`，
`overall_complete=false`。矩阵是机器可检查的当前状态，不把合成 fixture 或模板当作人工完成。

## 两站只读采样

三页基础证据：[`output/online-audit/zoogo-sites-readonly-20261001.json`](../output/online-audit/zoogo-sites-readonly-20261001.json)。
带规则证据详情的复核文件：[`output/online-audit/zoogo-sites-readonly-20261001-evidence.json`](../output/online-audit/zoogo-sites-readonly-20261001-evidence.json)。
二十页扩展采样：[`output/online-audit/zoogo-sites-readonly-20261001-page20.json`](../output/online-audit/zoogo-sites-readonly-20261001-page20.json)，
其中 `zoogosports.com` 的带详情文件为 [`output/online-audit/zoogosports-readonly-20261001-page20-evidence.json`](../output/online-audit/zoogosports-readonly-20261001-page20-evidence.json)。

- `https://zoogo.club`：首页、robots、`wp-sitemap.xml` 均返回 `200`；审计 job `succeeded`，有界采样 3 页、36 条规则结果、0 个硬 findings。
- `https://zoogosports.com`：首页、robots、`sitemap_index.xml` 均返回 `200`；审计 job `succeeded`，有界采样 3 页、36 条规则结果、0 个硬 findings。
- 请求方法为 `GET`，页面上限为每站 3 页，临时 SQLite 隔离；没有调用 CMS、Git、PR、表单或发布接口。
- 规则结果仍包含 `needs_review` 和 `unknown`，`0 findings` 只表示该有界样本没有硬 finding，不表示全站通过。
- 二十页扩展采样中，`zoogo.club` 为 20 页、240 条规则结果（`pass=182`、`needs_review=31`、`unknown=15`、`not_applicable=12`）；`zoogosports.com` 为 20 页、240 条规则结果（`pass=168`、`fail=20`、`needs_review=26`、`unknown=8`、`not_applicable=18`）。20 条规则失败包含 19 条 `internal_links` 和 1 条 `http_status_redirect`，证据指向 `https://zoogosports.com/cdn-cgi/l/email-protection` 返回 `404`；这些是规则层失败，数据库中的 `AuditFinding` 记录仍为 0，不能混写为“0 问题”。
- 所有在线报告均为有界 HTML/robots/sitemap 采样，不是全站审计；没有调用 CMS、Git、PR、表单或发布接口，也不测量排名、消费者 AI 引用、流量或询盘。

## 未完成验收

1. 人工标注评测集和人工审核采纳/拒绝记录仍待真实参与者完成。
2. 真实联网 Visibility Provider 需要已授权的 endpoint、凭据、模型配置和持续采样；当前只验证了 fixture、unavailable 和手工采集边界。
3. Search Console、分析/CRM 询盘数据、生产 PostgreSQL 并发压力、生产备份策略和线上发布仍未验收；独立临时 PostgreSQL 迁移、双库备份恢复和本地并发/租约 smoke 已通过。
4. 当前演示站已扩展为 20 个明确标记的合成 HTML 页面，但仍不能替代真实业务站点或真实企业资料。
5. Dockerfile 镜像构建尚未完成：本轮 Docker Hub token 网络请求超时；Compose 配置渲染和 Docker daemon 可用性已验证，未把镜像构建失败写成运行成功。

## 重现

```powershell
backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider backend/tests -q
backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q evals\tests
npm --prefix apps/web run build
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini current
backend\.venv\Scripts\python.exe evals\visibility_eval.py --output evals\artifacts\phase6-evaluation.json
backend\.venv\Scripts\python.exe evals\phase6_completion.py --allow-incomplete --output evals\artifacts\phase6-completion-matrix.json
backend\.venv\Scripts\python.exe -m pytest -q evals\tests\test_annotation_tool.py
backend\.venv\Scripts\python.exe evals\annotations\annotation_tool.py summary <annotations.json> --output evals\artifacts\phase6-human-summary.json
backend\.venv\Scripts\python.exe scripts\readonly_online_audit.py https://zoogo.club https://zoogosports.com --page-limit 3 --output output\online-audit\zoogo-sites-readonly-20261001.json
backend\.venv\Scripts\python.exe scripts\readonly_online_audit.py https://zoogo.club https://zoogosports.com --page-limit 20 --output output\online-audit\zoogo-sites-readonly-20261001-page20.json
# Set DATABASE_URL to an isolated, already running PostgreSQL database.
backend\.venv\Scripts\python.exe scripts\postgres_smoke.py --database-url $env:DATABASE_URL
$env:POSTGRES_PASSWORD = "local-only-validation-secret"
docker compose config --quiet
docker compose --profile demo config --quiet
git diff --check
```
