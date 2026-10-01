"""Curated external guidance and internal checklists used for review context.

These records describe policy, evidence, or market-boundary guidance. They are
never enterprise facts and must not be used to invent product claims.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


SUPPLEMENTAL_KNOWLEDGE_ENTRIES: tuple[dict[str, Any], ...] = (
    {
        "id": "google-search-essentials",
        "entry_type": "external_guidance",
        "claim_type": "search_policy_boundary",
        "title": "Google Search Essentials",
        "summary": "将技术要求、垃圾内容政策和以人为本的内容原则分开检查；符合基础要求也不保证抓取、收录或排名。",
        "scope": "Google Search 基础要求与政策边界；不保证 AI 展示、排名或询盘。",
        "market_scope": "Google Search；不外推其他搜索引擎",
        "source_url": "https://developers.google.com/search/docs/essentials",
        "source_date": "2025-12-10",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；使用前复核页面状态",
        "source_kind": "official_guidance",
        "topic_tags": "search,policy,spam,content-quality",
    },
    {
        "id": "google-search-console-observation",
        "entry_type": "external_guidance",
        "claim_type": "search_observation_boundary",
        "title": "Search Console 观测口径",
        "summary": "Search Console 是查看 Google 搜索表现和诊断问题的证据入口；记录属性、时间窗和报告限制，不把报告当作因果证明。",
        "scope": "需要站点验证和实际数据；报告可能延迟、聚合或采样，不能证明排名因果或询盘增长。",
        "market_scope": "Google Search Console；授权站点观测",
        "source_url": "https://developers.google.com/search/docs/monitor-debug/search-console-start",
        "source_date": "2025-12-10",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；使用前复核页面状态",
        "source_kind": "official_guidance",
        "topic_tags": "search-console,measurement,observation,attribution",
    },
    {
        "id": "google-structured-data-policies",
        "entry_type": "external_guidance",
        "claim_type": "structured_data_policy",
        "title": "结构化数据政策",
        "summary": "结构化数据必须准确代表页面可见主要内容并遵守垃圾内容政策；违反政策可能失去富结果资格。",
        "scope": "Google 结构化数据富结果政策；遵守政策仍不保证展示或排名，不能臆造价格、库存、认证或评价。",
        "market_scope": "Google Search structured-data features",
        "source_url": "https://developers.google.com/search/docs/appearance/structured-data/sd-policies",
        "source_date": "2026-07-10",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；使用前复核资格要求",
        "source_kind": "official_guidance",
        "topic_tags": "structured-data,policy,visible-content,rich-results",
    },
    {
        "id": "ftc-green-guides",
        "entry_type": "external_guidance",
        "claim_type": "environmental_claim_evidence",
        "title": "FTC Green Guides",
        "summary": "美国市场的环境和可持续声明需要相应依据，笼统或无法证明的表述应进入人工复核。",
        "scope": "美国 FTC 消费者广告语境；不外推到其他市场，也不替代具体行业、州或联邦规则核验。",
        "market_scope": "美国 FTC；环境营销声明",
        "source_url": "https://www.ftc.gov/legal-library/browse/rules/green-guides",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "仅保存摘要与链接；使用前复核现行规则和执法材料",
        "source_kind": "official_regulator",
        "topic_tags": "united-states,environmental-claims,evidence,advertising",
    },
    {
        "id": "ftc-endorsements-reviews",
        "entry_type": "external_guidance",
        "claim_type": "endorsement_disclosure",
        "title": "FTC 评价与背书披露",
        "summary": "评价、背书和影响者内容的商业关系及重要连接需要按美国 FTC 语境审查披露和真实性。",
        "scope": "美国消费者广告语境；不证明企业评价、认证或产品性能，也不外推全球广告法。",
        "market_scope": "美国 FTC；评价、背书和影响者内容",
        "source_url": "https://www.ftc.gov/business-guidance/advertising-marketing/endorsements-influencers-reviews",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "仅保存摘要与链接；使用前复核现行规则和执法材料",
        "source_kind": "official_regulator",
        "topic_tags": "united-states,reviews,endorsements,disclosure",
    },
    {
        "id": "eu-access2markets",
        "entry_type": "external_guidance",
        "claim_type": "market_boundary_lookup",
        "title": "EU Access2Markets 查询入口",
        "summary": "按出口国、进口国、具体商品编码和日期查询关税、原产地规则、产品要求与程序。",
        "scope": "动态查询结果依赖 HS/CN 分类、原产地和日期；不能把通用产品描述写成分类、合规或市场准入结论。",
        "market_scope": "欧盟目标市场；关税、原产地与产品要求查询",
        "source_url": "https://trade.ec.europa.eu/access-to-markets/en/home",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "官方查询入口；结果和规则使用前按具体交易复核",
        "source_kind": "official_trade_portal",
        "topic_tags": "european-union,tariff,origin,product-requirements,hs-code",
    },
    {
        "id": "usitc-hts",
        "entry_type": "external_guidance",
        "claim_type": "tariff_classification_boundary",
        "title": "USITC Harmonized Tariff Schedule",
        "summary": "美国进口税率和附加措施应以当前 HTS 版本及具体税号为准，不能由模型凭产品名称猜测税号。",
        "scope": "美国进口关税税则查询；分类需要产品事实和适用规则，不等于产品许可、安全或合规结论。",
        "market_scope": "美国进口；HTS 分类与税率",
        "source_url": "https://hts.usitc.gov/",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "官方查询入口；使用前核对当前版本和公告",
        "source_kind": "official_trade_portal",
        "topic_tags": "united-states,tariff,hts-code,classification",
    },
    {
        "id": "uk-trade-tariff",
        "entry_type": "external_guidance",
        "claim_type": "tariff_vat_lookup",
        "title": "UK Trade Tariff",
        "summary": "英国商品编码、关税和 VAT 查询需要目的地、商品编码和交易日期；结果应按具体货品复核。",
        "scope": "英国 tariff service；税率随日期、原产地和贸易安排变化，不外推到欧盟或其他市场。",
        "market_scope": "英国进口；商品编码、关税与 VAT",
        "source_url": "https://www.gov.uk/trade-tariff",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "GOV.UK 官方查询入口；使用前核对当前措施",
        "source_kind": "official_trade_portal",
        "topic_tags": "united-kingdom,tariff,vat,commodity-code",
    },
    {
        "id": "wto-tariff-data",
        "entry_type": "external_guidance",
        "claim_type": "trade_data_boundary",
        "title": "WTO Tariff and Trade Data",
        "summary": "用于跨市场比较关税数据和成员承诺范围；统计或绑定税率不是单票交易的分类、原产地或最终税额。",
        "scope": "多边贸易数据背景；不能替代目的地海关的实时分类、许可或产品标准核验。",
        "market_scope": "WTO 多边贸易数据；跨市场比较",
        "source_url": "https://www.wto.org/english/tratop_e/tariffs_e/tariff_data_e.htm",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "WTO 官方数据入口；使用前核对数据年份和口径",
        "source_kind": "intergovernmental_data",
        "topic_tags": "trade-data,tariff,comparison,statistics",
    },
    {
        "id": "icc-incoterms-2020",
        "entry_type": "external_guidance",
        "claim_type": "incoterms_contract_boundary",
        "title": "ICC Incoterms 2020",
        "summary": "报价中使用 Incoterms 时记录正式规则名称、版本和指定地点，并核对费用、风险、运输与保险责任。",
        "scope": "ICC 合同解释框架；不决定关税、产品合规、所有权、价格或适用法律，具体规则文本受版权约束。",
        "market_scope": "B2B 国际贸易报价；Incoterms 2020",
        "source_url": "https://iccwbo.org/business-solutions/incoterms-rules/incoterms-2020/",
        "source_date": "2026-07-03",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "ICC 官方页面；知识库只保存摘要和链接，不复制规则正文",
        "source_kind": "standards_body",
        "topic_tags": "incoterms,quotation,shipping,risk,cost",
    },
    {
        "id": "google-title-links",
        "entry_type": "external_guidance",
        "claim_type": "search_result_title_boundary",
        "title": "Google 搜索结果标题链接",
        "summary": "标题应简洁、明确并描述页面；Google 生成标题链接时还可能参考页面主标题、显著文字及链接文本。",
        "scope": "仅适用于 Google Search 标题链接；搜索引擎可能改写标题，不保证展示、排名或点击。",
        "market_scope": "Google Search；搜索结果标题链接",
        "source_url": "https://developers.google.com/search/docs/appearance/title-link",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；使用前复核页面状态",
        "source_kind": "official_guidance",
        "topic_tags": "seo,title,title-tag,title-link,search-results,页面标题,标题标签,标题链接",
    },
    {
        "id": "google-meta-descriptions",
        "entry_type": "external_guidance",
        "claim_type": "search_result_snippet_boundary",
        "title": "Google 搜索摘要与 Meta Description",
        "summary": "Meta description 可提供简洁、贴合页面的摘要；Google 也可能根据页面内容生成摘要，摘要展示长度没有固定字符承诺。",
        "scope": "仅适用于 Google 搜索摘要；编写描述不保证其被采用，也不保证排名或点击提升。",
        "market_scope": "Google Search；搜索摘要",
        "source_url": "https://developers.google.com/search/docs/appearance/snippet",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；使用前复核页面状态",
        "source_kind": "official_guidance",
        "topic_tags": "seo,meta-description,snippet,search-results,摘要,元描述,页面描述",
    },
    {
        "id": "valve-selection-input-checklist",
        "entry_type": "external_guidance",
        "claim_type": "valve_selection_information_heuristic",
        "title": "阀门选型资料收集清单",
        "summary": "先收集介质及组成、压力与温度范围、流量/工况、阀门功能、材料、端部连接与尺寸、驱动/控制方式及需核对的标准版本；资料不足时创建补充问题，不据此推荐具体型号或等级。",
        "scope": "内部采购信息完整性启发式；不构成工程选型、材料兼容性、压力等级或标准适用性结论，须由工程人员结合产品证据核验。",
        "market_scope": "B2B 工业阀门询价；不限定市场",
        "source_url": "https://www.asme.org/codes-standards/find-codes-standards/b16-34-valves-flanged-threaded-welding-end",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "内部字段清单；ASME 标准正文受其许可约束，仅保存摘要和官方目录链接",
        "source_kind": "internal_heuristic",
        "topic_tags": "valve,selection,pressure,temperature,flow-rate,fluid-media,material,compatibility,connection,size,actuator,control,configuration,operating-duty,service-condition,duty,compare,comparison,alternatives,valve-type,water-treatment,阀门,选型,压力,温度,流量,介质,材质,材料,兼容性,连接,尺寸,执行器,控制,工况,比较,替代方案,水处理",
    },
    {
        "id": "product-document-applicability-checklist",
        "entry_type": "external_guidance",
        "claim_type": "product_document_evidence_heuristic",
        "title": "产品证书与测试报告适用性核对",
        "summary": "核对文件类型、签发方、文件编号和版本、有效期、所依据的法规/标准及版本、适用型号/配置/批次、测试范围与目的地；缺一项时要求补资料，不凭证书名称推断真伪、有效性或产品合规。",
        "scope": "内部证据核对清单；各市场法规和认可路径不同，不代替主管机构、认证机构或工程人员判断。",
        "market_scope": "B2B 产品资料与目标市场核验；不限定市场",
        "source_url": "",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "内部证据核对启发式；无外部来源，不替代主管机构或认证机构判断",
        "source_kind": "internal_heuristic",
        "topic_tags": "certificate,test-report,standard,regulation,issuer,model,batch,scope,validity,document-evidence,证书,测试报告,标准,法规,签发方,型号,批次,范围,有效期,文件核验",
    },
    {
        "id": "valve-actuator-input-checklist",
        "entry_type": "external_guidance",
        "claim_type": "valve_actuator_information_heuristic",
        "title": "阀门执行器与自动化资料清单",
        "summary": "自动化阀门询价时收集确切型号和配置、开关或调节功能、要求的失效位置、工艺压差与动作频次、可用气源/电源、控制信号、安装接口及环境条件；请供应商工程人员用已确认的阀门负载和工况校核执行器输出。",
        "scope": "内部资料完整性清单；不计算扭矩/推力，不推荐执行器型号，也不判定控制、安全或危险区域适用性。缺失工况或产品数据时应暂停结论并补资料。",
        "market_scope": "B2B 工业阀门自动化采购；不限定市场",
        "source_url": "",
        "source_date": "2026-09-30",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "内部整理的采购资料启发式；无外部来源，不构成工程选型",
        "source_kind": "internal_heuristic",
        "topic_tags": "actuator,actuation,automation,automated-valve,control-signal,open-close,modulating,fail-position,fail-safe,operating-cycle,cycle-frequency,air-supply,electrical-supply,mounting-interface,torque,thrust,environmental-conditions,执行器,驱动,自动化,自动阀,控制信号,开关,调节,失效位置,故障位置,动作频次,气源,电源,安装接口,扭矩,推力,环境条件",
    },
    {
        "id": "valve-test-record-applicability-checklist",
        "entry_type": "external_guidance",
        "claim_type": "valve_test_record_evidence_heuristic",
        "title": "阀门测试记录与批次适用性核对",
        "summary": "收到测试或检验记录后，对照采购要求核对文件类型、适用标准及版本、试件/样品/生产测试范围、具体型号和配置、序列号或批次、测试日期、结果与验收依据、签发或见证方及偏差记录；证据未覆盖实际产品或合同范围时提出补充核对。",
        "scope": "内部证据核对清单；不同测试的要求由合同、适用标准和工程审核确定。不能凭文件标题、样本报告或单一批次记录推断其他配置、整批产品、证书有效性或市场合规。",
        "market_scope": "B2B 工业阀门质量与供应商资料核验；不限定市场",
        "source_url": "",
        "source_date": "2026-09-30",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "内部证据核对启发式；无外部来源，不复用标准正文",
        "source_kind": "internal_heuristic",
        "topic_tags": "test-report,test-record,test-results,inspection-record,production-test,sample-test,acceptance-criteria,serial-number,production-lot,batch,traceability,witness,issuer,deviation,测试报告,测试记录,检验记录,生产测试,样品测试,验收依据,序列号,生产批次,可追溯性,见证方,签发方,偏差",
    },
    {
        "id": "valve-drawing-installation-maintenance-checklist",
        "entry_type": "external_guidance",
        "claim_type": "valve_document_package_information_heuristic",
        "title": "阀门图纸、安装与维护资料清单",
        "summary": "核对资料是否对应准确的型号、口径、连接、配置和版本；按项目需要索取数据表、总装/尺寸图、端部连接信息、安装说明、检修手册及备件清单。替换现有阀门时，将图纸尺寸和现场接口交由工程人员核对，不凭名称或系列相近推断可互换。",
        "scope": "内部采购资料完整性清单；不提供具体尺寸、安装间隙、维护周期或备件型号，也不证明制造商拥有或已提供这些文件。",
        "market_scope": "B2B 工业阀门技术文件与售后资料；不限定市场",
        "source_url": "",
        "source_date": "2026-09-30",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "内部整理的采购资料启发式；无外部来源，具体尺寸和维护要求须按产品文件核验",
        "source_kind": "internal_heuristic",
        "topic_tags": "datasheet,data-sheet,technical-documentation,documents,documentation,technical-drawing,dimensional-drawing,general-arrangement,dimensions,connection,interface,face-to-face,end-to-end,replacement,interchangeability,installation,clearance,maintenance,operation-manual,maintenance-manual,spare-parts,parts-list,revision,数据表,技术文件,文件,文档,技术图纸,尺寸图,总装图,尺寸,连接,接口,端到端,替换,互换,安装,空间,检修,操作手册,维护手册,备件,零件清单,版本",
    },
    {
        "id": "eu-pressure-equipment-directive",
        "entry_type": "external_guidance",
        "claim_type": "eu_pressure_equipment_boundary",
        "title": "欧盟压力设备指令（PED）",
        "summary": "欧盟委员会的 PED 2014/68/EU 介绍用于查找压力设备的法规与合格评定框架；是否适用取决于设备定义、参数、类别和排除项。",
        "scope": "欧盟压力设备法规入口；不能仅凭“阀门”类别推断 PED 适用、分类、CE 标志或合规状态，需根据产品配置和法规原文复核。",
        "market_scope": "European Union 欧盟；压力设备 PED",
        "source_url": "https://single-market-economy.ec.europa.eu/sectors/pressure-equipment-and-gas-appliances/pressure-equipment-sector/pressure-equipment-directive_en",
        "source_date": "2014-05-15",
        "accessed_at": "2026-09-30",
        "valid_until": "revalidate-before-use",
        "license": "欧盟委员会法规说明页；使用前核对现行法规、协调标准和适用范围",
        "source_kind": "official_regulator",
        "topic_tags": "eu,european-union,pressure-equipment,ped,conformity-assessment,ce,欧盟,压力设备,合格评定,指令",
    },
)


def _normalise_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(entry)
    url = str(result.get("source_url", ""))
    internal_heuristics = {"rfq-page-content-checklist", "source-freshness-fact-separation"}
    standards = {"w3c-prov-o", "accessibility-content-usability"}
    regulators = {"ftc-ad-substantiation"}
    if "source_kind" not in result:
        entry_id = str(result.get("id", ""))
        result["source_kind"] = (
            "internal_heuristic" if entry_id in internal_heuristics
            else "standards_body" if entry_id in standards
            else "official_regulator" if entry_id in regulators
            else "official_guidance" if url.startswith("https://")
            else "unknown"
        )
    result.setdefault("topic_tags", str(result.get("claim_type", "")).replace("_", ","))
    result.setdefault("freshness_policy", "revalidate_before_use")
    result.setdefault("last_verified_at", result.get("accessed_at", ""))
    return result


def curated_entries(base_entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return stable, JSON-ready guidance records with source metadata."""

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in (*base_entries, *SUPPLEMENTAL_KNOWLEDGE_ENTRIES):
        entry = _normalise_entry(raw)
        entry_id = str(entry.get("id", "")).strip()
        if not entry_id or entry_id in seen or entry.get("entry_type") != "external_guidance":
            continue
        seen.add(entry_id)
        result.append(entry)
    return result


_MARKET_ALIASES = {
    "united states": ("united states", "u.s.", "usa", "america", "美国"),
    "us": ("united states", "u.s.", "usa", "america", "美国"),
    "usa": ("united states", "u.s.", "usa", "america", "美国"),
    "united kingdom": ("united kingdom", "britain", "uk", "英国"),
    "uk": ("united kingdom", "britain", "uk", "英国"),
    "european union": ("european union", "欧盟"),
    "eu": ("european union", "欧盟"),
    "germany": ("germany", "deutschland", "德国", "european union", "欧盟"),
    "france": ("france", "法国", "european union", "欧盟"),
    "netherlands": ("netherlands", "holland", "荷兰", "european union", "欧盟"),
    "italy": ("italy", "意大利", "european union", "欧盟"),
    "spain": ("spain", "西班牙", "european union", "欧盟"),
    "poland": ("poland", "波兰", "european union", "欧盟"),
    "sweden": ("sweden", "瑞典", "european union", "欧盟"),
    "denmark": ("denmark", "丹麦", "european union", "欧盟"),
    "belgium": ("belgium", "比利时", "european union", "欧盟"),
    "ireland": ("ireland", "爱尔兰", "european union", "欧盟"),
    "austria": ("austria", "奥地利", "european union", "欧盟"),
    "portugal": ("portugal", "葡萄牙", "european union", "欧盟"),
    "finland": ("finland", "芬兰", "european union", "欧盟"),
    "czechia": ("czechia", "czech republic", "捷克", "european union", "欧盟"),
}
_MARKET_FAMILIES = {
    "united-states": ("united states", "u.s.", "usa", "america", "美国"),
    "united-kingdom": ("united kingdom", "britain", "uk", "英国"),
    "european-union": ("european union", "欧盟"),
}

_SEARCH_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
_SEARCH_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "can", "do", "for", "from", "how",
    "i", "in", "is", "it", "of", "on", "or", "review", "should", "that", "the", "this", "to", "we", "what",
    "when", "which", "with", "you", "your", "b2b", "content", "page", "product",
}
_STRICT_TOPIC_TRIGGERS = {
    "eu_pressure_equipment_boundary": {
        "ce", "class", "classification", "compliance", "conformity", "directive", "law", "ped",
        "regulation", "regulatory",
    },
}


def _search_terms(value: str, *, include_cjk: bool = False) -> set[str]:
    normalized = value.casefold().replace("_", " ").replace("-", " ")
    terms: set[str] = set()
    for term in _SEARCH_TOKEN_RE.findall(normalized):
        if term in _SEARCH_STOP_WORDS:
            continue
        terms.add(term)
        if term.endswith("ies") and len(term) > 4:
            terms.add(term[:-3] + "y")
        elif term.endswith("es") and len(term) > 4 and term[:-2].endswith(("s", "x", "z", "ch", "sh")):
            terms.add(term[:-2])
        elif term.endswith("s") and len(term) > 4 and not term.endswith(("ss", "us", "is")):
            terms.add(term[:-1])
    if include_cjk:
        for phrase in _CJK_RUN_RE.findall(normalized):
            for width in (2, 3, 4):
                terms.update(phrase[index:index + width] for index in range(len(phrase) - width + 1))
    return terms


def _market_terms(market: str) -> tuple[str, ...]:
    normalized = " ".join(market.casefold().split())
    return next(
        (
            terms
            for key, terms in _MARKET_ALIASES.items()
            if normalized == key or (key in normalized if len(key) > 2 else key in normalized.split())
        ),
        (normalized,),
    )


def _market_families(market: str) -> set[str]:
    terms = _market_terms(market)
    return {
        family
        for family, aliases in _MARKET_FAMILIES.items()
        if any(alias in terms for alias in aliases)
    }


def filter_guidance(
    entries: Iterable[Mapping[str, Any]],
    *,
    market: str | None = None,
    claim_type: str | None = None,
) -> list[dict[str, Any]]:
    result = [_normalise_entry(entry) for entry in entries]
    if market and market.strip():
        terms = _market_terms(market.strip())
        result = [
            entry
            for entry in result
            if any(term in str(entry.get("market_scope", "")).casefold() for term in terms)
        ]
    if claim_type and claim_type.strip():
        expected = claim_type.strip().casefold()
        result = [entry for entry in result if str(entry.get("claim_type", "")).casefold() == expected]
    return result


def select_guidance(
    entries: Iterable[Mapping[str, Any]],
    context: Mapping[str, str] | None = None,
    *,
    limit: int = 8,
) -> tuple[dict[str, Any], ...]:
    """Select relevant policy context without treating it as product evidence."""

    if limit < 1:
        return ()
    values = {key: str(value).strip().casefold() for key, value in (context or {}).items() if value}
    primary_query = " ".join(values.get(key, "") for key in ("request_summary", "question"))
    supporting_query = " ".join(values.get(key, "") for key in ("product", "use_case", "buyer_role", "purchase_stage"))
    primary_terms = _search_terms(primary_query, include_cjk=True)
    supporting_terms = _search_terms(supporting_query, include_cjk=True)
    market = values.get("target_market", "")
    requested_families = _market_families(market) if market else set()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    always_use = {"source-freshness-fact-separation"}
    for position, raw in enumerate(entries):
        entry = _normalise_entry(raw)
        if entry.get("entry_type") != "external_guidance":
            continue
        title_terms = _search_terms(str(entry.get("title", "")))
        body_terms = _search_terms(" ".join(
            str(entry.get(field, ""))
            for field in ("id", "summary", "scope", "market_scope", "claim_type")
        ))
        tag_terms = _search_terms(str(entry.get("topic_tags", "")), include_cjk=True)
        entry_market = str(entry.get("market_scope", "")).casefold()
        scoped_families = {
            family
            for family, terms in _MARKET_FAMILIES.items()
            if any(term in entry_market for term in terms)
        }
        if market and scoped_families and not scoped_families.intersection(requested_families):
            continue
        topic_score = (
            4 * len(primary_terms & title_terms)
            + 2 * len(primary_terms & body_terms)
            + 5 * len(primary_terms & tag_terms)
            + 2 * len(supporting_terms & title_terms)
            + len(supporting_terms & body_terms)
            + 3 * len(supporting_terms & tag_terms)
        )
        strict_triggers = _STRICT_TOPIC_TRIGGERS.get(str(entry.get("claim_type", "")), set())
        if strict_triggers and not strict_triggers.intersection(primary_terms | supporting_terms):
            continue
        if topic_score == 0 and entry.get("id") not in always_use:
            continue
        score = topic_score + (1000 if entry.get("id") in always_use else 0)
        if market and scoped_families.intersection(requested_families):
            score += 1
        scored.append((score, -position, entry))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return tuple(entry for _, _, entry in scored[:limit])


def runtime_guidance(context: Mapping[str, str] | None = None, *, limit: int = 8) -> tuple[dict[str, Any], ...]:
    """Load API-owned entries lazily so the worker does not import the app at startup."""

    from .app import KNOWLEDGE_ENTRIES

    return select_guidance(curated_entries(KNOWLEDGE_ENTRIES), context, limit=limit)
