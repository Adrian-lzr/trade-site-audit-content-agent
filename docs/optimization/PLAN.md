**trade-site-audit-content-agent：可直接交给 AI 的优化执行计划书**

版本：1.0；编制日期：2026-10-01。仓库：[Adrian-lzr/trade-site-audit-content-agent](https://github.com/Adrian-lzr/trade-site-audit-content-agent)。固定基线：[2356fa39898d1495bdbd95ae65a03486f197d70a](https://github.com/Adrian-lzr/trade-site-audit-content-agent/commit/2356fa39898d1495bdbd95ae65a03486f197d70a)。配套输入：《项目优化目标_20261001.md》。本计划是后续实施说明，本次交付没有修改仓库源码或部署网站。

**1. 直接交给执行 AI 的指令**

> 你是本仓库的执行工程师。请读取本计划及配套目标文件，检查当前源码与基线差异，然后按 T00–T19 的依赖关系实施、验证和记录结果。直接完成代码与测试，不停留在建议或复述计划。保留现有 FastAPI、SQLAlchemy/Alembic、LangGraph、React/TypeScript/Vite 技术栈。先建立已知错误的回归，再实施最小可审核改动；使用独立优化分支，保留用户已有修改。每完成一项，记录实际变更文件、迁移、检查命令、结果、证据路径和下一步。若最新代码已经解决某项问题，用对应测试或调用链证明后标为已满足，避免重复实现。依赖不足时完成可独立执行的工作，明确待真实输入的验收项。不得用模拟结果、改评分逻辑、删除回归或伪造人工评阅来取得通过。按本计划的外部输入与试点范围执行真实调用；密钥通过环境或秘密管理提供。完成后分别交付源码变更、目标完成矩阵和验收证据，清楚标明达到 M0、M1、M2 或 M3。

可以将两份文件放入仓库 `docs/optimization/GOALS.md` 与 `docs/optimization/PLAN.md`，从 T00 开始。一般实现选择按本计划默认值推进，无需每个任务重新征询偏好。真实账号写入、目标网站发布和远程代码交付按用户实际提供的授权配置执行；其余开发、修复、隔离测试和本地预览持续推进。

**2. 初始输入、默认值和外部依赖**

| 输入 | 默认实施方式 | 缺少真实输入时的处理 |
| --- | --- | --- |
| 源码 | 当前 main，记录执行起始 SHA；本计划问题基于上述固定提交 | 获取不到源码时列出访问阻塞；不编造代码状态 |
| 开发环境 | Python 3.12、Node 22、PostgreSQL 16；依赖使用仓库锁文件 | 先修复本地环境或在可用 CI 运行；标明未执行检查 |
| 首个产品 | 阀门合成产品，保留 synthetic 标记；采购参数来自受控 fixture | 可以完成演示，不能当作真实企业事实 |
| 网站形态 | 必做受控静态 HTML adapter；真实试点按实际网站形态选择 adapter | 本地演示完成后列出实际源码/CMS、字段映射和预发布 URL 等缺项 |
| 企业资料 | 有来源的 CSV、有文本层 PDF；确认人员及可公开范围 | 用合成资料测试解析；真实 Fact 确认保留待办 |
| 内容模型 | 现有 OpenAI-compatible DraftGateway，模型/URL/价格由配置确定 | 保留 Fixture，完成协议测试；真实评测标 blocked |
| 观测 Provider | 至少一个真实 model_api 适配器，原生 citations/usage 能力明确；手工证据入口保留 | 用结构化 fixture 测协议；不能把它算作真实可见性 |
| 身份 | 生产 OIDC/JWT：issuer、audience、JWKS；Membership 仍从数据库加载 | 本地假身份服务仅用于集成测试；生产准入待真实配置 |
| 人工盲评 | 独立评阅者与可使用的数据；30 个 holdout × 3 方案 | AI 生成匿名材料与待标注清单；不代填“人工”成绩 |
| 商业数据 | 真实 Search Console/分析数据或可核对导出 CSV；询盘事件定义 | 完成导入与报告能力，实际增长结论保持未评估 |

执行前将参数写入 `docs/optimization/INPUTS.md`，仅写配置名、状态和非敏感标识。密钥、个人数据和内部资料正文不写进 Git 或报告。参数含凭据时不要把完整连接串输出到日志。

**3. 实施原则与目标结构**

采用“先正确性、再真实闭环、再质量与运营”的顺序。现有模块直接修复后，逐步把 `backend/app.py` 中业务逻辑移入应用服务；不在第一步重写整个后端。保持 API 兼容，结构化声明和指标语义通过明确 schema/metric 版本升级。SQLite 演示与 PostgreSQL 生产路径均需验证，不能用 SQLite 通过推断 PostgreSQL 并发正确。

| 边界 | 当前入口 | 建议职责与新增位置 |
| --- | --- | --- |
| HTTP 与契约 | backend/app.py、schemas.py | backend/api/ 路由、backend/contracts/ 数据契约；只处理认证、输入、服务调用与结果 |
| 事实与审批规则 | app.py、content_workflow.py、publication_worker.py | backend/services/facts.py、changes.py；共享当前事实和可发布条件 |
| 采集与证据 | crawler.py、audit.py、audit_rules.py | 保留网络防护，增加解析与 evidence 服务；原始快照不被正文抽取替换 |
| 内容编排 | content_workflow.py、content_worker.py | 图状态、声明校验、任务恢复；Gateway 与账务通过适配边界注入 |
| 模型与预算 | model_gateway.py、visibility_provider.py | backend/services/model_calls.py、budgets.py；统一请求、用量与费用状态 |
| 发布与验证 | git_publisher.py、publication_worker.py | backend/publishers/ 页面适配器；backend/services/deployment.py 负责内容验证 |
| 观测 | visibility_worker.py、visibility_provider.py | backend/services/visibility_metrics.py；版本化指标、Provider 证据与历史维度 |
| UI | apps/web/src/*Workspace.tsx、api.ts | 按审核、证据、工作区和运行状态拆组件；类型来自服务端契约 |
| 交付验证 | evals/、scripts/、CI | 公平评测、真实容器与数据库检查、分级验收证据 |

新增文件名是建议实施位置，执行 AI 可按仓库现有规范调整；必须记录替代路径。新增迁移从实际 Alembic head 递增，当前基线为 0014，不能硬编码假定后续编号空闲。数据库新增字段使用可解释的兼容默认值与回填报告，不静默丢弃历史证据。

**4. 优先级、依赖与阶段验收**

| 阶段 | 任务 | 完成条件 | 计划工作量估算 |
| --- | --- | --- | --- |
| P0 核心正确性 | T00–T05 | M0：事实、标题、时间和指标反例完成数据库级回归 | 6–10 人日 |
| P1 真实闭环能力 | T06–T14 | 代码与本地适配器、审核、身份、恢复、Provider 接口齐全 | 14–24 人日 |
| P2 运行与评测 | T15–T17 | M1 可复现；真实输入具备时完成 M2 | 8–14 人日，人工评阅另计 |
| P3 效果与交付 | T18–T19 | M3 的真实数据观察、文档与结果可核验 | 4–7 人日，观察窗口另计 |

这是单人实施的范围估算，不是工期承诺。总计约 32–55 人日；外部身份、网站接入、人工评阅和建议 ≥28 天的业务观察会影响日历时间。时间受限时先交付 M1，再完成真实试点输入，不通过删除 P0 或改变指标压缩范围。

| 任务 | 前置任务 | 目标 |
| --- | --- | --- |
| T00 | 无 | O10 |
| T01 | T00 | O01、O10 |
| T02 | T00、T01 | O02 |
| T03 | T01 | O01、O06 |
| T04 | T03 | O01、O06 |
| T05 | T01 | O05 |
| T06 | T02、T03、T04、T05 | O03、O07、O10 |
| T07 | T04、T06 | O03、O08 |
| T08 | T03、T06、T07 | O01、O03、O08 |
| T09 | T06 | O07 |
| T10 | T02、T03、T06 | O02、O06 |
| T11 | T04、T05、T08、T09、T10 | O01、O03、O07 |
| T12 | T03、T04、T06、T08、T09 | O04 |
| T13 | T02、T12 | O04 |
| T14 | T05、T07、T09 | O05、O08 |
| T15 | T04、T07、T10、T11、T14 | O09 |
| T16 | T07、T08、T09、T13、T14 | O08、O10 |
| T17 | T11、T13、T14、T16 | O04、O07、O08、O10 |
| T18 | T05、T13、T14、T15 | O05、O09 |
| T19 | T15、T17、T18 | O09、O10 |

表中依赖只表示技术前置。T17 通过且 T00–T14、T16 全部满足，即可验收 M1；M2 还需要目标文件列出的真实身份、真实站点与真实评测材料。执行过程中严格区分 `code_done`、`fixture_verified`、`real_verified` 和 `blocked_external`。

**5. 统一任务完成标准**

每项任务交付至少包含代码/配置变更、适用迁移与回填、关键异常或反例验证、接口兼容说明，以及 `output/optimization/<task-id>/evidence.json`。证据记录实际 commit、输入 hash、规则/模型/提示版本、检查命令、环境、时间、exit code 和真实/合成来源。提交按可审核业务变化拆分；核心状态和 schema 变化必须先通过对应集成检查，再推进依赖任务。

执行状态维护在 `docs/optimization/STATUS.md`，行字段为 `task_id | status | commit | checks | evidence | blocker | next_action`。没有运行的检查用 not_run，外部输入不足用 blocked_external，不能写 passed。文件存在、HTTP 200、声明“已接通”均不能单独证明业务验收。

**6. 任务卡 T00–T10**

**T00｜锁定基线、建立反例与执行记录。**

代码范围：现有源码、`backend/tests/`、`evals/tests/`、`.github/workflows/ci.yml`；新增 `docs/optimization/` 与隔离证据目录。目标 O10；参考 A01、X05。

实施：记录 HEAD、工作区修改、依赖锁、迁移 head、现有测试结果；逐项核对 B01–B10，把需要 ORM 或网络适配器证明的影响转为真实集成案例。同步执行 T15 的数据准备子步骤：先按产品/来源分组冻结 dev 与 holdout 的原始资料、问题和来源清单，封存 holdout 期望答案，不在开发阶段读取它调提示；待评测运行条件齐全后再执行 T15 的比较与盲评。输入不足时先封存独立合成集并标记，真实 holdout 由独立资料/人员提供后再次冻结，不能把已用于修复的案例转称未见 holdout。读取现有测试 fixture 的隔离方式，避免运行清理脚本指向非测试数据库。测试应调用实际服务/ORM/Gateway 边界，不另造与实现同构的评分函数。已有代码已修复的事项保留回归并标 satisfied。

基线命令从仓库根目录执行，安装按现有 CI 锁文件：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python -m pytest -p no:cacheprovider backend/tests -q
.venv/bin/python -m pytest -p no:cacheprovider evals/tests -q
npm ci --prefix apps/web
npm run build --prefix apps/web
```

不要假定该命令现在已经执行。CI 的 144 + 7 是历史基线；后续通过数由实际结果填写。提交基线报告和带输入的反例，不将已知失败混成未解释的全绿。

验收：每个 B 编号对应现有证据与后续任务；能够重现或明确排除问题；未执行项有原因。已有发布探针必须升级为含真实 Fact 行的 ORM 测试。

**T01｜统一 UTC 时间与数据库约束。**

代码范围：`backend/models.py`、`database.py`、`app.py`、`content_workflow.py`、`publication_worker.py`、`visibility_worker.py`、迁移及测试。目标 O01/O10；参考 A01、X03。

实施：建立共用 UTC 归一化工具；入口要求带时区，历史 naive 时间按已记录的 UTC 约定处理并报告；有效期和租约比较统一。为 SQLite 每个连接开启 foreign_keys，盘点已有数据与复合工作区约束。先生成孤儿/冲突数据报告，再决定回填，不任意删除。启用 FK 后修正测试清理顺序，保持测试隔离。API 和 Worker 不各自实现时间政策。

验收：SQLite 下包含真实 Fact 的批准→发布守卫不再报时间 TypeError；PostgreSQL 同路径通过；有效期边界、未来生效、到期和 lease 边界测试通过；非法 FK 或跨工作区绑定不能落库。恢复/迁移步骤可重复运行。

**T02｜修复标题、canonical 与审计汇总语义。**

代码范围：`crawler.py`、`audit.py`、`audit_rules.py`、`models.py`、`schemas.py`、审计 API/UI；新增共用文档解析模块。目标 O02；参考 S01、S02、S03、S06、S07。

实施：共用解析器仅提取 HTML 文档 head 的 title，排除 SVG/title；分别保存 requested/final/declared canonical。canonical 的解析、相对 URL 归一化和跨域/多标签结果使用明确规则，不能自动替换采集身份。原 HTML、元数据与正文有独立 hash/版本。将规则结果映射成一致的 findings：fail 生成问题；unknown 和 needs_review 单列；NA 不计失败。更新 summary 与 UI，保留历史 metric/rule 版本。

验收：至少 15 类片段，包括 SVG、多 title、body meta、相对 canonical、跨域 canonical、产品表格、FAQ、JS 空壳和异常 HTML；同一冻结快照重跑稳定；fail 数与 findings 映射一致；旧快照与旧摘要不伪装为新规则结果。

**T03｜实现事实系列的当前版本与审批失效。**

代码范围：Fact 模型/迁移、`app.py` 事实创建/确认、`content_workflow.py` 检索、`publication_worker.py` 守卫；新增 `services/facts.py`。目标 O01/O06；参考 A08、X03。

实施：用 FactSeries 或等价聚合记录当前有效策略；系列按工作区、产品和适用范围分隔。保留事实值不可变，追加版本和状态事件；同一范围的新确认版从 valid_from 开始替代前版，未来版不提前生效。确定区间无重叠和撤销规则，查询 `resolve_current_facts(as_of)` 作为生成、审核与发布的唯一依据。使用事务和并发约束防止两个确认同时成为当前。旧系列存在重叠时输出迁移冲突清单，不能猜测真实企业意图。

验收：v1→v2、未来版、撤销、过期、不同市场/型号、并发确认、旧审批发布均通过。新版确认后绑定 v1 的修订被明确标 stale，发布被拒绝；历史审核仍可查看。SQLite 单进程与 PostgreSQL 并发路径各有证据。

**T04｜建立 V2 内容 schema 与声明—证据校验。**

代码范围：`schemas.py`、`content_workflow.py`、`model_gateway.py`、`app.py` 手工改稿入口、revision 存储及测试；新增 `contracts/content.py`、`services/claims.py`。目标 O01/O06；参考 A08、X02、X05。

实施：定义统一的字段和嵌套 schema，禁止任意 dict 内藏数值。每条事实性声明绑定 fact_id/version、产品、属性、值、单位、范围和来源；定位到具体字段路径。LLM 返回声明与证据引用，服务端从可信 Fact 重新加载值并校验，不接受模型自填“已验证”。数值用 Decimal 和显式单位类别比较，百分比、数量、压力和温度不互通；转换仅采用可审计白名单。修订同时保存 claim bindings、validation report 和 schema_version，并进入修订 hash。

首版对认证、价格、性能等高风险表述采用受控声明槽与确定性渲染；文本扫描/语义辅助检测漏报或扩写，无法证明的表述转 needs_review/needs_information。低风险编辑性文字与企业事实声明分开标识。高风险硬性冲突不能被人工简单“忽略”后发布，应改稿或补事实再审核。手工编辑与模型输出共享相同校验器。

建议协议示意，真实 ID 和范围由数据库解析：

```json
{
  "schema_version": 2,
  "field_diff": {
    "title": "Valve procurement specifications",
    "body_blocks": [{"kind": "specification", "text": "Minimum order: 20 pieces.", "claim_ids": ["c1"]}]
  },
  "claims": [{"claim_id": "c1", "field_path": "/body_blocks/0/text", "fact_id": 101, "fact_version": 2, "subject": "Valve-A", "predicate": "minimum_order_quantity", "value": "20", "unit": "pieces", "scope": {"market": "US"}}],
  "answered_question_ids": [201],
  "missing_information": []
}
```

示意中的 subject/value/unit/scope 都是待核对字段，不是信任来源。迁移不改写旧修订 hash；V1 历史记录保留，按规则标 unverified，重新验证或形成新修订后重新批准。Gateway、Fixture、手工修订、预览和发布逐一兼容升级。

验收：O01 的八类高风险反例全部阻断或转人工；合法参数与合理单位转换通过；嵌套结构、反向否定、跨型号、认证适用范围和缺来源均覆盖。未知 schema/字段拒绝。记录漏检与误拒；不以全拒绝实现“零错误”。

**T05｜重建可见性指标与历史版本。**

代码范围：`visibility_worker.py`、`visibility_provider.py`、`models.py`、visibility API/前端；新增 `services/visibility_metrics.py`。目标 O05；参考 S10、S11、S12。

实施：分别计算 brand_mention、domain_mention、validated_site_citation、answered_question 与 sample_success。明确样本分母、问题去重、不可用和错误；费用未知单列。citation 保留 URL、Provider 原始来源、规范化域名、验证状态和抓取/回答时间。配置品牌别名与子域名政策，匹配完整 host 边界，排除内部 IP 和无效 URL。若只从文本抽出链接，标 inferred，不混为 Provider 原生 citation。给指标增加 metric_version，旧报表保留旧口径，新计算以新版本显示。

验收：只有 mentioned_domains 的样本网站引用率为 0；原生可信 citation 命中为 1；相似恶意域名不命中；无样本、重复问题、部分失败、manual/synthetic 样本、未知费用均有明确值和说明。问题回答覆盖不能由网站引用替代。

**T06｜抽出应用服务与稳定 API 契约。**

代码范围：`app.py`、`schemas.py`、`knowledge.py`、`authz.py`、worker 入口；新增 api/contracts/services 目录、契约生成与测试。目标 O03/O07/O10；参考 A01、A02、A06、A07。

实施：按站点审计、事实、采购问题、内容、发布、可见性、成员拆路由。先搬移经过回归保护的业务逻辑，保留兼容别名；统一错误 code、request_id、expected_version 和分页。停止 `knowledge.py` 反向导入 app 常量，将指导条目和选择逻辑放独立模块。产生 OpenAPI 契约快照；选择兼容当前 TypeScript 的类型生成器、锁版本并从官方资料验证兼容性；保留可控的 fetch wrapper，减少手工重复 API 类型。

验收：路由/授权/错误/乐观锁行为对照通过；Worker 使用相同事实与审批服务；OpenAPI diff 可读，前端按新契约编译；app 不再承载各域完整业务实现。按领域逐块提交，禁止为了文件行数指标制造无意义跳转。

**T07｜持久化模型调用、统一费用与预算。**

代码范围：`model_gateway.py`、`visibility_provider.py`、`visibility_worker.py`、模型/迁移；新增 model_calls/budgets 服务。目标 O03/O08；参考 A02、S11、X02、X03、X04。

实施：引入 ModelCall 与 BudgetReservation 或等价模型，记录 generation/sample ID、input_hash、attempt、Provider request ID、输出引用/hash、usage、金额 Decimal、currency、price_version、cost_source、cost_known、latency、error 和账务状态。只对支持的计费项计算可预留上限，包含输入、输出上限和可知工具费用；价格未知时严格预算模式拒绝，普通模式显示未知风险与配置政策。

预算预留提交后再调用 HTTP，结果在第二个短事务结算。成功且已保存响应时复用结果；超时或崩溃造成 Provider 结果不明时标 unknown_result，若 Provider 支持检索/幂等可对账，否则按策略人工处理或显式新 attempt。租约恢复不能清空未知费用。HTTP 响应使用流式计数限制，避免先完整缓冲再截断。网络重试、修复调用与业务幂等分开记录。

验收：模型返回后、写修订前重启能复用已持久化输出；真正的不确定窗口被识别而非承诺无重复计费；同工作区并发 reservation 不超可预留额度；网络期间不持有工作区行锁；unknown cost 不作为 0；content 和 visibility 均可核对费用。账单硬限制由 Provider 侧设置配合，本地估算不宣称控制不可知费用。

**T08｜完善图审核恢复、Worker 公平调度与 outbox。**

代码范围：`worker.py`、`content_worker.py`、`content_workflow.py`、`publication_worker.py`、`visibility_worker.py`、outbox/lease 模型。目标 O01/O03/O08；参考 A03、A04、A07、A09、A10、X01、X03。

实施：保留数据库审批作为权威；批准/拒绝在同事务写 WorkflowReviewEvent/outbox，绑定 thread、revision/hash 和 decision ID。消费者通过 Command(resume) 确认审核并完成图状态；恢复节点只核对权威审批与状态，不再生成、不直接发布。修改后生成新修订需新审核。人工响应与工作流 ID 显式对应，过期响应被拒绝。

每类任务支持可配置 Worker 角色与公平轮询；领取使用原子条件、租约 token 和到期时间。所有 finish/副作用前验证当前 token；超时/可重试/不可重试错误分型，指数退避加上限；设置 heartbeat、dead-letter 与管理员重放条件。重放保留原 idempotency key、输入版本和 attempt 关联。数据库事务不跨模型/网络/Git 长操作持有业务锁；外部副作用依赖确定性 marker 和事后核对。

验收：审批后 checkpoint 与业务状态一致，拒绝不写 publish outbox；重复审核事件不多生成/多发布；两 Worker 同领、失租约晚回写、进程 kill、任务积压和死信恢复通过。按 O08 指定 fixture 环境测公平性；不得以启动顺序偶然满足。

**T09｜接入可信身份与工作区成员管理。**

代码范围：`authz.py`、API 依赖、Membership、配置、Web 登录/工作区状态；新增身份适配器与受控成员 CLI/API。目标 O07；参考 A01、A02、A05。

实施：生产模式校验 OIDC/JWT 签名、允许算法、issuer、audience、expiry、可信 subject；拒绝把 X-Local-User 当凭据。默认用标准 OIDC 登录，敏感 token 按部署拓扑选择后端 session / HttpOnly cookie，配置 CSRF 与 exact-origin CORS；不要把管理密钥交给浏览器。Membership 用可信身份映射，角色只取数据库。明确 demo、test、production 三模式，production 身份缺配置时 fail closed。

验收：非法算法、过期 token、错 issuer/audience、伪造身份头、失效会话和跨工作区资源失败；四角色矩阵通过；管理员加入/撤销成员即生效；工作区切换清除旧缓存/证据；UI 禁用操作和服务端授权一致。假 OIDC 只证明协议路径，真实身份环境另留证据。

**T10｜正文证据、资料提议与问题驱动检索。**

代码范围：`crawler.py`、`knowledge.py`、Fact/Source/Snapshot 模型、事实导入 API、采购问题映射；新增 ingestion/evidence/retrieval 服务。目标 O02/O06；参考 A06、A08、S03、S04、S05、S06。

实施：保持原快照 immutable，新增 parser_version、content block、定位和来源 hash。正文提取针对产品标题、规格表、说明、FAQ 与采购条件建立 fixtures，页面导航和脚注剔除有证据；Readability 式文章抽取仅为参考，不能让规格表消失。受限读取 sitemap/index 发现站内 URL，沿用公共 IP/DNS pinning/redirect/robots/路径防护，限制递归层数、文档数量、压缩展开大小和页面预算；URL 查询参数去重可配置且保留原 URL。

CSV 和有文本层 PDF 解析输出 proposed/internal_only facts，保存页码/行号/片段；解析不等于确认。SQL 工作区+产品+有效期+公开权限预过滤后进行词法/全文检索，返回 fact/source 定位、相关性与缺失字段。企业事实与外部指导分开；指导条目增加更新时间与过期策略。实际选入任务的事实集合冻结并进入输入 hash。

验收：O06 的至少 30 个检索问题 Recall@5 ≥90%，含缺资料拒绝和 0 跨工作区/内部泄漏；PDF 无文本层明确 unsupported_needs_ocr；批量导入失败可回滚；sitemap 循环/跨域/压缩炸弹/内网引用被拒绝；老爬虫防护回归全部保留。向量、OCR 和浏览器渲染只有明确基线需要时再扩展。

**7. 任务卡 T11–T19**

**T11｜把审核工作台变成可操作的证据界面。**

代码范围：`App.tsx`、`ContentReviewWorkspace.tsx`、`ProcurementWorkspace.tsx`、`VisibilityWorkspace.tsx`、`api.ts`、styles；新增审核/证据/运行状态组件。目标 O01/O03/O07；参考 A01、A09、A10、S10、S11。

实施：加入登录与工作区选择；以具体页面修订展示原文/修改 diff、声明、对应事实、来源片段、问题覆盖与缺资料。审核人能批准、退回、编辑后生成新修订或请求补资料；按钮与实际角色、修订版本和校验状态对应。显著展示 demo/synthetic、资料不足、旧事实、旧快照、费用未知、部署未验证和 Provider 类型，避免只显示绿色成功。

任务状态先使用可取消、退避且有期限的轮询；断网、重启、空数据、权限不足、冲突和重试均有可解释状态。工作区切换和退出清除缓存；重复点击不重复提交。服务端密钥不放 VITE_ 或浏览器持久化。增加必要组件测试与 Playwright 业务旅程；每个页面状态测试应验证业务行为，避免仅 snapshot 样式。

验收：operator 创建任务、reviewer 核对证据并批准、禁止角色操作、修改后旧批准失效、旧事实阻断和刷新后恢复均由 UI→API→数据库贯通。移动宽度和键盘审核可用；人工能从一条声明找到事实及原片段，不需查看数据库。

**T12｜新增真实页面字段适配器。**

代码范围：`publication_worker.py`、`git_publisher.py`、发布配置、PublicationAttempt/模型；新增 `publishers/base.py`、`publishers/static_html.py` 与受控网站 fixture。目标 O04；参考 S07、S08。

实施：定义 PublisherAdapter 的 prepare/apply/inspect/rollback 接口。默认静态 HTML adapter 通过服务端 manifest 映射 site/page 到允许仓库和文件，定义可修改 title/meta/body 标记/FAQ；客户端和模型不能决定任意文件路径。应用前绑定 expected base commit、目标文件/字段 hash、revision hash 和当前事实集；prepare 返回实际 diff 和待审核产物。若 prepare 与已批准内容不等价，形成新修订并重新审核。

使用 DOM/标记级修改，不以全局字符串替换覆盖模板。新增内容做 HTML 转义/允许标签和链接校验；不引入脚本、事件属性或未批准资质。审查 JSON 仍保存 provenance，网站文件必须实改。Git marker、分支和 idempotency key 保留；已成功请求重放返回历史执行结果，不再次应用或把历史结果声明为“现在仍可发布”。真实网站为 WordPress 时，额外实现先写草稿和保存远程 revision ID 的适配器，不能用静态演示冒充该站接通。

验收：批准字段在实际 HTML 变化且其他结构/内容保留；改稿不合法、路径穿越、symlink 越界、错误仓库、过期 source hash 和失效事实被拒绝；重复提交只产生一个可核验应用结果；recrawl 后的幂等重放与新修改分开处理。fixture 通过标 fixture_verified，真实站点目标另验。

**T13｜页面部署验证与回滚闭环。**

代码范围：部署回调与 `verify_deployment`、`publication_worker.py`、`git_publisher.py`、站点/部署模型、crawler。目标 O04；参考 S02、S08、S09。

实施：区分 prepared、applied、deployment_pending、verified、mismatch、verification_unavailable、rollback_pending 与 rolled_back。部署触发与回调来自配置允许的目标，回调校验签名/nonce 和 revision/commit/target，不能仅信任客户端“deployed=true”。用已有受防护 fetcher 读取目标 URL，把文档 title、批准正文块、FAQ 和稳定内容 hash 与 prepared artifact 比较；部署版本有可核对标識，记录响应证据和验证时间。

验证失败不改称成功；退避和超时有上限，不绕过 Cloudflare 或忽略错误。回滚创建新的可审查提交或受控 CMS revision；要求目标 head/remote revision 与预期一致。回滚后重新验证页面结果。动态模板排除明确非目标字段，保存比较规则版本，不能为了匹配忽略业务内容。

验收：真实页面匹配、尚未部署、正文错位、错误 commit 标识、回调重放、网络不可用、手工修改 SHA 冲突及成功回滚均有端到端证据。仅 git cat-file 成功不能标 verified。实际用户站点接入需绑定授权范围和预发布 URL，不能直接把在线只读审计网站当可写目标。

**T14｜真实可见性 Provider 与稳定监测实验。**

代码范围：`visibility_provider.py`、`visibility_worker.py`、run/sample 模型、provider 配置与手工采样接口。目标 O05/O08；参考 S09、S10、S11、S12。

实施：选一个用户可用、具备真实回答及可解释引用字段的 Provider，实现独立适配器。实施时读取其官方接口文档，锁定 SDK/协议版本，不照抄第三方样例的模型名、端点、市场参数或价格。保留 original answer、native citations、模型/表面、联网设置、语言/市场、采样时间、usage、费用来源、raw evidence hash 和 adapter_version。遵循超时、限流、响应上限、缓存/重复采样与预算规则。手工证据记录采集人、界面类型和时间；不能仅更改 provider_kind 声称消费者表面接通。

冻结 10 个采购问题，默认每问 3 次；分开统计回答成功、实际回答问题、品牌提及、域名提及和网站引用。消费者表面采集为条件扩展，只使用有权限的官方或被允许的数据方式；代码无真实权限时标 unavailable，模型 API 仍可以独立验收。Provider API 与消费者界面不共享同一成绩标签。

验收：协议 fixture 包含原生 citations、无引用、限流、超时、结构变化、未知价格和部分成功；真实采样另外保留 ≥30 个计划样本及实际成功数，不能删去失败提高成功率。真实报告能回查每次请求证据，模型 API 的 source_kind 标 model_api。已产生费用但失败的调用仍纳入账务。

**T15｜公平评测、盲评与失败归因。**

代码范围：`evals/visibility_eval.py`、`evals/annotations/`、`evals/phase6_completion.py`、测试及新增数据/运行脚本。目标 O09；参考 A04、A08、X04、X05。

实施：使用 T00 提前冻结的至少 50 个案例，20 dev / 30 holdout，核对产品系列和来源文档隔离及 hash，保留原网页真实内容及抽取证据。A 是原内容；B/C 给相同原页、采购问题、确认事实、模型和配置；B 普通提示，C 声明绑定/校验/有限修复。记录每个模型请求、额外修复和总成本。运行真实工作流，不能以直接事实模板替代 C 或以去事实模板替代 A。资料充足的可回答案例与应拒绝的资料不足案例分别统计；可回答案例错误拒绝计为失败，资料不足案例正确拒绝单独报告，不能删除拒绝样本改善成绩。

规则评测包括类型、声明绑定、单位、范围、版本、缺资料与不支持内容拦截；单独测量检索与起草，定位失败来自资料、召回、模型、校验还是审核。做移除声明校验、移除当前版本检查的消融，严格隔离，不能用于实际发布。盲评随机化 A/B/C 展示次序，隐藏策略标签；30 holdout 共 90 输出全部人工评，≥30 输出二人复评。生成 annotation schema、匿名材料和分歧报告，人工提交保留真实时间与评阅者标识。

验收：按 O09 提交事实正确、问题覆盖、高风险错误、可发布/拒绝比例、偏好、样本数、时延、成本和置信区间。Judge/人工缺失不算“通过”；离线 CI 使用 fixture 只验证评测流程，真实评测报告独立保存。未达到候选阈值则回到对应问题任务，不反向修改 holdout 或量表。

**T16｜统一事件审计与运行可观测性。**

代码范围：`observability.py`、AuditEvent、API 服务、所有 Worker、调用账务；新增运行指标/追踪接口。目标 O08/O10；参考 A02、A07、X04。

实施：变更企业事实、问题集冻结、修订、批准、发布请求、部署验证、回滚和成员权限时，在业务事务中自动写 AuditEvent。Worker 记录 initiator 和 executor、task/thread/lease、attempt、revision 与 result/error。统一 request_id → task_id → model_call_id → revision_id → publication_id → visibility_sample_id 的关联链。增加队列最老等待时长、heartbeat、失败/重试/死信、Provider 状态、费用已知率和预算预留指标。

默认结构化本地日志与数据库事件即可运行；可配置导出 Langfuse/OTel，不把其整套 ClickHouse/Redis 栈变成项目启动前提。敏感原文、密钥、个人数据和内部事实按权限保留/脱敏，trace 中使用引用和摘要。文档说明 retention、访问角色和可导出字段。

验收：UI/API 的事实确认、批准和发布能查到自动事件；崩溃/重试链可追溯；敏感字段不出现在日志/公开导出；关闭外部追踪时业务仍工作；管理员能判断 Worker 是否真的在消费而不只 API 健康。

**T17｜真实数据库、容器与全流程 CI。**

代码范围：`.github/workflows/ci.yml`、`backend/Dockerfile`、`docker-compose.yml`、测试 fixture、`scripts/container_build_check.py`、`postgres_smoke.py`、`postgres_concurrency_smoke.py`、`local_postgres_restore.py`、`local_readiness.py`。目标 O04/O07/O08/O10；参考 A01、A02、A03、X03。

实施：CI 新增隔离 PostgreSQL 16 服务，覆盖迁移、FactSeries 并发确认、预算 reservation、lease/fencing、outbox 和发布守卫。现有 backend/tests/conftest.py 强制 SQLite，必须另建 PostgreSQL 集成 fixture/作业，不能仅设置 DATABASE_URL 后运行原测试就声称测了 PostgreSQL。每个作业使用空测试库；既有本地 smoke 对 host 与非空库的限制保留。

真正执行镜像 build、Compose up、API/Web readiness、Worker heartbeat、合成网站采集、生成、审核、页面应用/验证/回滚，再停止自己创建的资源。补 Git 二进制、发布目标目录/允许配置及最小可写卷；证据文件与模型凭据不烘焙进镜像。数据库迁移由单独初始化步骤完成，避免多 API 同时执行；checkpointer 初始化亦有明确的单次配置策略。加浏览器业务测试与备份→新库恢复→业务读取验证。注入进程终止只针对测试子进程。

验收：新环境启动成功、Worker 实际消费、SQLite 和 PostgreSQL 各自测试通过、镜像确实构建、容器端到端闭环完成、恢复数据可使用；相应证据绑定 commit 与镜像摘要。默认 CI 全部 fixture，不需要真实 key；真实试点检查以独立作业/命令运行。源码 gate 与真实 build/runtime gate 分列。

**T18｜历史观测、内容收益与业务数据报告。**

代码范围：visibility 报告 API/UI、采购问题/版本、页面/部署模型；新增分析数据只读导入、趋势与报告服务。目标 O05/O09；参考 S08、S09、S10、S11、S12。

实施：使用 cohort key 固定问题版本、产品、市场、语言、Provider 类型/版本、联网配置和重复次数；报告对比窗口、计划/成功样本数、提及/引用变化与置信区间。维度改变时形成新 cohort，不连线冒充同一时间趋势。加入审核耗时、退回原因、修改轮次、缺资料比例和可发布率。样本不足或不可比较时显示明确提示，不生成趋势结论。

商业数据优先支持有来源、日期范围和 hash 的 Search Console/分析 CSV，后续按真实权限接只读 API。定义 form_submit/qualified_inquiry 等事件与去重，避免把页面浏览当询盘；CRM 接入不是当前内容闭环的必需依赖。真实发布前后建议各保留 ≥28 天数据，记录同期营销与平台变化。前后变化只能作观察，因果结论需要额外实验设计。

验收：时间范围、分母和来源可回查，未知与缺测可见；跨 cohort 不合并；合成 CSV 与真实 CSV 分别标识。无账号数据时交付 fixture 验证和真实输入缺项，不填询盘增幅。报告能区分“内容已正确应用”“模型样本变化”“真实业务事件变化”。

**T19｜验收矩阵、文档和可展示交付。**

代码范围：README、docs、运行/评测手册、`scripts/production_readiness.py`、许可证清单；目标 O09/O10；参考所有 A/S/X 项。

实施：重写 README 与现有交付文档，使其与实际 Compose、Publisher、身份与 Provider 能力一致。提交数据字典、状态/恢复说明、API 文档、运行与回滚步骤、预算政策、评测卡、已知限制、试点输入与目标完成矩阵。门禁保留 evidence manifest 评估器的只读职责，真实运行由 T17 的命令生成证据；校验本地文件/hash/commit 和结果来源，不能只接受写有 passed 的任意 JSON。

对现有全生产门禁保留原兼容 profile；如新增 `content_pilot` profile，明确声明其产品范围和必需证据，不借此宣称原来的 CRM/全业务门禁通过。M2 必须包含真实身份、站点预发布、Provider、盲评与部署/回滚证据。检查自有代码许可及第三方引用：独立实现设计，保留复用代码的授权与 NOTICE；自有代码许可按作者确认范围落地。

演示脚本依次展示资料不足、错误声明拒绝、事实替代、具体修订审核、页面字段修改、部署不匹配/成功、受控回滚、提及与引用区别，以及真实/合成评测分离。录制或截图绑定版本，不展示敏感原文和密钥。交付可核对的工程结果，避免用模板分数或架构图代替验证。

验收：O01–O10 每项有 passed/failed/blocked 状态和证据；记录实际 M 等级；从干净环境按文档复现；源码、锁文件、迁移、测例、评测数据许可、运行说明和未完成项齐全。

**8. 关键验收矩阵与故障注入**

| 编号 | 操作/反例 | 应有结果 | 任务 |
| --- | --- | --- | --- |
| C01 | 只有 MOQ=20 pieces，生成 FDA approved | 不进入可发布状态，提示缺 certification 证据 | T04 |
| C02 | MOQ 数字移作 20 bar；20 pieces 移作 20% | 属性/单位冲突拒绝；合法 MOQ 改稿通过 | T04 |
| C03 | nested pressure=9999、跨型号参数、内部/跨工作区 Fact | schema 或绑定校验阻断，无公开泄漏 | T03、T04、T10 |
| C04 | v2 当前生效；尝试发布批准于 v1 的修订 | stale/conflict，需刷新事实再审核 | T03、T12 |
| C05 | 含实际 Fact 的 SQLite 发布与有效期边界 | 无时间异常，与 PostgreSQL 政策一致 | T01、T17 |
| C06 | head/title + body SVG/title | 只提取文档标题；片段来源可查 | T02 |
| C07 | domain mention 有，citations 无；相似恶意 host | 网站引用为 0；品牌/域名提及单列 | T05 |
| C08 | 两 Worker 同领、失租约晚完成、kill 后恢复 | 一份业务终态，旧 token 写入被拒绝；尝试历史保留 | T08、T17 |
| C09 | 模型成功后未保存修订；Provider 结果不明 | 已落盘响应复用；不明结果显式对账状态 | T07 |
| C10 | 超额并发预算、未知费用、调用失败已计费 | 预留不超额度；未知不算免费；费用不遗漏 | T07、T14 |
| C11 | 批准后编辑、重复审核事件、拒绝事件 | 新修订需新批准；图恢复幂等；拒绝无发布副作用 | T08、T11 |
| C12 | 伪造身份、错 issuer/audience、跨工作区查看/导出 | 生产模式拒绝；事件记录可信身份 | T09 |
| C13 | 发布仅生成 JSON；或线上内容与批准不同 | 不满足页面应用验收；部署标 mismatch/pending | T12、T13 |
| C14 | 应用后重复请求；目标 head 被人工移动后回滚 | 重放读旧结果；冲突回滚拒绝 | T12、T13 |
| C15 | 未完成盲评、fixture Provider、无业务数据 | 对应 M2/M3 项保持 blocked，M1 可独立通过 | T15、T18、T19 |
| C16 | 新库迁移与恢复后读取、Worker 运行但不消费 | 真正恢复可用；不消费不算健康通过 | T17 |

关键正确性与外部副作用场景必须有集成证据。UI 样式等低风险改动只做必要视觉/构建检查，不追求镜像实现的测试数量。既有网络防护、乐观锁、修订 hash 与 git rollback guard 不因重构弱化。

**9. 验证命令与结果组织**

T00 的基线命令保持使用。迁移在明确隔离的新数据库运行；PostgreSQL integration job 使用新的 fixture/入口。现有脚本可复用但先读其参数和执行范围：

```bash
.venv/bin/python scripts/container_build_check.py
.venv/bin/python scripts/local_compose_check.py
.venv/bin/python scripts/postgres_smoke.py --help
.venv/bin/python scripts/postgres_concurrency_smoke.py --help
.venv/bin/python scripts/local_postgres_restore.py --help
.venv/bin/python scripts/production_readiness.py --help
```

前两条目前属于源码/Compose 门禁，不是镜像构建；真实构建由 T17 增加单独 runtime harness 执行。只使用专用空测试库和隔离发布仓库。运行 PostgreSQL smoke 的有效命令从脚本参数与测试环境生成，证据中保存脱敏后的命令；不能将生产 DSN 传给有清理操作的测试。

计划新增的运行入口建议为 `scripts/optimization_runtime_smoke.py`、`evals/run_content_comparison.py` 和 `scripts/optimization_acceptance.py`。这些名字目前不是既有命令，应在对应任务实现后才写入启动文档；参数包含隔离环境配置和 output，真实调用有显式的 Provider/预算参数，失败返回非零状态。

每任务 evidence schema 建议：

```json
{
  "task_id": "T04",
  "code_commit": "actual_sha",
  "verification": "fixture_verified",
  "environment": {"python": "actual_version", "database": "actual_engine"},
  "checks": [{"name": "C01", "status": "passed", "command_ref": "commands.txt", "log_ref": "test.log", "input_sha256": "actual_hash"}],
  "artifacts": [{"path": "validation-report.json", "sha256": "actual_hash"}],
  "blocked": [],
  "started_at": "actual_timestamp",
  "finished_at": "actual_timestamp"
}
```

该 JSON 是格式示意，不能直接作为通过证据。运行命令与报告自动生成实际值。引用 manifest 不产生模型调用、部署或容器运行；实现验收入口时检查结果出处、artifact hash 和必要事实字段，保留 original gate 的错误状态。

**10. 中止当前动作与恢复条件**

| 触发条件 | 当前任务处理 | 后续可继续的工作 |
| --- | --- | --- |
| 事实范围、版本或原页 hash 冲突 | 返回可解释 conflict，保留变更与证据；刷新资料形成新修订 | 其他任务、只读分析、修订准备 |
| Provider 结果不明或未知价格 | 保留 unknown_result/cost_unknown，按账务政策对账 | fixture 协议、UI、其他不依赖真实调用的检查 |
| 网页 429、robots 禁止、内网/越界 URL | 尊重现有采集限制；报告 unknown/blocked | 保存已采证据与范围报告 |
| 发布目标 SHA、文件 hash 或 CMS revision 不符 | 不覆盖目标；生成差异与需重新审核的修订 | 本地 prepare、审核、其他服务实现 |
| 没有身份/站点/评阅者/分析权限 | 对对应真实验收标 blocked_external | 完成适配器、fixture、材料和独立任务 |
| 测试失败或历史回填歧义 | 定位根因并修复；保留原失败证据和回填清单 | 不受该变更影响的任务 |

这些是产品正确性与试点边界，不是逐项人工确认流程。执行 AI 对可逆开发持续推进，对不确定外部结果保持可核对状态。

**11. 参考研究方法与阅读范围**

以下 10 个架构项目、12 个内容/场景项目互不重复，共 22 个；补充 5 个基础组件，共研究 27 个仓库。每个已核对公开仓库元数据、默认分支提交、目录树、README，以及至少一个与建议相关的关键实现或技术文档；合计读取 79 个文件。本研究未安装或运行这些参考项目，不将其 README 宣传效果、模型清单或“production-ready”名称视为已验证生产结论。

相似性有明确粒度：架构组覆盖全栈 API/Agent、图编排、资料证据和人工审核前端；前端型项目只借 UI 状态与审核协议，框架/评测组件单列 X，不冒充完整同类产品。场景组覆盖 SEO 审计、资料提取、内容交付和可见性监测；它们不都属于 B2B 外贸行业。建议来自已读取代码所体现的模式，具体迁移到本项目属于设计判断。

所有关键文件链接固定到读取提交，避免 main 更新导致证据漂移。项目状态与许可证判断截至 2026-10-01；实际复用代码或引入依赖前应核对所用版本，特别是已归档示例、修改过的 Apache 条款和 GPL/AGPL 项目。表中许可证为读取文件/仓库元数据的识别结果，目的是界定复用范围，本计划默认独立实现业务设计。


**12. 架构参考：10 个不同项目**

| 编号与项目 | 相似粒度与已读证据 | 借鉴做法与任务 | 适用限制与许可 |
| --- | --- | --- | --- |
| A01 · [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) | FastAPI、React、PostgreSQL、迁移与容器的全栈基础；直接相近。[README](https://github.com/fastapi/full-stack-fastapi-template/blob/cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7/README.md)；[关键实现](https://github.com/fastapi/full-stack-fastapi-template/blob/cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7/backend/app/api/deps.py)；[补充实现](https://github.com/fastapi/full-stack-fastapi-template/blob/cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7/frontend/tests/login.spec.ts) | 从已验证 token 解析用户，再查数据库权限；浏览器测试覆盖真实登录流程。迁移服务边界和契约思路。 对应：T00、T01、T06、T09、T11、T17。 | 不是 Agent 业务；当前模板使用 SQLModel，保持本项目 SQLAlchemy。上游语法/依赖不能直接假定兼容 Python 3.12。 许可：MIT。 |
| A02 · [wassim249/fastapi-langgraph-agent-production-ready-template](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template) | FastAPI + LangGraph + PostgreSQL checkpoint + auth/metrics；后端直接相近。[README](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template/blob/f7faab38fa5332669627139b27c4f2dc4178649e/README.md)；[关键实现](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template/blob/f7faab38fa5332669627139b27c4f2dc4178649e/app/core/langgraph/graph.py)；[补充实现](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template/blob/f7faab38fa5332669627139b27c4f2dc4178649e/app/api/v1/auth.py) | 以连接池管理 AsyncPostgresSaver，把 graph、auth、LLM 服务和可观测性分开；统一实例避免重复池。 对应：T06、T07、T09、T16、T17。 | 模板名称不证明生产安全；不照搬赞助模型列表、fallback 和 memory 配置，避免影响预算与数据范围。 许可：MIT。 |
| A03 · [NicholasGoh/fastapi-mcp-langgraph-template](https://github.com/NicholasGoh/fastapi-mcp-langgraph-template) | FastAPI、LangGraph、PostgreSQL checkpoint 和独立 MCP 服务；后端边界相近。[README](https://github.com/NicholasGoh/fastapi-mcp-langgraph-template/blob/2bd004a51e5d741e8eaf5bb01716b6a673684a53/README.md)；[关键实现](https://github.com/NicholasGoh/fastapi-mcp-langgraph-template/blob/2bd004a51e5d741e8eaf5bb01716b6a673684a53/backend/api/core/agent/persistence.py)；[补充实现](https://github.com/NicholasGoh/fastapi-mcp-langgraph-template/blob/2bd004a51e5d741e8eaf5bb01716b6a673684a53/backend/shared_mcp/tools.py) | 业务 ORM 与 graph checkpointer 各用适合的连接接口；共享工具/资源 schema 与服务器边界明确。 对应：T08、T17。 | 最后读取提交为 2025-06-13；README 有 planned features，不能算已完成 auth/监控。MCP 示例仅加法与 greeting，项目 MCP 为后续扩展。 许可：MIT。 |
| A04 · [langchain-ai/open_deep_research](https://github.com/langchain-ai/open_deep_research) | 持久状态、问题驱动研究、结构化结果、有界图路由；Agent 编排相近。[README](https://github.com/langchain-ai/open_deep_research/blob/1b7d2e80db9faa586165c60e09096dbbfd483a64/README.md)；[关键实现](https://github.com/langchain-ai/open_deep_research/blob/1b7d2e80db9faa586165c60e09096dbbfd483a64/src/open_deep_research/deep_researcher.py)；[补充实现](https://github.com/langchain-ai/open_deep_research/blob/1b7d2e80db9faa586165c60e09096dbbfd483a64/src/open_deep_research/state.py) | 状态 schema、迭代上限、完成条件和结果汇总分开；研究与报告评测独立。保留小而明确的业务图。 对应：T08、T15。 | 已归档；只学习图与终止条件，不作为新生产依赖。开放研究工具权限不能照搬到企业发布。 许可：MIT。 |
| A05 · [bytedance/deer-flow](https://github.com/bytedance/deer-flow) | 前端、Gateway、LangGraph、运行事件、人工输入；完整 Agent 应用相近。[README](https://github.com/bytedance/deer-flow/blob/f840e843d3e2db1485cb65ceae73a5525aa80193/README.md)；[关键实现](https://github.com/bytedance/deer-flow/blob/f840e843d3e2db1485cb65ceae73a5525aa80193/backend/packages/harness/deerflow/agents/human_input.py)；[补充实现](https://github.com/bytedance/deer-flow/blob/f840e843d3e2db1485cb65ceae73a5525aa80193/backend/packages/harness/deerflow/agents/interaction_policy.py) | 人工输入有 version、request_id、source 和响应类型；交互模式由可信入口确定。可借鉴审核响应与任务的绑定。 对应：T09、T11。 | 读取为 2.0，README 明确与 1.x 重写分离；不引入其沙箱、任意工具、子 Agent 或源文件提示中频繁确认的策略。 许可：MIT。 |
| A06 · [assafelovic/gpt-researcher](https://github.com/assafelovic/gpt-researcher) | Web/API + 资料检索/压缩 + 起草 + 引用/成本；证据生成架构相近。[README](https://github.com/assafelovic/gpt-researcher/blob/0957c301ed06c2a5857b834358c7227c739041d4/README.md)；[关键实现](https://github.com/assafelovic/gpt-researcher/blob/0957c301ed06c2a5857b834358c7227c739041d4/gpt_researcher/skills/context_manager.py) | 按问题选择上下文，控制 max_results，并把成本回调与上下文选择关联；来源跟踪和最终报告分离。 对应：T06、T10。 | 通用研究证据不是已确认企业事实；不能把搜索到的认证信息直接变成企业资质。 许可：Apache-2.0。 |
| A07 · [langgenius/dify](https://github.com/langgenius/dify) | 工作区、API、任务、模型、RAG 与 HITL；业务平台分层相近。[README](https://github.com/langgenius/dify/blob/f004f6b67d6408e11aeb5a2cc7a14d6c9ea628c3/README.md)；[关键实现](https://github.com/langgenius/dify/blob/f004f6b67d6408e11aeb5a2cc7a14d6c9ea628c3/api/core/workflow/nodes/human_input/boundary.py)；[补充实现](https://github.com/langgenius/dify/blob/f004f6b67d6408e11aeb5a2cc7a14d6c9ea628c3/api/core/workflow/nodes/human_input/session_binding.py) | 人工表单生命周期与图暂停事件之间有适配层，session/form 绑定集中处理；超时和完成为不同事件。 对应：T06、T08、T16。 | Dify 使用附加条件的 Apache 许可；多租户及前端品牌有单独条款。参考设计、独立实现，不拷贝平台源码或工作流编辑器。 许可：Dify 修改版 Apache-2.0 条款；见 LICENSE。 |
| A08 · [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 资料解析、元数据过滤、检索、引用、Agent 与前端证据；证据架构相近。[README](https://github.com/infiniflow/ragflow/blob/519e7d98a5651564d4e35d6648f006cba4baaf4f/README.md)；[关键实现](https://github.com/infiniflow/ragflow/blob/519e7d98a5651564d4e35d6648f006cba4baaf4f/docs/guides/dataset/retrieval_testing.md)；[补充实现](https://github.com/infiniflow/ragflow/blob/519e7d98a5651564d4e35d6648f006cba4baaf4f/internal/agent/component/prompts/citation.go) | 先独立验证来源 chunk 的召回、完整性和排序，再诊断生成；引用通过来源 ID/片段关联。 对应：T03、T04、T10、T15。 | 当前仓库包含 Go 服务演进，不能按旧印象描述为纯 Python；不引入整套文档引擎或把引用提示当事实正确证明。 许可：Apache-2.0。 |
| A09 · [langchain-ai/agent-inbox](https://github.com/langchain-ai/agent-inbox) | 待审核任务 inbox、接受/编辑/响应/忽略；人工审核前端架构相近。[README](https://github.com/langchain-ai/agent-inbox/blob/f1616f3e7998e7e4fd574545b9558554d88d9c49/README.md)；[关键实现](https://github.com/langchain-ai/agent-inbox/blob/f1616f3e7998e7e4fd574545b9558554d88d9c49/src/components/agent-inbox/hooks/use-interrupted-actions.tsx) | 结构化人工响应、不可用动作禁用、提交加载状态、错误反馈和 thread 刷新，适合证据审核 UI。 对应：T08、T11。 | 已归档；仅前端/HITL 层相似，浏览器存管理 key 的示例不是本项目生产身份方案。 许可：MIT。 |
| A10 · [langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui) | React 状态/stream、thread、interrupt 与人工恢复；Agent 前端相近。[README](https://github.com/langchain-ai/agent-chat-ui/blob/cf72cb0f68a04d24db93eb19afb2d46f3a5261d4/README.md)；[关键实现](https://github.com/langchain-ai/agent-chat-ui/blob/cf72cb0f68a04d24db93eb19afb2d46f3a5261d4/src/components/thread/agent-inbox/hooks/use-interrupted-actions.tsx)；[补充实现](https://github.com/langchain-ai/agent-chat-ui/blob/cf72cb0f68a04d24db93eb19afb2d46f3a5261d4/src/providers/Stream.tsx) | 人工 decisions 使用 command.resume 提交；UI 统一运行状态、错误与恢复，不将已提交等同最终完成。 对应：T08、T11。 | 本项目是审核工作台而非纯聊天；保留业务页面，不整体迁移到 Next.js 或强制流式聊天。 许可：MIT。 |


**13. 内容与应用场景参考：12 个不同项目**

| 编号与项目 | 相似粒度与已读证据 | 借鉴做法与任务 | 适用限制与许可 |
| --- | --- | --- | --- |
| S01 · [StJudeWasHere/seonaut](https://github.com/StJudeWasHere/seonaut) | SEO 站点审计、问题聚合、sitemap 和按严重度报告；场景直接相关。[README](https://github.com/StJudeWasHere/seonaut/blob/880b312c28fab8b0bf7fe4f9449dc4746dbb82ff/README.md)；[关键实现](https://github.com/StJudeWasHere/seonaut/blob/880b312c28fab8b0bf7fe4f9449dc4746dbb82ff/internal/issues/page/reporters.go)；[补充实现](https://github.com/StJudeWasHere/seonaut/blob/880b312c28fab8b0bf7fe4f9449dc4746dbb82ff/internal/crawler/sitemap_checker.go) | 单页规则注册清晰，标题/索引/canonical/链接问题独立；sitemap 解析与页面问题报告分开。 对应：T02。 | Go 技术栈无需迁移；不同 SEO 问题的重要性需按 B2B 页面重新定义，不能复制规则总分。 许可：MIT。 |
| S02 · [GoogleChrome/lighthouse](https://github.com/GoogleChrome/lighthouse) | 网页证据采集、独立审计和报告；技术审计场景直接相关。[README](https://github.com/GoogleChrome/lighthouse/blob/079cfeff048a2534f6c6d4d49ea2e5f37b645048/readme.md)；[关键实现](https://github.com/GoogleChrome/lighthouse/blob/079cfeff048a2534f6c6d4d49ea2e5f37b645048/docs/architecture.md)；[补充实现](https://github.com/GoogleChrome/lighthouse/blob/079cfeff048a2534f6c6d4d49ea2e5f37b645048/core/audits/audit.js) | Gatherer → artifacts → Audit → Report 分层；规则读取冻结产物，报告显示证据、类别与错误。 对应：T02、T13。 | 浏览器实验室审计不代表现场用户性能；只作为可选适配器，不强迫所有页面运行 Chrome。 许可：Apache-2.0。 |
| S03 · [eliasdabbas/advertools](https://github.com/eliasdabbas/advertools) | SEO 采集、robots/sitemap、页面数据分析；采集场景相关。[README](https://github.com/eliasdabbas/advertools/blob/59dc2be7a98ab3bab2a711efacd5023f8ecffa14/README.rst)；[关键实现](https://github.com/eliasdabbas/advertools/blob/59dc2be7a98ab3bab2a711efacd5023f8ecffa14/advertools/sitemaps.py) | sitemap/index 可递归或仅索引探索；记录 loc、lastmod、来源 sitemap、etag 与 download_date。 对应：T02、T10。 | 数据分析库不提供企业审批；复制递归功能时必须增加本项目原有网络与资源上限。 许可：MIT。 |
| S04 · [firecrawl/firecrawl](https://github.com/firecrawl/firecrawl) | 网页转模型上下文、多采集引擎和结构化输出；资料采集相关。[README](https://github.com/firecrawl/firecrawl/blob/1b5ffa68a72010544cbbc152356f018916c23127/README.md)；[关键实现](https://github.com/firecrawl/firecrawl/blob/1b5ffa68a72010544cbbc152356f018916c23127/apps/api/src/scraper/scrapeURL/engines/index.ts) | fetch、浏览器、PDF 等引擎通过显式能力和策略选择；采集结果与格式/来源信息分离。 对应：T10。 | 根仓库为 AGPL-3.0，部分能力依赖服务；不直接复制核心实现或代理全部流量，只参考 adapter 设计。 许可：AGPL-3.0。 |
| S05 · [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | 为 LLM 提取网页正文、Markdown 与引用；内容证据相关。[README](https://github.com/unclecode/crawl4ai/blob/e5d2e786d1a101225f3f6a3e6fd344d76eeb13af/README.md)；[关键实现](https://github.com/unclecode/crawl4ai/blob/e5d2e786d1a101225f3f6a3e6fd344d76eeb13af/crawl4ai/markdown_generation_strategy.py)；[补充实现](https://github.com/unclecode/crawl4ai/blob/e5d2e786d1a101225f3f6a3e6fd344d76eeb13af/crawl4ai/content_filter_strategy.py) | content filter 与 Markdown generation 分离，原始/过滤内容和引用分别输出；抽取策略可替换。 对应：T10。 | 产品规格表需单独保真；不启用绕过反爬或额外域名访问。作为可选库前验证版本/部署成本。 许可：Apache-2.0。 |
| S06 · [mozilla/readability](https://github.com/mozilla/readability) | 网页主要内容与标题抽取；正文处理的局部场景相关。[README](https://github.com/mozilla/readability/blob/ab4027a8b37669745016869a37a504727992b2ba/README.md)；[关键实现](https://github.com/mozilla/readability/blob/ab4027a8b37669745016869a37a504727992b2ba/Readability.js) | 基于文档 title 和正文结构提取元数据，清理导航/样式；保留来源与抽取前内容。 对应：T02、T10。 | 这是抽取库，不是完整 Agent 产品；文章导向启发式可能丢产品参数，不能直接覆盖原 HTML 或照搬标题修剪。 许可：Apache-2.0。 |
| S07 · [Yoast/wordpress-seo](https://github.com/Yoast/wordpress-seo) | 站点 title/canonical/schema 与 CMS 内容治理；页面字段相关。[README](https://github.com/Yoast/wordpress-seo/blob/846eed2811d2fd996e539969f9381beafbbdaa1a/README.md)；[关键实现](https://github.com/Yoast/wordpress-seo/blob/846eed2811d2fd996e539969f9381beafbbdaa1a/src/generators/schema-generator.php)；[补充实现](https://github.com/Yoast/wordpress-seo/blob/846eed2811d2fd996e539969f9381beafbbdaa1a/src/presenters/canonical-presenter.php) | 页面上下文到 metadata/schema 输出采用专用 presenter/generator；内容和页面模板不是同一层。 对应：T02、T12。 | license.txt 为 GPL v3 文本；参考字段分层，独立实现，不复制 PHP 插件逻辑。其规则不等于所有站点的 canonical 政策。 许可：GPL v3 文本；见 license.txt。 |
| S08 · [TheCraigHewitt/seomachine](https://github.com/TheCraigHewitt/seomachine) | SEO 研究→写作→优化→草稿发布与分析；内容应用场景直接相关。[README](https://github.com/TheCraigHewitt/seomachine/blob/e818d5e38551a931333381d69c43da6e767ec775/README.md)；[关键实现](https://github.com/TheCraigHewitt/seomachine/blob/e818d5e38551a931333381d69c43da6e767ec775/data_sources/modules/wordpress_publisher.py)；[补充实现](https://github.com/TheCraigHewitt/seomachine/blob/e818d5e38551a931333381d69c43da6e767ec775/data_sources/modules/google_search_console.py) | WordPress adapter 创建 draft 并保留发布信息；Search Console 返回 query/clicks/impressions/CTR 等真实数据字段。 对应：T12、T13、T18。 | 偏长文章而非采购规格；内容长度/评分和多角色提示不能代替企业事实审核。WordPress 凭据需服务端配置。 许可：MIT。 |
| S09 · [towfiqi/serpbear](https://github.com/towfiqi/serpbear) | 搜索位置、计划监测、历史记录与错误；长期观测场景相关。[README](https://github.com/towfiqi/serpbear/blob/a1328fb4142dde40e0011c646339c4d4dbabdaca/README.md)；[关键实现](https://github.com/towfiqi/serpbear/blob/a1328fb4142dde40e0011c646339c4d4dbabdaca/cron.js)；[补充实现](https://github.com/towfiqi/serpbear/blob/a1328fb4142dde40e0011c646339c4d4dbabdaca/utils/parseKeywords.ts) | 监测配置、定时调度、历史结果、最后更新错误和数据展示分离，适合固定 cohort 的趋势报告。 对应：T13、T14、T18。 | SERP 排名不是 AI 引用；不照搬其抓取方式或把位置趋势等同采购询盘收益。 许可：MIT。 |
| S10 · [firecrawl/firegeo](https://github.com/firecrawl/firegeo) | 品牌、竞争者、采购/市场 prompts 与多模型回答；GEO 监测直接相关。[README](https://github.com/firecrawl/firegeo/blob/5bef51fe5ab3bed46a58b8825416f06e34309d7f/README.md)；[关键实现](https://github.com/firecrawl/firegeo/blob/5bef51fe5ab3bed46a58b8825416f06e34309d7f/lib/brand-detection-utils.ts)；[补充实现](https://github.com/firecrawl/firegeo/blob/5bef51fe5ab3bed46a58b8825416f06e34309d7f/app/api/brand-monitor/analyze/route.ts) | 品牌别名/后缀/词边界配置，分析请求绑定 session 和 prompts；可借鉴品牌比较与原回答 UI。 对应：T05、T11、T14、T18。 | SaaS starter 不证明指标可靠；其分析 credits 不是模型真实美元费用，不继承 UI 分数权重。 许可：MIT。 |
| S11 · [ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool](https://github.com/ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool) | AI 可见性、原回答、citations、模型/表面与市场；监测场景直接相关。[README](https://github.com/ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool/blob/2e56fa49497a1552ec0aaa84ec2c1dc2148cee2d/README.md)；[关键实现](https://github.com/ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool/blob/2e56fa49497a1552ec0aaa84ec2c1dc2148cee2d/packages/lib/src/providers/types.ts)；[补充实现](https://github.com/ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool/blob/2e56fa49497a1552ec0aaa84ec2c1dc2148cee2d/packages/lib/src/providers/registry/openai-api.ts) | ScrapeResult 明确 text/raw/webQueries/citations/modelVersion，ProviderOptions 分开 webSearch/market/language；不同来源保持类型。 对应：T05、T07、T11、T14、T18。 | README 的平台覆盖需逐一实测；不复制模型名、价格或默认 geo 行为。LICENSE.md 为 MIT，声明衍生自 Elmo，复用需保留版权。 许可：MIT；含 Elmo 衍生声明。 |
| S12 · [danishashko/geo-aeo-tracker](https://github.com/danishashko/geo-aeo-tracker) | 按 prompt/市场跨 AI 表面采样、引用机会、历史分析；监测场景直接相关。[README](https://github.com/danishashko/geo-aeo-tracker/blob/949c51687b108e4aee538f9634152c99c4b9ab08/README.md)；[关键实现](https://github.com/danishashko/geo-aeo-tracker/blob/949c51687b108e4aee538f9634152c99c4b9ab08/lib/server/brightdata-scraper.ts)；[补充实现](https://github.com/danishashko/geo-aeo-tracker/blob/949c51687b108e4aee538f9634152c99c4b9ab08/app/api/analyze/route.ts) | 采样返回 provider/prompt/answer/sources/snapshotId；数据采集与 LLM 分析分离，保留采集来源与表面信息。 对应：T05、T14、T18。 | 依赖外部 Bright Data 等服务且有本地缓存；不复制“全部并行”或综合分数，预算与引用真实性需独立验证。 许可：MIT。 |


**14. 补充基础组件：5 个，不重复计数**

| 编号与项目 | 相似粒度与已读证据 | 借鉴做法与任务 | 适用限制与许可 |
| --- | --- | --- | --- |
| X01 · [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | 项目已使用的有状态图框架。[README](https://github.com/langchain-ai/langgraph/blob/b36b1d58a8b408455b512cfad3b1b26e02927282/README.md)；[关键实现](https://github.com/langchain-ai/langgraph/blob/b36b1d58a8b408455b512cfad3b1b26e02927282/libs/langgraph/langgraph/types.py) | interrupt 恢复从节点开头重执行；Command(resume)、持久 checkpoint 与节点副作用边界必须正确。 对应：T08。 | 组件参考，不计入前面 10 个架构项目；框架持久化不能替代外部发布/计费幂等。 许可：MIT。 |
| X02 · [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 类型化输出、输出验证和 usage limits。[README](https://github.com/pydantic/pydantic-ai/blob/1ef88349d309ae95782079f4c99bbbefdd6ff5a4/README.md)；[关键实现](https://github.com/pydantic/pydantic-ai/blob/1ef88349d309ae95782079f4c99bbbefdd6ff5a4/docs/output.md)；[补充实现](https://github.com/pydantic/pydantic-ai/blob/1ef88349d309ae95782079f4c99bbbefdd6ff5a4/pydantic_ai_slim/pydantic_ai/usage.py) | 嵌套 JSON schema 与业务 validator 分层；请求上限在调用前、token/成本可在响应后检查。 对应：T04、T07。 | 保持 LangGraph，借鉴 schema 与预算接口，不新增第二套编排；结构正确不等于事实正确。 许可：MIT。 |
| X03 · [DBOS-inc/dbos-transact-py](https://github.com/DBOS-inc/dbos-transact-py) | 数据库支持的持久 workflow/queue。[README](https://github.com/DBOS-inc/dbos-transact-py/blob/fd07ca9b54501086b54bb4f5fcac6140ef6f5c89/README.md)；[关键实现](https://github.com/DBOS-inc/dbos-transact-py/blob/fd07ca9b54501086b54bb4f5fcac6140ef6f5c89/dbos/_queue.py)；[补充实现](https://github.com/DBOS-inc/dbos-transact-py/blob/fd07ca9b54501086b54bb4f5fcac6140ef6f5c89/dbos/_core.py) | queue 并发/速率/分区上限和工作结果持久化；用恢复/失败窗口验证效果，而非只测 happy path。 对应：T01、T03、T07、T08、T17。 | 先完善现有 PostgreSQL 队列，不同时引入 DBOS/Temporal/Celery 三套任务系统；外部调用结果不明仍需对账。 许可：MIT。 |
| X04 · [langfuse/langfuse](https://github.com/langfuse/langfuse) | 模型 trace、费用、数据集和评测。[README](https://github.com/langfuse/langfuse/blob/9d168c646be62f497d3e03957d66cb09b60fd7eb/README.md)；[关键实现](https://github.com/langfuse/langfuse/blob/9d168c646be62f497d3e03957d66cb09b60fd7eb/packages/shared/src/server/repositories/observations.ts) | observation/trace 关联时延与费用，评测数据集、用户反馈、人工标注与代码 evaluator 可组合。 对应：T07、T15、T16。 | LICENSE 将 ee 目录单列许可，其余主体 MIT；轻量日志先行，外部导出可选，敏感正文不默认上报。 许可：主体 MIT；ee 目录另有许可。 |
| X05 · [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | 多模型/多提示对比、断言和事实评测。[README](https://github.com/promptfoo/promptfoo/blob/0e1e076298e1f2fd27fd80fd6a413047293c0eae/README.md)；[关键实现](https://github.com/promptfoo/promptfoo/blob/0e1e076298e1f2fd27fd80fd6a413047293c0eae/examples/compare-gpt-vs-claude-vs-gemini/promptfooconfig.yaml)；[补充实现](https://github.com/promptfoo/promptfoo/blob/0e1e076298e1f2fd27fd80fd6a413047293c0eae/src/assertions/factuality.ts) | 同一 tests/prompts 对比 providers，断言与 latency 明确；事实 Judge 失败仍是失败，不能被反向断言变成通过。 对应：T00、T04、T15。 | 样例模型 ID/阈值不照搬；LLM judge 不能代替独立人工盲评或作为唯一发布守卫。 许可：MIT。 |


**15. 参考固定版本与读取状态**

| 编号 | 固定提交 | 提交时间（UTC） | 读取文件数 | 状态 |
| --- | --- | --- | --- | --- |
| A01 | [cb740b656d7a](https://github.com/fastapi/full-stack-fastapi-template/commit/cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7) | 2026-09-01T18:42:01Z | 3 | 未归档；本研究未运行 |
| A02 | [f7faab38fa53](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template/commit/f7faab38fa5332669627139b27c4f2dc4178649e) | 2026-09-25T18:37:53Z | 3 | 未归档；本研究未运行 |
| A03 | [2bd004a51e5d](https://github.com/NicholasGoh/fastapi-mcp-langgraph-template/commit/2bd004a51e5d741e8eaf5bb01716b6a673684a53) | 2025-06-13T10:11:58Z | 3 | 未归档；本研究未运行 |
| A04 | [1b7d2e80db9f](https://github.com/langchain-ai/open_deep_research/commit/1b7d2e80db9faa586165c60e09096dbbfd483a64) | 2026-08-10T18:13:29Z | 3 | 已归档，设计参考 |
| A05 | [f840e843d3e2](https://github.com/bytedance/deer-flow/commit/f840e843d3e2db1485cb65ceae73a5525aa80193) | 2026-10-01T12:36:35Z | 3 | 未归档；本研究未运行 |
| A06 | [0957c301ed06](https://github.com/assafelovic/gpt-researcher/commit/0957c301ed06c2a5857b834358c7227c739041d4) | 2026-09-26T16:56:07Z | 2 | 未归档；本研究未运行 |
| A07 | [f004f6b67d64](https://github.com/langgenius/dify/commit/f004f6b67d6408e11aeb5a2cc7a14d6c9ea628c3) | 2026-10-01T10:27:08Z | 4 | 未归档；本研究未运行 |
| A08 | [519e7d98a565](https://github.com/infiniflow/ragflow/commit/519e7d98a5651564d4e35d6648f006cba4baaf4f) | 2026-10-01T08:58:00Z | 3 | 未归档；本研究未运行 |
| A09 | [f1616f3e7998](https://github.com/langchain-ai/agent-inbox/commit/f1616f3e7998e7e4fd574545b9558554d88d9c49) | 2026-09-18T22:08:27Z | 2 | 已归档，设计参考 |
| A10 | [cf72cb0f68a0](https://github.com/langchain-ai/agent-chat-ui/commit/cf72cb0f68a04d24db93eb19afb2d46f3a5261d4) | 2026-09-28T07:42:06Z | 3 | 未归档；本研究未运行 |
| S01 | [880b312c28fa](https://github.com/StJudeWasHere/seonaut/commit/880b312c28fab8b0bf7fe4f9449dc4746dbb82ff) | 2026-05-23T19:00:59Z | 3 | 未归档；本研究未运行 |
| S02 | [079cfeff048a](https://github.com/GoogleChrome/lighthouse/commit/079cfeff048a2534f6c6d4d49ea2e5f37b645048) | 2026-09-30T21:24:13Z | 3 | 未归档；本研究未运行 |
| S03 | [59dc2be7a98a](https://github.com/eliasdabbas/advertools/commit/59dc2be7a98ab3bab2a711efacd5023f8ecffa14) | 2026-06-30T15:06:46Z | 2 | 未归档；本研究未运行 |
| S04 | [1b5ffa68a720](https://github.com/firecrawl/firecrawl/commit/1b5ffa68a72010544cbbc152356f018916c23127) | 2026-10-01T13:39:53Z | 3 | 未归档；本研究未运行 |
| S05 | [e5d2e786d1a1](https://github.com/unclecode/crawl4ai/commit/e5d2e786d1a101225f3f6a3e6fd344d76eeb13af) | 2026-09-25T06:35:50Z | 3 | 未归档；本研究未运行 |
| S06 | [ab4027a8b376](https://github.com/mozilla/readability/commit/ab4027a8b37669745016869a37a504727992b2ba) | 2026-07-09T11:32:19Z | 2 | 未归档；本研究未运行 |
| S07 | [846eed2811d2](https://github.com/Yoast/wordpress-seo/commit/846eed2811d2fd996e539969f9381beafbbdaa1a) | 2026-09-29T09:27:14Z | 4 | 未归档；本研究未运行 |
| S08 | [e818d5e38551](https://github.com/TheCraigHewitt/seomachine/commit/e818d5e38551a931333381d69c43da6e767ec775) | 2026-08-05T19:04:21Z | 3 | 未归档；本研究未运行 |
| S09 | [a1328fb4142d](https://github.com/towfiqi/serpbear/commit/a1328fb4142dde40e0011c646339c4d4dbabdaca) | 2026-05-14T07:30:56Z | 3 | 未归档；本研究未运行 |
| S10 | [5bef51fe5ab3](https://github.com/firecrawl/firegeo/commit/5bef51fe5ab3bed46a58b8825416f06e34309d7f) | 2025-07-21T20:25:30Z | 3 | 未归档；本研究未运行 |
| S11 | [2e56fa49497a](https://github.com/ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool/commit/2e56fa49497a1552ec0aaa84ec2c1dc2148cee2d) | 2026-08-27T10:24:40Z | 4 | 未归档；本研究未运行 |
| S12 | [949c51687b10](https://github.com/danishashko/geo-aeo-tracker/commit/949c51687b108e4aee538f9634152c99c4b9ab08) | 2026-08-12T11:11:38Z | 3 | 未归档；本研究未运行 |
| X01 | [b36b1d58a8b4](https://github.com/langchain-ai/langgraph/commit/b36b1d58a8b408455b512cfad3b1b26e02927282) | 2026-10-01T09:00:10Z | 2 | 未归档；本研究未运行 |
| X02 | [1ef88349d309](https://github.com/pydantic/pydantic-ai/commit/1ef88349d309ae95782079f4c99bbbefdd6ff5a4) | 2026-10-01T13:43:39Z | 3 | 未归档；本研究未运行 |
| X03 | [fd07ca9b5450](https://github.com/DBOS-inc/dbos-transact-py/commit/fd07ca9b54501086b54bb4f5fcac6140ef6f5c89) | 2026-10-01T01:17:07Z | 3 | 未归档；本研究未运行 |
| X04 | [9d168c646be6](https://github.com/langfuse/langfuse/commit/9d168c646be62f497d3e03957d66cb09b60fd7eb) | 2026-10-01T12:55:04Z | 3 | 未归档；本研究未运行 |
| X05 | [0e1e076298e1](https://github.com/promptfoo/promptfoo/commit/0e1e076298e1f2fd27fd80fd6a413047293c0eae) | 2026-10-01T06:03:01Z | 3 | 未归档；本研究未运行 |

已读取的许可直接证据：[Dify LICENSE](https://github.com/langgenius/dify/blob/f004f6b67d6408e11aeb5a2cc7a14d6c9ea628c3/LICENSE)、[Firecrawl 根许可证](https://github.com/firecrawl/firecrawl/blob/1b5ffa68a72010544cbbc152356f018916c23127/LICENSE)、[Yoast license.txt](https://github.com/Yoast/wordpress-seo/blob/846eed2811d2fd996e539969f9381beafbbdaa1a/license.txt)、[GetCito LICENSE.md](https://github.com/ai-search-guru/getcito-worlds-first-open-source-aio-aeo-or-geo-tool/blob/2e56fa49497a1552ec0aaa84ec2c1dc2148cee2d/LICENSE.md)、[Langfuse LICENSE](https://github.com/langfuse/langfuse/blob/9d168c646be62f497d3e03957d66cb09b60fd7eb/LICENSE). 这些项目的许可分类不能一律按 MIT 处理；依赖和源码复用范围按实际所用版本登记。

**16. 首轮执行包与最终交付格式**

首轮建议一次完成 T00–T05 的正确性分支，交付六项任务证据和 M0 矩阵；随后连续推进 T06–T14 的业务闭环。可用真实资料、Provider 与身份配置时同步准备 M2，不等待全部文档完工才发现接入条件缺失。T15 的 holdout 在开发改进前冻结，人工评阅材料尽早准备；T17 完成后先交付 M1，T18 的长期观察独立记录。

执行 AI 每轮结果按以下顺序返回：完成结果与当前 M 等级；已满足的 O/T 编号；源码 commit 与改动文件；实际测试/运行证据；待真实输入或未达标项；下一步具体任务。最终验收包应有 `STATUS.md`、`INPUTS.md`、迁移/运行/回滚手册、O01–O10 矩阵、C01–C16 结果、模型调用与费用摘要、匿名盲评与统计、真实站点部署/回滚证据、监测 cohort 数据和最终已知限制。

所有完成声明对应可定位证据。达到 M1 的代码交付有独立价值；实际站点、真实 Provider、独立人工评阅和业务观察分别验证后，才能升级对应 M2/M3 状态。
