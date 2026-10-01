import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  CircleHelp,
  Clock3,
  Eye,
  ExternalLink,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import {
  createVisibilityRun,
  executeVisibilityRun,
  getProcurementQuestionSet,
  getProcurementQuestionSetVersion,
  getVisibilityRun,
  listProcurementQuestionSets,
  listVisibilityRuns,
  ProcurementQuestionSet,
  ProcurementQuestionSetSummary,
  ProcurementQuestionSetVersion,
  Site,
  VisibilityCitation,
  VisibilityMetrics,
  VisibilityProvider,
  VisibilityRun,
  VisibilitySample,
} from "./api";

type Props = {
  workspaceKey: string;
  sites: Site[];
  sitesLoading?: boolean;
};

const PROVIDERS: Array<{ value: VisibilityProvider; title: string; detail: string }> = [
  { value: "fixture", title: "Fixture 演示", detail: "本地确定性样本，明确标记 synthetic" },
  { value: "structured_http", title: "结构化联网来源", detail: "需要有效凭据；不可用时会记录 unavailable" },
  { value: "manual_capture", title: "人工捕获", detail: "保存人工提供的回答与引用记录" },
];

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "发生未知错误";
}

function asDate(value?: string | null) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function statusText(status?: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "采样中",
    succeeded: "已完成",
    partial: "部分完成",
    failed: "失败",
    unavailable: "不可用",
    succeeded_sample: "成功",
  };
  return labels[(status || "").toLowerCase()] || status || "未知";
}

function statusTone(status?: string) {
  const value = (status || "").toLowerCase();
  if (["succeeded", "success", "complete", "completed", "pass"].includes(value)) return "good";
  if (["failed", "error"].includes(value)) return "bad";
  if (["running", "queued", "pending"].includes(value)) return "working";
  if (["unavailable", "partial"].includes(value)) return "review";
  return "muted";
}

function isRunning(status?: string) {
  return ["queued", "running", "pending"].includes((status || "").toLowerCase());
}

function rate(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  const percent = value <= 1 ? value * 100 : value;
  return `${Math.round(percent)}%`;
}

function money(value?: string | number | null) {
  if (value === null || value === undefined || value === "") return "$0.00";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `$${parsed.toFixed(4)}` : String(value);
}

function metricNumber(metrics: VisibilityMetrics | undefined, ...keys: string[]) {
  for (const key of keys) {
    const value = metrics?.[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
}

function nestedMetricNumber(metrics: VisibilityMetrics | undefined, group: string, ...keys: string[]) {
  const nested = metrics?.[group];
  if (!nested || typeof nested !== "object") return undefined;
  for (const key of keys) {
    const value = (nested as Record<string, unknown>)[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
}

function normalizeCitations(citations?: VisibilityCitation[] | string[]) {
  return (citations || []).map((citation) => {
    if (typeof citation === "string") return { url: citation };
    return citation;
  }).filter((citation) => citation.url);
}

function runMetrics(run: VisibilityRun) {
  const metrics = run.metrics || {};
  return {
    brand: metricNumber(metrics, "brand_mention_rate", "brandMentionRate") ?? nestedMetricNumber(metrics, "brand", "mention_rate", "mentionRate"),
    nonBrand: metricNumber(metrics, "non_brand_mention_rate", "nonBrandMentionRate") ?? nestedMetricNumber(metrics, "non_brand", "mention_rate", "mentionRate"),
    citation: metricNumber(metrics, "site_citation_rate", "siteCitationRate"),
    success: metricNumber(metrics, "sampling_success_rate", "success_rate", "samplingSuccessRate"),
    coverage: metricNumber(metrics, "question_coverage_rate", "questionCoverageRate"),
  };
}

function frozenVersions(set: ProcurementQuestionSet | undefined) {
  return (set?.versions || []).filter((version) => version.state === "frozen").sort((a, b) => b.version - a.version);
}

function versionLabel(version: ProcurementQuestionSetVersion) {
  return `v${version.version} · ${version.questions.length} 个问题 · 已冻结`;
}

function SampleCard({ sample }: { sample: VisibilitySample }) {
  const citations = normalizeCitations(sample.citations);
  return (
    <article className="visibility-sample">
      <div className="visibility-sample-heading">
        <div className="visibility-sample-title">
          <span className="visibility-sample-index">{String(sample.position).padStart(2, "0")}</span>
          <div><strong>{sample.question || `问题 #${sample.question_id}`}</strong><small>{sample.brand_query ? "品牌词" : "非品牌词"}</small></div>
        </div>
        <span className={`visibility-status ${statusTone(sample.status)}`}>{statusText(sample.status)}</span>
      </div>
      {sample.status === "unavailable" && <div className="visibility-inline-note warning"><CircleHelp size={14} /><span>该来源当前不可用，未计入未提及或引用失败指标。</span></div>}
      {sample.status === "failed" && <div className="visibility-inline-note error"><AlertCircle size={14} /><span>{sample.error_message || sample.error_code || "采样失败，未计入成功率分母。"}</span></div>}
      {sample.answer_text && <p className="visibility-answer">{sample.answer_text}</p>}
      {sample.status === "succeeded" && !sample.answer_text && <p className="visibility-answer muted">Provider 没有返回可解析回答。</p>}
      <div className="visibility-sample-meta">
        <span>{sample.model ? `模型 ${sample.model}` : "模型未记录"}</span>
        <span>{sample.input_tokens || sample.output_tokens ? `${sample.input_tokens || 0} 输入 · ${sample.output_tokens || 0} 输出 token` : "token 未记录"}</span>
        <span>{money(sample.cost_usd)}</span>
        {sample.provider_request_id && <code>{sample.provider_request_id}</code>}
      </div>
      {sample.mentioned_domains && sample.mentioned_domains.length > 0 && <div className="visibility-domains"><strong>回答中提及的域名</strong>{sample.mentioned_domains.map((domain) => <span key={domain}>{domain}</span>)}</div>}
      {citations.length > 0 && <div className="visibility-citations"><strong>可追溯引用</strong>{citations.map((citation, index) => <a key={`${citation.url}:${index}`} href={citation.url} target="_blank" rel="noreferrer"><span>{citation.title || citation.url}</span><ArrowUpRight size={13} /></a>)}</div>}
      {sample.raw_response && <details className="visibility-raw"><summary>查看原始回答</summary><pre>{sample.raw_response}</pre></details>}
    </article>
  );
}

export function VisibilityWorkspace({ workspaceKey, sites, sitesLoading = false }: Props) {
  const [siteId, setSiteId] = useState("");
  const [questionSets, setQuestionSets] = useState<ProcurementQuestionSetSummary[]>([]);
  const [questionSetDetails, setQuestionSetDetails] = useState<Record<number, ProcurementQuestionSet>>({});
  const [questionSetId, setQuestionSetId] = useState<number | "">("");
  const [versionId, setVersionId] = useState<number | "">("");
  const [provider, setProvider] = useState<VisibilityProvider>("fixture");
  const [market, setMarket] = useState("United States");
  const [language, setLanguage] = useState("en");
  const [brandTerms, setBrandTerms] = useState("");
  const [maxSamples, setMaxSamples] = useState("20");
  const [budget, setBudget] = useState("0.50");
  const [runs, setRuns] = useState<VisibilityRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<VisibilityRun | null>(null);
  const [setsLoading, setSetsLoading] = useState(false);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runLoading, setRunLoading] = useState(false);
  const [submitBusy, setSubmitBusy] = useState(false);
  const [setsError, setSetsError] = useState("");
  const [runsError, setRunsError] = useState("");
  const [runError, setRunError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (siteId && sites.some((site) => String(site.id) === siteId)) return;
    setSiteId(sites[0] ? String(sites[0].id) : "");
  }, [siteId, sites]);

  const selectedSite = useMemo(() => sites.find((site) => String(site.id) === siteId) || null, [siteId, sites]);
  const selectedSet = questionSetId === "" ? undefined : questionSetDetails[questionSetId];
  const frozen = useMemo(() => frozenVersions(selectedSet), [selectedSet]);
  const selectedVersion = frozen.find((version) => version.id === versionId) || frozen[0];

  const refreshRuns = useCallback(async (nextSiteId = siteId) => {
    if (!nextSiteId) {
      setRuns([]);
      setSelectedRun(null);
      return;
    }
    setRunsLoading(true);
    setRunsError("");
    try {
      const result = await listVisibilityRuns(workspaceKey, nextSiteId);
      setRuns(result);
      setSelectedRun((current) => current && result.some((run) => String(run.id) === String(current.id)) ? current : result[0] || null);
    } catch (error) {
      setRunsError(errorMessage(error));
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }, [siteId, workspaceKey]);

  const refreshQuestionSets = useCallback(async (nextSiteId = siteId) => {
    if (!nextSiteId) {
      setQuestionSets([]);
      setQuestionSetDetails({});
      setQuestionSetId("");
      setVersionId("");
      return;
    }
    setSetsLoading(true);
    setSetsError("");
    try {
      const summaries = await listProcurementQuestionSets(workspaceKey, nextSiteId);
      const details = await Promise.all(summaries.map(async (summary) => {
        try {
          return await getProcurementQuestionSet(workspaceKey, nextSiteId, summary.id);
        } catch {
          return null;
        }
      }));
      const detailMap: Record<number, ProcurementQuestionSet> = {};
      details.forEach((detail) => { if (detail) detailMap[detail.id] = detail; });
      setQuestionSets(summaries);
      setQuestionSetDetails(detailMap);
      const firstSet = summaries.find((summary) => detailMap[summary.id] && frozenVersions(detailMap[summary.id]).length > 0) || summaries[0];
      setQuestionSetId(firstSet?.id ?? "");
      const firstVersion = firstSet ? frozenVersions(detailMap[firstSet.id])[0] : undefined;
      setVersionId(firstVersion?.id ?? "");
    } catch (error) {
      setSetsError(errorMessage(error));
      setQuestionSets([]);
      setQuestionSetDetails({});
      setQuestionSetId("");
      setVersionId("");
    } finally {
      setSetsLoading(false);
    }
  }, [siteId, workspaceKey]);

  useEffect(() => {
    void refreshQuestionSets(siteId);
    void refreshRuns(siteId);
  }, [refreshQuestionSets, refreshRuns, siteId]);

  useEffect(() => {
    if (!questionSetId || !siteId || questionSetDetails[questionSetId]) return;
    void getProcurementQuestionSet(workspaceKey, siteId, questionSetId).then((detail) => {
      setQuestionSetDetails((current) => ({ ...current, [detail.id]: detail }));
    }).catch(() => undefined);
  }, [questionSetDetails, questionSetId, siteId, workspaceKey]);

  useEffect(() => {
    if (!questionSetId || !selectedSet || versionId !== "") return;
    const version = frozenVersions(selectedSet)[0];
    if (version) setVersionId(version.id);
  }, [questionSetId, selectedSet, versionId]);

  useEffect(() => {
    if (!selectedRun || !siteId || !isRunning(selectedRun.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const current = await getVisibilityRun(workspaceKey, siteId, selectedRun.id);
        setSelectedRun(current);
        setRuns((items) => items.map((item) => String(item.id) === String(current.id) ? current : item));
        if (!isRunning(current.status)) window.clearInterval(timer);
      } catch (error) {
        setRunError(errorMessage(error));
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [selectedRun, siteId, workspaceKey]);

  function changeSite(nextSiteId: string) {
    setSiteId(nextSiteId);
    setSelectedRun(null);
    setRunsError("");
    setSetsError("");
  }

  async function loadRun(run: VisibilityRun) {
    setSelectedRun(run);
    if (!siteId) return;
    setRunLoading(true);
    setRunError("");
    try {
      setSelectedRun(await getVisibilityRun(workspaceKey, siteId, run.id));
    } catch (error) {
      setRunError(errorMessage(error));
    } finally {
      setRunLoading(false);
    }
  }

  async function startRun() {
    if (!siteId || versionId === "") {
      setRunError("请先选择站点和已冻结的问题集版本。");
      return;
    }
    const version = selectedVersion || (await getProcurementQuestionSetVersion(workspaceKey, siteId, questionSetId as number, 1).catch(() => null));
    if (!version || version.state !== "frozen") {
      setRunError("只有已冻结的问题集版本可以用于采样。");
      return;
    }
    setSubmitBusy(true);
    setRunError("");
    setNotice("");
    try {
      const requestedSamples = Math.max(1, Math.min(50, Number.parseInt(maxSamples, 10) || version.questions.length || 1));
      const requestedBudget = Number.parseFloat(budget);
      const created = await createVisibilityRun(workspaceKey, siteId, {
        question_set_version_id: version.id,
        provider,
        provider_kind: provider === "manual_capture" ? "manual_capture" : "model_api",
        market: market.trim() || "未指定",
        language: language.trim() || "未指定",
        brand_terms: brandTerms.split(/[,\n]/).map((term) => term.trim()).filter(Boolean),
        max_samples: requestedSamples,
        budget_usd: Number.isFinite(requestedBudget) && requestedBudget >= 0 ? requestedBudget : 0,
        idempotency_key: `visibility-${siteId}-${version.id}-${Date.now()}`,
      });
      let result = created;
      if (created.status === "queued") {
        result = await executeVisibilityRun(workspaceKey, siteId, created.id);
      }
      setSelectedRun(result);
      setRuns((items) => [result, ...items.filter((item) => String(item.id) !== String(result.id))]);
      setNotice(result.is_synthetic || provider === "fixture" ? "Fixture 采样已启动，结果会明确标记为 synthetic。" : "采样任务已启动，正在等待 Provider 返回结果。");
      window.setTimeout(() => setNotice(""), 4200);
    } catch (error) {
      setRunError(errorMessage(error));
    } finally {
      setSubmitBusy(false);
    }
  }

  const metrics = selectedRun ? runMetrics(selectedRun) : { brand: undefined, nonBrand: undefined, citation: undefined, success: undefined, coverage: undefined };
  const samples = selectedRun?.samples || [];
  const selectedProvider = PROVIDERS.find((item) => item.value === provider) || PROVIDERS[0];
  const capability = selectedRun?.capability || (typeof selectedRun?.capability_json === "object" ? selectedRun.capability_json : null);

  return (
    <div className="page-wrap visibility-wrap" id="visibility">
      <div className="page-heading">
        <div>
          <div className="eyebrow">持续观测 <span>·</span> AI 可见性监测</div>
          <h1>可见性监测</h1>
          <p>使用冻结的问题集分别观察品牌提及、目标站点引用和采样成本。</p>
        </div>
        <div className="heading-actions">
          <button className="button button-secondary" type="button" onClick={() => { void refreshQuestionSets(); void refreshRuns(); }} disabled={setsLoading || runsLoading}><RefreshCw size={15} className={setsLoading || runsLoading ? "spin" : ""} />刷新监测</button>
        </div>
      </div>

      <section className="visibility-disclaimer" aria-label="监测口径说明"><ShieldCheck size={15} /><span><strong>口径分开保存。</strong>每次采样都保留 Provider、模型、原始回答与引用；fixture 仅用于演示，不能代表真实线上搜索可见性。</span></section>

      {notice && <div className="visibility-notice" role="status"><Check size={15} />{notice}</div>}
      {(setsError || runsError) && <div className="alert alert-error visibility-alert" role="alert"><AlertCircle size={16} /><div><strong>读取监测数据失败</strong><span>{setsError || runsError}</span></div><button className="icon-button subtle" type="button" onClick={() => { void refreshQuestionSets(); void refreshRuns(); }} aria-label="重试监测数据"><RefreshCw size={14} /></button></div>}

      <section className="visibility-config" aria-labelledby="visibility-config-title">
        <div className="visibility-section-heading"><div><span className="section-kicker">01</span><div><h2 id="visibility-config-title">创建一次采样</h2><p>只允许使用当前工作区内已冻结的问题集版本。</p></div></div><span className={`source-badge ${provider === "fixture" ? "source-fixture" : "source-live"}`}><span />{selectedProvider.title}</span></div>
        <div className="visibility-config-grid">
          <label className="field-label">站点<select value={siteId} onChange={(event) => changeSite(event.target.value)} disabled={sitesLoading || sites.length === 0}><option value="">{sitesLoading ? "正在读取站点" : sites.length ? "选择站点" : "暂无站点"}</option>{sites.map((site) => <option key={site.id} value={site.id}>{site.name} · {site.origin}</option>)}</select></label>
          <label className="field-label">问题集<select value={questionSetId} onChange={(event) => { const next = event.target.value ? Number(event.target.value) : ""; setQuestionSetId(next); const first = next === "" ? undefined : frozenVersions(questionSetDetails[next])[0]; setVersionId(first?.id ?? ""); }} disabled={setsLoading || questionSets.length === 0}><option value="">{setsLoading ? "正在读取问题集" : questionSets.length ? "选择问题集" : "暂无问题集"}</option>{questionSets.map((set) => <option key={set.id} value={set.id}>{set.name} · 当前 v{set.current_version}</option>)}</select></label>
          <label className="field-label">冻结版本<select value={versionId} onChange={(event) => setVersionId(event.target.value ? Number(event.target.value) : "")} disabled={setsLoading || frozen.length === 0}><option value="">{setsLoading ? "正在读取版本" : frozen.length ? "选择冻结版本" : "没有冻结版本"}</option>{frozen.map((version) => <option key={version.id} value={version.id}>{versionLabel(version)}</option>)}</select></label>
          <label className="field-label">Provider<select value={provider} onChange={(event) => setProvider(event.target.value as VisibilityProvider)}><option value="fixture">Fixture 演示</option><option value="structured_http">结构化联网来源</option><option value="manual_capture">人工捕获</option></select><span className="field-hint">{selectedProvider.detail}</span></label>
          <label className="field-label">目标市场<input value={market} onChange={(event) => setMarket(event.target.value)} placeholder="例如 United States" maxLength={120} /></label>
          <label className="field-label">语言<input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="例如 en" maxLength={35} /></label>
          <label className="field-label">品牌词（可选）<input value={brandTerms} onChange={(event) => setBrandTerms(event.target.value)} placeholder="多个词用逗号分隔" maxLength={600} /><span className="field-hint">品牌词与非品牌词单独统计。</span></label>
          <div className="visibility-number-fields"><label className="field-label">最多样本<input type="number" min="1" max="50" value={maxSamples} onChange={(event) => setMaxSamples(event.target.value)} /></label><label className="field-label">预算 USD<input type="number" min="0" max="100" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} /></label></div>
        </div>
        {selectedSite?.is_synthetic && <div className="visibility-inline-note warning"><TriangleAlert size={14} /><span>当前站点是合成演示站点；监测结果仍会保留来源和 synthetic 标记。</span></div>}
        {selectedSet && frozen.length === 0 && <div className="visibility-inline-note warning"><CircleHelp size={14} /><span>当前问题集没有已冻结版本。请先在“采购问题”中冻结一个版本后再采样。</span></div>}
        {!selectedSite && !sitesLoading && <div className="visibility-empty-inline"><Eye size={17} /><span>请先在“站点与审计”登记站点。</span></div>}
        {runError && <div className="visibility-inline-note error"><AlertCircle size={14} /><span>{runError}</span></div>}
        <div className="visibility-submit-row"><span>{selectedVersion ? `${selectedVersion.questions.length} 个问题 · ${versionLabel(selectedVersion)}` : "请选择已冻结的问题集版本"}</span><button className="button button-primary" type="button" onClick={() => void startRun()} disabled={submitBusy || setsLoading || !siteId || !selectedVersion}><Play size={14} />{submitBusy ? "启动中" : "开始采样"}</button></div>
      </section>

      <div className="visibility-layout">
        <section className="visibility-run-panel" aria-labelledby="visibility-runs-title">
          <div className="visibility-section-heading"><div><span className="section-kicker">02</span><div><h2 id="visibility-runs-title">采样记录</h2><p>{runsLoading ? "正在读取" : `${runs.length} 次采样`}</p></div></div><button className="icon-button" type="button" onClick={() => void refreshRuns()} disabled={runsLoading || !siteId} title="刷新采样记录" aria-label="刷新采样记录"><RefreshCw size={14} className={runsLoading ? "spin" : ""} /></button></div>
          {runsLoading ? <div className="visibility-loading"><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div> : runs.length === 0 ? <div className="visibility-empty"><Clock3 size={18} /><div><strong>还没有采样记录</strong><span>选择冻结的问题集并启动一次 fixture 采样，查看完整的原始回答与引用。</span></div></div> : <div className="visibility-run-list">{runs.map((run) => <button type="button" key={run.id} className={`visibility-run-row ${String(selectedRun?.id) === String(run.id) ? "selected" : ""}`} onClick={() => void loadRun(run)}><span className="visibility-run-main"><strong>{run.provider === "fixture" ? "Fixture 演示" : run.provider}</strong><small>{asDate(run.created_at)} · {run.market || "未指定市场"} · {run.language || "未指定语言"}</small></span><span className="visibility-run-count">{run.successful_samples ?? 0}/{run.planned_samples ?? 0}<small>成功样本</small></span><span className={`visibility-status ${statusTone(run.status)}`}>{statusText(run.status)}</span></button>)}</div>}
        </section>

        <section className="visibility-detail-panel" aria-labelledby="visibility-detail-title">
          <div className="visibility-section-heading"><div><span className="section-kicker">03</span><div><h2 id="visibility-detail-title">结果与证据</h2><p>{selectedRun ? `采样 #${selectedRun.id}` : "选择左侧记录查看原始回答"}</p></div></div>{selectedRun && <button className="icon-button" type="button" onClick={() => void loadRun(selectedRun)} disabled={runLoading} title="刷新采样详情" aria-label="刷新采样详情"><RefreshCw size={14} className={runLoading ? "spin" : ""} /></button>}</div>
          {!selectedRun ? <div className="visibility-empty detail-empty"><Eye size={19} /><strong>尚未选择采样</strong><span>这里会展示成功、失败和 unavailable 样本；失败样本不会被计算为“未提及”。</span></div> : <>
            <div className="visibility-detail-meta"><span className={`visibility-status ${statusTone(selectedRun.status)}`}>{statusText(selectedRun.status)}</span><span className={`source-badge ${selectedRun.is_synthetic || selectedRun.provider === "fixture" ? "source-fixture" : "source-live"}`}><span />{selectedRun.is_synthetic || selectedRun.provider === "fixture" ? "synthetic fixture" : selectedRun.provider_kind || selectedRun.provider}</span><span>创建于 {asDate(selectedRun.created_at)}</span><span>总成本 {money(selectedRun.total_cost_usd)}</span>{selectedRun.budget_usd !== undefined && selectedRun.budget_usd !== null && <span>预算 {money(selectedRun.budget_usd)}</span>}</div>
            <div className="visibility-metrics" aria-label="可见性指标"><article><span>品牌词提及率</span><strong>{rate(metrics.brand)}</strong><small>非品牌词 {rate(metrics.nonBrand)} · 仅统计成功样本</small></article><article><span>站点引用率</span><strong>{rate(metrics.citation)}</strong><small>包含目标域名可追溯引用</small></article><article><span>采样成功率</span><strong>{rate(metrics.success)}</strong><small>{selectedRun.successful_samples ?? 0} / {selectedRun.planned_samples ?? 0} 计划样本</small></article><article><span>问题覆盖率</span><strong>{rate(metrics.coverage)}</strong><small>至少一次引用目标站点的问题</small></article></div>
            {selectedRun.error && <div className="visibility-inline-note error"><AlertCircle size={14} /><span>{selectedRun.error}</span></div>}
            {capability && <details className="visibility-capability"><summary>能力与变量记录</summary><pre>{typeof capability === "string" ? capability : JSON.stringify(capability, null, 2)}</pre></details>}
            {samples.length === 0 ? <div className="visibility-empty detail-empty"><CircleHelp size={18} /><strong>{isRunning(selectedRun.status) ? "采样正在进行" : "没有样本详情"}</strong><span>{isRunning(selectedRun.status) ? "页面会自动刷新；预算和失败状态会记录在本次任务中。" : "该任务没有返回可展示的样本。"}</span></div> : <div className="visibility-samples">{samples.map((sample) => <SampleCard key={sample.id} sample={sample} />)}</div>}
          </>}
        </section>
      </div>

      <footer className="page-footer"><span>监测结果只描述当前 Provider、问题集版本和采样窗口，不代表全网排名或业务增长。</span><a href="https://developers.google.com/search/docs/appearance/ai-features" target="_blank" rel="noreferrer">查看口径边界 <ExternalLink size={13} /></a></footer>
    </div>
  );
}
