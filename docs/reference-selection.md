# 参考项目选择记录

本项目坚持 Python / FastAPI 主路线。外部项目只用于比较模块边界和交互方式；规则、采集器和业务数据模型在本仓库独立实现，不把参考项目的代码或搜索排名结论当作本项目能力。

## 本轮检索

检索日期：2026-09-28 至 2026-09-29。以下结论来自 GitHub 页面、API 元数据和公开仓库文件，许可证和默认分支可能变化，采用前应再次核对。

| 项目 | 许可证核对 | 选择性参考 | 不采用 |
| --- | --- | --- | --- |
| [seo-ops](https://github.com/tigerless-labs/seo-ops) | GitHub 未给出可确认许可证，`LICENSE` 路径未找到 | 确定性规则的稳定 ID、证据型结果、三态/不可判定结果、限流和页面上限 | 不复制源码、规则清单或许可证声明；不把启发式结果说成搜索处罚 |
| [agentic-seo](https://github.com/addyosmani/agentic-seo) | MIT（以当前公开 README/包元数据为准） | robots/AI crawler 文件检查、结构化 JSON 输出、阈值和 CI 友好契约 | 不引入 Node 运行时；不把 token 或文档评分当作收录保证 |
| [GetCito](https://github.com/ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool) | README 称 MIT，但 GitHub 元数据为 `NOASSERTION` 且未找到 `LICENSE` | Web/Worker 分离、持久任务、OpenAPI 与引用数据模型、工作台组件分层 | 不复制代码，不替换本项目 Python/SQLAlchemy 路线，不采用未经核对的许可证 |
| [AEO Radar](https://github.com/hellowalt/aeo-radar) | MIT（以当前公开包元数据为准） | KPI、趋势、结果表和低门槛 dashboard 信息架构 | 不使用 Playwright stealth 抓取消费者产品；不绕过服务条款或伪造联网可见性 |
| [SEOnaut](https://github.com/StJudeWasHere/seonaut/tree/880b312c28fab8b0bf7fe4f9449dc4746dbb82ff) | MIT；提交 `880b312c28fab8b0bf7fe4f9449dc4746dbb82ff` | 低门槛站点登记、范围选项说明、问题到受影响页面的下钻 | Go 服务端模板，不移植代码或组件 |
| [Umami](https://github.com/umami-software/umami/tree/ec0ff50388c264ed8ce46f00967e92f7e71476ae) | MIT；提交 `ec0ff50388c264ed8ce46f00967e92f7e71476ae` | 渐进式站点接入：先收集名称和域名，再进入后续配置 | Next.js 与私有组件库，不移植代码或组件 |
| [Lighthouse](https://github.com/GoogleChrome/lighthouse/tree/08c05cdee7e191719480477056bd145abcb09c17) | Apache-2.0；提交 `08c05cdee7e191719480477056bd145abcb09c17` | 将摘要、检查清单、表格和逐项证据分层呈现 | 原生 JavaScript 报告渲染器，不复制实现 |

## 已落地的参考原则

- `backend/audit_rules.py` 使用稳定规则 ID、版本、证据和 `unknown`/`not_applicable`/`needs_review` 状态；网络失败不会被当作通过。
- `backend/audit.py` 对 robots、sitemap、重定向和站内链接进行有界采集，并把采集上下文冻结到任务记录，便于复现。
- `apps/web` 采用站点登记、运行检测、查看问题、打开证据的四步入口；真实采集与合成演示明确标识，规则证据可逐条展开。
- SEOnaut、Umami 与 Lighthouse 的上述提交只用于交互和证据组织参考；未复制代码，许可证记录不替代逐文件依赖审查。

这些实现是本仓库的独立代码。外部项目没有被声明为本项目的代码来源，也没有被用来证明排名、收录或询盘增长。
