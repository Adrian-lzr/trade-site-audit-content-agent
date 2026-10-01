# 可追溯知识库条目

这些条目是给事实审核、采购问题规划和内容改稿提供边界的工作资料，不是企业事实，也不直接产生排名、收录、AI 引用或询盘保证。每次使用前应检查来源页面是否更新，并把企业自身的规格、证书和商业条件记录为独立的 `Fact`。当前条目属于只读 `external_guidance`；它们不能满足企业声明的证据要求，也不能替代目标市场的法律意见。

| API `id` | 条目 | 可执行摘要（对应 `summary`） | 适用范围与边界（对应 `scope`） | 来源与版本信息 |
| --- | --- | --- | --- | --- |
| `google-seo-starter` | Google SEO 入门 | 内容应独特、实用、面向读者；没有自动获得第一名的秘诀，字数没有理想排名上下限。 | Google 一般 SEO 指引；不保证抓取、收录、排名或询盘。 | [Google Search Central](https://developers.google.com/search/docs/fundamentals/seo-starter-guide)，页面更新 2025-12-18，访问 2026-09-29；文档默认 CC BY 4.0；代码示例 Apache 2.0 |
| `google-ai-features` | Google AI 搜索功能 | AI 概览和 AI 模式沿用基础 SEO、搜索政策和内容质量要求，没有额外的 AI 专用 Schema。 | 仅适用于 Google AI 搜索功能，不能外推到其他平台；满足条件也不保证展示。 | [AI 功能和您的网站](https://developers.google.com/search/docs/appearance/ai-features)，页面更新 2025-12-31，访问 2026-09-29；文档默认 CC BY 4.0；代码示例 Apache 2.0 |
| `product-structured-data` | 商品结构化数据 | 结构化数据应与页面可见信息一致；商品展示取决于页面类型和资格条件。 | 询价型 B2B 页面需逐项核对展示资格；加标记不等于排名提升。 | [Google Product structured data](https://developers.google.com/search/docs/appearance/structured-data/product)，页面更新 2025-12-18，访问 2026-09-29；Google 文档默认 CC BY 4.0；Schema.org 词汇按 CC BY-SA 3.0 |
| `w3c-prov-o` | 来源追踪（PROV-O） | 将资料视为 Entity，将供应商、认证机构和审核人视为 Agent，将采集、核验和修订视为 Activity。 | 来源模型不证明来源真实或事实正确，仍需企业审核。 | [W3C PROV-O](https://www.w3.org/TR/prov-o/)，W3C Recommendation 2013-04-30，访问 2026-09-29；W3C 2023 Document License；复用时保留原文链接、版权和状态 |
| `ftc-ad-substantiation` | 广告事实证据 | 客观或隐含的产品声明应在发布前有合理依据；“经测试”“认证”等词会传达相应证据水平。 | FTC 美国消费者保护语境；这是 1984 年政策声明，不直接替代现行指南或其他市场法律判断。 | [FTC advertising substantiation policy](https://www.ftc.gov/legal-library/browse/ftc-policy-statement-regarding-advertising-substantiation)，日期 1984-11-23，访问 2026-09-29；只保存摘要与链接；使用前重新核验最新规则与执法材料 |
| `rfq-page-content-checklist` | 询价型 B2B 页面内容最低集 | 检查型号/规格、数量/批量、配置/定制、目的地/交付点/日期、包装/文件、MOQ/交期/贸易术语以及样品/备件/售后需求是否有对应资料。 | 这是采购信息完整性启发式，不证明转化、排名或询盘提升；缺失 MOQ、交期、运费和响应条件必须进入资料补充，不得由模型补写。 | [Schema.org Product](https://schema.org/Product)、[Google Product structured data](https://developers.google.com/search/docs/appearance/structured-data/product)，访问 2026-09-29；字段含义需结合企业事实和目标市场核验 |
| `product-eligibility-boundary` | Product snippets 与 Merchant listings 资格边界 | Product/Offer 标记、Merchant Center feed、价格/库存/购买路径与纯 RFQ 页面资格不同；结构化数据必须与页面可见信息一致。 | 只说明 Google 展示资格边界，不保证展示；询价页不得为了富结果臆造 price、availability 或购买路径。 | [Google Product structured data](https://developers.google.com/search/docs/appearance/structured-data/product)、[Merchant listings](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing)，访问 2026-09-29 |
| `technical-signal-interpretation` | 技术审计信号解释 | robots、noindex、canonical、sitemap 分别涉及抓取、索引指令、规范信号和发现入口；网络失败或权限限制应保留为 unknown/needs_review。 | 这是诊断信号解释，不等于处罚、收录或排名结论；不要把 robots 阻断直接写成“排名惩罚”。 | [Google robots.txt](https://developers.google.com/search/docs/crawling-indexing/robots/intro)、[RFC 9309](https://www.rfc-editor.org/rfc/rfc9309)，访问 2026-09-29；RFC 发布 2022-09 |
| `internationalized-b2b-pages` | 国际化 B2B 页面与 hreflang | 语言/区域版本应互指，canonical 与语言版本保持一致；目标市场和语言事实需要单独确认。 | hreflang 不能替代翻译、企业市场授权或本地法规判断，也不保证国际排名。 | [Google localized versions](https://developers.google.com/search/docs/specialty/international/localized-versions)，访问 2026-09-29 |
| `accessibility-content-usability` | 可访问性与内容可用性检查 | 标题层级、表单标签、键盘操作、对比度和错误提示影响询价流程可用性，应与自动审计结果分开记录。 | 单次自动检查不能宣称 WCAG 合规，也不能自动推出 SEO 或询盘提升。 | [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/)，W3C Recommendation 2023-10-05，访问 2026-09-29 |
| `source-freshness-fact-separation` | 来源时效与企业事实分层 | 外部规则应记录 source_date、accessed_at、valid_until、supersedes；规格、证书、交期和价格只能进工作区 Fact，并保留 reviewer、visibility 和版本。 | 外部条目不能填充缺失企业事实；持续更新页面的日期必须重新核验，不能伪造为永久有效。 | [W3C PROV-O](https://www.w3.org/TR/prov-o/)，W3C Recommendation 2013-04-30，访问 2026-09-29 |

## 来源更新批次一（2026-09-30）

以下 10 条官方/标准机构来源在 2026-09-30 核验可访问（HTTP HEAD 200）。动态来源没有可靠的单一发布日期时标为 `continuously maintained`，仍需每次使用前打开原文复核。

| API `id` | 条目 | 可执行摘要 | 适用边界 | 来源与版本信息 | 来源类型 |
| --- | --- | --- | --- | --- | --- |
| `google-search-essentials` | Google Search Essentials | 分开检查技术要求、垃圾内容政策和以人为本的内容原则。 | 仅 Google Search；不保证抓取、收录、排名、AI 展示或询盘。 | [Google Search Essentials](https://developers.google.com/search/docs/essentials)，页面更新 2025-12-10，访问 2026-09-30 | 官方指导 |
| `google-search-console-observation` | Search Console 观测口径 | 记录 Search Console 属性、时间窗和报告限制，把它用于站点表现观测与诊断。 | 需要已验证站点和真实数据；报告延迟、聚合或采样不构成因果证明。 | [How To Use Search Console](https://developers.google.com/search/docs/monitor-debug/search-console-start)，页面更新 2025-12-10，访问 2026-09-30 | 官方指导 |
| `google-structured-data-policies` | 结构化数据政策 | 标记需准确代表页面可见主要内容并符合政策。 | 只约束 Google 富结果政策；不保证资格或展示，不能补造价格、库存、认证、评价。 | [General Structured Data Guidelines](https://developers.google.com/search/docs/appearance/structured-data/sd-policies)，页面更新 2026-07-10，访问 2026-09-30 | 官方指导 |
| `ftc-green-guides` | FTC Green Guides | 环境声明应有相应依据；笼统或无法证明的措辞进入人工复核。 | 美国 FTC 消费者广告语境；不外推其他市场，也不替代具体行业/州规则审查。 | [FTC Green Guides](https://www.ftc.gov/legal-library/browse/rules/green-guides)，持续维护，访问 2026-09-30 | 官方监管机构 |
| `ftc-endorsements-reviews` | FTC 评价与背书披露 | 检查评价、背书和影响者内容的真实性及商业关系披露。 | 美国消费者广告语境；不证明企业评价、认证或产品性能，不外推全球广告法。 | [FTC Endorsements, Influencers, and Reviews](https://www.ftc.gov/business-guidance/advertising-marketing/endorsements-influencers-reviews)，持续维护，访问 2026-09-30 | 官方监管机构 |
| `eu-access2markets` | EU Access2Markets 查询入口 | 按出口国、进口国、商品编码和日期查询关税、原产地规则、产品要求与程序。 | 查询结果依赖 HS/CN 分类、原产地和日期；不能当作产品合规或市场准入结论。 | [EU Access2Markets](https://trade.ec.europa.eu/access-to-markets/en/home)，动态查询服务，访问 2026-09-30 | 官方贸易查询 |
| `usitc-hts` | USITC Harmonized Tariff Schedule | 美国进口税率和措施以当前 HTS 版本及具体税号为准，模型不能凭产品名称猜税号。 | 美国进口关税范围；不等于许可、安全或产品合规结论。 | [USITC HTS](https://hts.usitc.gov/)，持续维护，访问 2026-09-30 | 官方贸易查询 |
| `uk-trade-tariff` | UK Trade Tariff | 按商品编码、原产地和交易日期查询英国关税与 VAT。 | 仅英国；税率随日期与贸易安排变化，不外推到欧盟。 | [UK Trade Tariff](https://www.gov.uk/trade-tariff)，持续维护，访问 2026-09-30 | 官方贸易查询 |
| `wto-tariff-data` | WTO Tariff and Trade Data | 用于关税数据与成员承诺的跨市场比较。 | 统计或绑定税率不是单票分类、原产地判定、最终税额或许可结论。 | [WTO Tariff and Trade Data](https://www.wto.org/english/tratop_e/tariffs_e/tariff_data_e.htm)，动态数据入口，访问 2026-09-30 | 政府间组织数据 |
| `icc-incoterms-2020` | ICC Incoterms 2020 | 报价写明正式规则名称、版本和指定地点，并核对费用、风险、运输与保险责任。 | 不决定关税、产品合规、所有权、价格或适用法律；不复制 ICC 规则正文。 | [ICC Incoterms 2020](https://iccwbo.org/business-solutions/incoterms-rules/incoterms-2020/)，页面 dateModified 2026-07-03，访问 2026-09-30 | 标准机构 |

## 来源更新批次二（2026-09-30）

以下 5 条新增指导面向已有的页面改稿和阀门采购场景。4 个外部来源页面以 GET 复核均返回 `200`；来源摘要只给选题和核验边界，不代替原文或企业技术资料。

| API `id` | 条目 | 可执行摘要 | 适用边界 | 来源与版本信息 | 来源类型 |
| --- | --- | --- | --- | --- | --- |
| `google-title-links` | Google 搜索结果标题链接 | 标题应简洁、明确并描述页面；Google 还可能参考页面主标题、显著文字及链接文本生成标题链接。 | 仅适用于 Google Search；不保证采用页面标题、展示或排名。 | [Influencing Title Links](https://developers.google.com/search/docs/appearance/title-link)，持续维护，访问 2026-09-30 | 官方指导 |
| `google-meta-descriptions` | Google 搜索摘要与 Meta Description | Meta description 可提供贴合页面的简洁摘要；Google 也可能根据页面内容生成摘要，展示长度没有固定字符承诺。 | 仅适用于 Google 搜索摘要；不保证采用描述或带来排名、点击提升。 | [How to Write Meta Descriptions](https://developers.google.com/search/docs/appearance/snippet)，持续维护，访问 2026-09-30 | 官方指导 |
| `valve-selection-input-checklist` | 阀门选型资料收集清单 | 先收集介质及组成、压力与温度范围、流量/工况、阀门功能、材料、端部连接与尺寸、驱动/控制方式及待核对标准版本；资料不足时创建补充问题。 | 内部信息完整性启发式；不推荐具体型号/等级，不判材料兼容、工程设计或标准适用性。 | [ASME B16.34 标准目录](https://www.asme.org/codes-standards/find-codes-standards/b16-34-valves-flanged-threaded-welding-end)，持续维护，访问 2026-09-30；仅引用目录，不复制标准正文 | 内部启发式 |
| `product-document-applicability-checklist` | 产品证书与测试报告适用性核对 | 核对文件类型、签发方、编号/版本、有效期、法规/标准版本、适用型号/配置/批次、测试范围与目的地；缺少信息时要求补资料。 | 内部证据核对清单；不凭名称推断文件真伪、有效性或产品合规，不代替认证机构判断。 | 内部清单，无外部来源；欧盟法规入口单列在 `eu-pressure-equipment-directive` | 内部启发式 |
| `eu-pressure-equipment-directive` | 欧盟压力设备指令（PED） | 欧盟委员会 PED 2014/68/EU 说明页用于查找压力设备法规与合格评定框架；适用性取决于设备定义、参数、类别和排除项。 | 欧盟压力设备入口；不能凭“阀门”类别推断 PED 适用、分类、CE 标志或合规状态。 | [Pressure Equipment Directive](https://single-market-economy.ec.europa.eu/sectors/pressure-equipment-and-gas-appliances/pressure-equipment-sector/pressure-equipment-directive_en)，指令日期 2014-05-15，页面访问 2026-09-30 | 官方监管机构 |

## 阀门采购资料补充批次三（2026-09-30）

本批增加三条内部资料完整性清单，回应 20 个合成阀门采购问题中的执行器、测试记录、图纸和运维资料主题。它们没有外部标准来源，不替代工程审核，也不证明供应商拥有所列文件；缺少产品事实时应创建补资料项。

| API `id` | 条目 | 可执行摘要 | 适用边界 | 来源与版本信息 | 来源类型 |
| --- | --- | --- | --- | --- | --- |
| `valve-actuator-input-checklist` | 阀门执行器与自动化资料清单 | 收集型号/配置、开关或调节功能、失效位置、工艺压差、动作频次、气源/电源、控制信号、接口和环境条件；请供应商工程人员依据已确认负载与工况校核。 | 不计算扭矩/推力，不推荐执行器，也不判定控制、安全或危险区域适用性。 | 内部整理，2026-09-30；无外部来源 | 内部启发式 |
| `valve-test-record-applicability-checklist` | 阀门测试记录与批次适用性核对 | 对照采购要求核对文件类型、标准版本、测试范围、型号/配置、序列号或批次、日期、结果与验收依据、签发/见证方及偏差记录。 | 不凭样本报告、文件标题或单一批次记录推断其他配置、整批产品、证书有效性或市场合规。 | 内部整理，2026-09-30；无外部来源 | 内部启发式 |
| `valve-drawing-installation-maintenance-checklist` | 阀门图纸、安装与维护资料清单 | 核对准确型号、口径、连接、配置和版本；按项目需要索取数据表、尺寸/总装图、安装说明、检修手册和备件清单。 | 不提供具体尺寸、安装间隙、维护周期或备件型号；替换适配须由工程人员核对现场和图纸。 | 内部整理，2026-09-30；无外部来源 | 内部启发式 |

## `/api/knowledge` 响应契约

当前接口合并 11 条既有记录和 18 条补充来源，共 29 条唯一 `external_guidance`。响应字段包括：`id`、`entry_type`、`claim_type`、`title`、`summary`、`scope`、`market_scope`、`source_url`、`source_date`、`accessed_at`、`last_verified_at`、`freshness_policy`、`valid_until`、`license`、`source_kind` 和 `topic_tags`。`source_kind` 将官方指导、监管机构、标准机构、政府贸易查询与内部启发式分开；纯内部清单的 `source_url` 可为空，模型输入只要求条目 ID 和摘要；`source_date` 可为 ISO 日期或 `continuously maintained`，`valid_until` 当前是人工复核提示而不是自动过期时间。接口支持按 `market` 和精确 `claim_type` 过滤，不接受写入。

内容 Worker 在生成时根据采购问题、改稿要求、产品/用途/采购阶段和目标市场检索最多 8 条指导；主题相关性高于单纯市场词命中，市场专属条目还受市场范围约束。检索会归一常见英文复数，降低通用词带来的误命中；RFQ 清单按题目相关性选择，只有来源时效/事实分层边界常驻。指导以 ID、摘要、适用范围、市场范围、可选来源 URL 与核验元数据组成独立的 `external_guidance` 传给模型。模型可用相关指导改善写作结构、清晰度、信息完整度和资料补充问题，也可用于避免越界措辞；不得把指导当企业证据或法规/工程适用性结论，产品/供应商主张仍只能由当前、已确认、可公开的工作区 `Fact` 支持。所选指导摘要参与生成 ID，内容更新时不会误用旧输入的模型缓存。Fixture gateway 保持确定性，不把指导转换为企业事实。

## 建议字段

当前目录仍是代码内维护的只读种子，并未持久化或自动抓取来源页面。后续若要支持编辑、版本历史和到期队列，再增加 `evidence_artifact`、`verified_by`、`review_due_at`、`supersedes` 等字段并设计迁移；不要把 `valid_until=revalidate-before-use` 当日期排序。企业事实仍必须有工作区归属、版本、可见性和审核记录；批量导入也只进入 `proposed`，不得绕过审核。
