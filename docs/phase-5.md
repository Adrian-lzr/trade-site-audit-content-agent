# Phase 5: 可见性监测

## 当前交付

Phase 5 已加入独立的可见性采样边界。采样使用冻结的采购问题集版本，结果按
工作区和站点归属保存；每个样本同时保留原始响应、解析回答、引用 URL、提及域名、
模型、token、费用和错误状态。

后端包括 `VisibilityRun` / `VisibilitySample` 数据模型、迁移 `0012_visibility_monitoring`、
`0013_visibility_budget_snapshots`、后续迁移和当前 repository head、
持久化租约 Worker 和 API：

- `fixture`：离线、确定性的 synthetic provider，明确标记为演示数据；
- `structured_http`：连接已获授权的结构化 HTTP 来源；缺少 endpoint 或 credential 时返回
  `unavailable`，不会把普通模型回答伪装成实时搜索结果；
- `manual_capture`：等待人工提交回答和引用的证据。

站点引用率、品牌词/非品牌词提及率、采样成功率和问题覆盖率分别计算，不合并不同来源、
问题集版本或模型配置。预算、幂等键、工作区隔离和 Worker 租约恢复由后端负责。
域名提及和可追溯引用是两个独立字段；只有引用 URL 经过保存并与回答样本关联时才计入引用率，
单独出现在回答文字里的域名不会被当作引用证据。

## 验证

2026-10-01 在临时 SQLite 数据库上运行：

```powershell
backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider backend/tests/test_visibility_phase5.py -q
```

该文件的定向测试历史结果为 `4 passed`；当前完整后端门禁为 **144 passed**（详见
[`docs/phase-6-report.md`](phase-6-report.md)）。Fixture 场景验证原始回答、引用和指标持久化；无凭据的
`structured_http` 场景验证样本为 `unavailable` 且不计入成功率；另验证跨工作区拒绝、
幂等、预算超限，以及多会话条件预占和固定精度结算。

Web 构建验证：

```powershell
npm --prefix apps/web run build
```

工作台的“可见性监测”页面提供站点、冻结问题集版本、Provider、市场、语言、品牌词、样本
数和预算配置，并展示采样记录、原始回答、引用和状态。已生成的桌面/移动截图位于
`output/playwright/visibility-fixture-desktop-20261001.png`、
`output/playwright/visibility-fixture-mobile-20261001.png` 及对应空状态截图。

两站的本次只读连通性与三页有界审计证据见
[`output/online-audit/zoogo-sites-readonly-20261001.json`](../output/online-audit/zoogo-sites-readonly-20261001.json)。
该文件同时记录 robots/sitemap 状态、重定向、审计 job、页面数、发现数和请求边界。

## 边界与未验收项

`structured_http` 只是可配置集成适配器；本仓库没有默认的真实搜索表面凭据，也没有声明
已接入 `zoogo.club` 或 `zoogosports.com` 的线上监测。两站目前只能作为已获授权的只读输入，
不会被自动发布或写入。搜索排名、消费者搜索引用、Search Console 归因、询盘和收入提升均
需要单独的真实来源、授权和持续观测证据。
