import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Check, ChevronDown, CircleHelp, FileText, LoaderCircle, Plus, RefreshCw, Save, ShieldCheck, Snowflake, Trash2 } from "lucide-react";
import {
  createProcurementQuestionSet,
  createProcurementQuestionSetVersion,
  freezeProcurementQuestionSetVersion,
  getProcurementQuestionSetVersion,
  listPages,
  listProcurementQuestionSets,
  listSites,
  PageSnapshot,
  ProcurementQuestion,
  ProcurementQuestionInput,
  ProcurementQuestionSetSummary,
  ProcurementQuestionSetVersion,
  Site,
  updateProcurementQuestionSetVersion,
} from "./api";

type DraftQuestion = ProcurementQuestionInput & { rowKey: string };

const VALVE_QUESTION_TEMPLATE: Omit<ProcurementQuestionInput, "page_ids">[] = [
  ["What information should I provide to request a quote for an industrial valve?", "Procurement buyer", "Supplier selection"],
  ["How do I select a valve for the fluid and operating conditions in my application?", "Application engineer", "Product specification"],
  ["Which details help confirm a valve is suitable for a specified pressure range?", "Project engineer", "Product specification"],
  ["What should I compare when choosing between different industrial valve types?", "Design engineer", "Product comparison"],
  ["How can I verify the materials used in a specific valve model?", "Quality engineer", "Supplier verification"],
  ["Where can I find the dimensions and connection details for a valve model?", "Design engineer", "Product specification"],
  ["What installation information is available for this valve series?", "Maintenance engineer", "Technical review"],
  ["What maintenance information should I review before selecting this valve?", "Maintenance engineer", "Technical review"],
  ["How can I request a datasheet for a specific valve model?", "Procurement buyer", "Supplier selection"],
  ["Which documents should I review before specifying this valve for a project?", "Project engineer", "Technical review"],
  ["How can I check whether a test report applies to the selected valve model?", "Quality engineer", "Supplier verification"],
  ["What customization details should I include when requesting a valve?", "Procurement buyer", "Request for quotation"],
  ["What is the minimum order quantity for this valve model?", "Procurement buyer", "Request for quotation"],
  ["How can I ask about production lead time for a custom valve order?", "Project manager", "Request for quotation"],
  ["Which packaging details should I confirm for an international valve shipment?", "Logistics coordinator", "Order planning"],
  ["What information is needed to estimate shipping for an export order?", "Logistics coordinator", "Order planning"],
  ["How are spare parts identified for this valve model?", "Maintenance engineer", "After-sales planning"],
  ["What after-sales support should I ask about for this valve series?", "Procurement buyer", "Supplier evaluation"],
  ["Can I request a sample or evaluation unit for this valve model?", "Product manager", "Supplier evaluation"],
  ["What information should I include when asking about compliance for a target market?", "Compliance specialist", "Supplier verification"],
].map(([question, buyer_role, purchase_stage]) => ({
  question,
  product: "Industrial valves",
  use_case: "Industrial flow control",
  buyer_role,
  purchase_stage,
  target_market: "To be confirmed",
  language: "English",
}));

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "发生未知错误";
}

function blankQuestion(): DraftQuestion {
  return {
    rowKey: `new-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    question: "",
    product: "",
    use_case: "",
    buyer_role: "",
    purchase_stage: "",
    target_market: "",
    language: "",
    page_ids: [],
  };
}

function fromSavedQuestion(question: ProcurementQuestion): DraftQuestion {
  return {
    rowKey: `saved-${question.id}`,
    question: question.question,
    product: question.product,
    use_case: question.use_case,
    buyer_role: question.buyer_role,
    purchase_stage: question.purchase_stage,
    target_market: question.target_market,
    language: question.language,
    page_ids: question.page_mappings.map((mapping) => mapping.page_id),
  };
}

function toInput(question: DraftQuestion): ProcurementQuestionInput {
  const { rowKey: _rowKey, ...input } = question;
  return { ...input, page_ids: [...input.page_ids] };
}

function latestPages(pages: PageSnapshot[]) {
  const seen = new Set<number>();
  return pages.filter((page) => {
    const pageId = page.page_id;
    if (!Number.isInteger(pageId) || !pageId || seen.has(pageId)) return false;
    seen.add(pageId);
    return true;
  });
}

function pagePath(page: PageSnapshot) {
  try {
    const url = new URL(page.url);
    return `${url.pathname}${url.search}` || "/";
  } catch {
    return page.url;
  }
}

function completeQuestions(questions: DraftQuestion[]) {
  for (const [index, question] of questions.entries()) {
    const missing = [
      ["采购问题", question.question],
      ["产品", question.product],
      ["用途", question.use_case],
      ["采购角色", question.buyer_role],
      ["采购阶段", question.purchase_stage],
      ["目标市场", question.target_market],
      ["语言", question.language],
    ].find(([, value]) => !String(value).trim());
    if (missing) return `第 ${index + 1} 条请填写${missing[0]}。`;
  }
  return "";
}

function validateFreeze(questions: DraftQuestion[]) {
  if (questions.length !== 20) return `冻结需要正好 20 条问题，目前有 ${questions.length} 条。`;
  const incomplete = completeQuestions(questions);
  if (incomplete) return incomplete;
  const unmappedIndex = questions.findIndex((question) => question.page_ids.length === 0);
  if (unmappedIndex >= 0) return `请先为第 ${unmappedIndex + 1} 条问题关联至少一个页面。`;
  return "";
}

export function ProcurementWorkspace({
  workspaceKey,
  onDirtyChange,
}: {
  workspaceKey: string;
  onDirtyChange?: (hasUnsavedDraft: boolean) => void;
}) {
  const [sites, setSites] = useState<Site[]>([]);
  const [sitesLoading, setSitesLoading] = useState(true);
  const [sitesError, setSitesError] = useState("");
  const [siteId, setSiteId] = useState("");
  const [pages, setPages] = useState<PageSnapshot[]>([]);
  const [pagesLoading, setPagesLoading] = useState(false);
  const [pagesError, setPagesError] = useState("");
  const [sets, setSets] = useState<ProcurementQuestionSetSummary[]>([]);
  const [setsLoading, setSetsLoading] = useState(false);
  const [setsError, setSetsError] = useState("");
  const [selectedSetId, setSelectedSetId] = useState<number | null>(null);
  const [versionNumber, setVersionNumber] = useState(0);
  const [version, setVersion] = useState<ProcurementQuestionSetVersion | null>(null);
  const [questions, setQuestions] = useState<DraftQuestion[]>([]);
  const [dirty, setDirty] = useState(false);
  const [versionLoading, setVersionLoading] = useState(false);
  const [versionError, setVersionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [newSetName, setNewSetName] = useState("");
  const [pageSearch, setPageSearch] = useState("");

  const hasUnsavedDraft = dirty || (createOpen && Boolean(newSetName.trim()));
  const onDirtyChangeRef = useRef(onDirtyChange);
  onDirtyChangeRef.current = onDirtyChange;
  const siteIdRef = useRef(siteId);
  siteIdRef.current = siteId;
  const hasUnsavedDraftRef = useRef(hasUnsavedDraft);
  hasUnsavedDraftRef.current = hasUnsavedDraft;

  const selectedSite = sites.find((site) => site.id === siteId) || null;
  const selectedSet = sets.find((item) => item.id === selectedSetId) || null;
  const pagesWithId = useMemo(() => latestPages(pages), [pages]);
  const matchingPages = useMemo(() => {
    const needle = pageSearch.trim().toLowerCase();
    if (!needle) return pagesWithId;
    return pagesWithId.filter((page) => `${page.url} ${page.title || ""}`.toLowerCase().includes(needle));
  }, [pageSearch, pagesWithId]);
  const mappedCount = questions.filter((question) => question.page_ids.length > 0).length;
  const freezeIssue = validateFreeze(questions);
  const isCurrentVersion = selectedSet?.current_version === versionNumber;
  const isEditable = version?.state === "draft" && isCurrentVersion;

  useEffect(() => {
    onDirtyChangeRef.current?.(hasUnsavedDraft);
  }, [hasUnsavedDraft]);

  useEffect(() => () => onDirtyChangeRef.current?.(false), []);

  useEffect(() => {
    if (!hasUnsavedDraft) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [hasUnsavedDraft]);

  function confirmDiscardDraft(action: string, includeNewSetName = true) {
    if (!dirty && !(includeNewSetName && createOpen && newSetName.trim())) return true;
    const draftParts = [
      dirty ? "当前问题集有未保存的修改" : "",
      includeNewSetName && createOpen && newSetName.trim() ? `新问题集名称“${newSetName.trim()}”尚未创建` : "",
    ].filter(Boolean);
    return window.confirm(`${draftParts.join("；")}。${action}会丢弃这些内容，确定继续吗？`);
  }

  const refreshSites = useCallback(async () => {
    setSitesLoading(true);
    setSitesError("");
    try {
      const result = await listSites();
      const activeSiteId = siteIdRef.current;
      const activeSiteRemoved = Boolean(activeSiteId && !result.some((site) => site.id === activeSiteId));
      if (activeSiteRemoved && hasUnsavedDraftRef.current) {
        const discard = window.confirm("刷新后当前站点已不在列表中。切换到其他站点会丢弃未保存内容，确定继续吗？");
        if (!discard) {
          setSites(result);
          setNotice("当前站点不在最新列表中；未保存内容已保留。");
          return;
        }
        setDirty(false);
        setCreateOpen(false);
        setNewSetName("");
      }
      setSites(result);
      setSiteId((current) => result.some((site) => site.id === current) ? current : result[0]?.id || "");
    } catch (error) {
      setSitesError(errorMessage(error));
    } finally {
      setSitesLoading(false);
    }
  }, []);

  useEffect(() => { void refreshSites(); }, [refreshSites]);

  const loadVersion = useCallback(async (set: ProcurementQuestionSetSummary, requestedVersion: number, activeSiteId = siteId) => {
    setVersionLoading(true);
    setVersionError("");
    setNotice("");
    setSelectedSetId(set.id);
    setVersionNumber(requestedVersion);
    setVersion(null);
    setQuestions([]);
    setDirty(false);
    try {
      const loaded = await getProcurementQuestionSetVersion(workspaceKey, activeSiteId, set.id, requestedVersion);
      setVersion(loaded);
      setQuestions(loaded.questions.map(fromSavedQuestion));
    } catch (error) {
      setVersionError(errorMessage(error));
    } finally {
      setVersionLoading(false);
    }
  }, [siteId, workspaceKey]);

  useEffect(() => {
    let cancelled = false;
    setSelectedSetId(null);
    setVersionNumber(0);
    setVersion(null);
    setQuestions([]);
    setDirty(false);
    setSets([]);
    setPages([]);
    setSetsError("");
    setPagesError("");
    setNotice("");
    if (!siteId) return () => { cancelled = true; };
    setSetsLoading(true);
    setPagesLoading(true);
    void Promise.allSettled([listPages(siteId), listProcurementQuestionSets(workspaceKey, siteId)]).then(([pageResult, setResult]) => {
      if (cancelled) return;
      if (pageResult.status === "fulfilled") {
        setPages(pageResult.value);
        setPagesError("");
      } else {
        setPagesError(errorMessage(pageResult.reason));
      }
      if (setResult.status === "fulfilled") {
        setSets(setResult.value);
        setSetsError("");
      } else {
        setSetsError(errorMessage(setResult.reason));
      }
      setPagesLoading(false);
      setSetsLoading(false);
    });
    return () => { cancelled = true; };
  }, [siteId, workspaceKey]);

  const refreshSets = useCallback(async (activeSiteId = siteId) => {
    if (!activeSiteId) return [];
    setSetsLoading(true);
    setSetsError("");
    try {
      const result = await listProcurementQuestionSets(workspaceKey, activeSiteId);
      setSets(result);
      return result;
    } catch (error) {
      setSetsError(errorMessage(error));
      return [];
    } finally {
      setSetsLoading(false);
    }
  }, [siteId, workspaceKey]);

  function updateQuestion(rowKey: string, update: (question: DraftQuestion) => DraftQuestion) {
    setQuestions((current) => current.map((question) => question.rowKey === rowKey ? update(question) : question));
    setDirty(true);
    setNotice("");
  }

  function addQuestion() {
    if (questions.length >= 20) return;
    setQuestions((current) => [...current, blankQuestion()]);
    setDirty(true);
  }

  function loadValveTemplate() {
    if (!isEditable || questions.length > 0) return;
    setQuestions(VALVE_QUESTION_TEMPLATE.map((question, index) => ({
      ...question,
      page_ids: [],
      rowKey: `valve-template-${index + 1}`,
    })));
    setDirty(true);
    setVersionError("");
    setNotice("已载入 20 条阀门采购问题示例。请确认题目并关联本站页面后保存。");
  }

  function removeQuestion(rowKey: string) {
    setQuestions((current) => current.filter((question) => question.rowKey !== rowKey));
    setDirty(true);
  }

  async function handleCreateSet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!siteId || !newSetName.trim()) return;
    if (!confirmDiscardDraft("创建新问题集", false)) return;
    setBusy(true);
    setVersionError("");
    try {
      const created = await createProcurementQuestionSet(workspaceKey, siteId, { name: newSetName.trim(), questions: [] });
      const refreshed = await refreshSets(siteId);
      const summary = refreshed.find((item) => item.id === created.id) || created;
      setCreateOpen(false);
      setNewSetName("");
      await loadVersion(summary, summary.current_version || 1, siteId);
      setNotice("问题集草稿已创建。请补充采购问题与页面映射后保存。");
    } catch (error) {
      setVersionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleSave() {
    if (!selectedSite || !selectedSet || !version || !isEditable) return;
    const incomplete = completeQuestions(questions);
    if (incomplete) {
      setVersionError(incomplete);
      return;
    }
    setBusy(true);
    setVersionError("");
    setNotice("");
    try {
      const saved = await updateProcurementQuestionSetVersion(workspaceKey, siteId, selectedSet.id, version.version, version.edit_version, questions.map(toInput));
      setVersion(saved);
      setQuestions(saved.questions.map(fromSavedQuestion));
      setDirty(false);
      await refreshSets();
      setNotice(`草稿 v${saved.version} 已保存。`);
    } catch (error) {
      setVersionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleFreeze() {
    if (!selectedSite || !selectedSet || !version || !isEditable || dirty) return;
    const issue = validateFreeze(questions);
    if (issue) {
      setVersionError(issue);
      return;
    }
    setBusy(true);
    setVersionError("");
    setNotice("");
    try {
      const frozen = await freezeProcurementQuestionSetVersion(workspaceKey, siteId, selectedSet.id, version.version, version.edit_version);
      setVersion(frozen);
      const refreshed = await refreshSets();
      setSelectedSetId(selectedSet.id);
      if (refreshed.length) setSets(refreshed);
      setNotice(`问题集 v${frozen.version} 已冻结。`);
    } catch (error) {
      setVersionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleCloneVersion() {
    if (!selectedSite || !selectedSet || selectedSet.current_state !== "frozen") return;
    setBusy(true);
    setVersionError("");
    setNotice("");
    try {
      const draft = await createProcurementQuestionSetVersion(workspaceKey, siteId, selectedSet.id, selectedSet.current_version);
      const refreshed = await refreshSets();
      const summary = refreshed.find((item) => item.id === selectedSet.id) || { ...selectedSet, current_version: draft.version, current_state: draft.state };
      await loadVersion(summary, draft.version, siteId);
      setNotice(`已创建 v${draft.version} 草稿。`);
    } catch (error) {
      setVersionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function onCreateOpen() {
    setVersionError("");
    setNotice("");
    setCreateOpen((current) => !current);
  }

  function requestLoadVersion(set: ProcurementQuestionSetSummary, requestedVersion: number, force = false) {
    if (!force && set.id === selectedSetId && requestedVersion === versionNumber) return;
    if (!confirmDiscardDraft("切换问题集或版本")) return;
    setCreateOpen(false);
    setNewSetName("");
    setDirty(false);
    void loadVersion(set, requestedVersion);
  }

  const setCreateForm = createOpen && <form className="proc-create-form" onSubmit={(event) => void handleCreateSet(event)}>
    <label className="field-label">问题集名称<input autoFocus value={newSetName} onChange={(event) => setNewSetName(event.target.value)} maxLength={200} placeholder="例如：工业阀门采购问题集" required /></label>
    <div className="proc-create-actions"><button className="button button-secondary small" type="button" onClick={() => setCreateOpen(false)} disabled={busy}>取消</button><button className="button button-primary small" type="submit" disabled={busy || !siteId}>{busy ? <LoaderCircle size={14} className="spin" /> : <Plus size={14} />}创建草稿</button></div>
  </form>;

  return <div className="page-wrap procurement-wrap">
    <div className="page-heading">
      <div>
        <div className="eyebrow">采购意图 <span>·</span> 页面映射</div>
        <h1>采购问题集</h1>
        <p>整理采购者真实会问的问题，并关联能提供依据的本站页面。</p>
      </div>
      <div className="heading-actions">
        <button className="button button-secondary" type="button" onClick={() => { void refreshSites(); if (siteId) { void refreshSets(); void listPages(siteId).then(setPages).catch((error) => setPagesError(errorMessage(error))); } }} disabled={sitesLoading || setsLoading || pagesLoading} title="刷新站点、问题集和页面列表"><RefreshCw size={15} className={sitesLoading || setsLoading || pagesLoading ? "spin" : ""} />刷新</button>
      </div>
    </div>

    {sitesError && <div className="alert alert-error" role="alert"><AlertCircle size={16} /><div><strong>站点列表加载失败</strong><span>{sitesError}</span></div><button className="icon-button subtle" type="button" onClick={() => void refreshSites()} aria-label="重试站点列表"><RefreshCw size={14} /></button></div>}

    <section className="proc-site-bar" aria-label="选择站点">
      <label className="proc-site-label" htmlFor="proc-site-select">站点</label>
      <div className="proc-site-select-wrap"><select id="proc-site-select" value={siteId} onChange={(event) => {
        const nextSiteId = event.target.value;
        if (nextSiteId === siteId || !confirmDiscardDraft("切换站点")) return;
        setDirty(false);
        setNewSetName("");
        setSiteId(nextSiteId);
        setPageSearch("");
        setCreateOpen(false);
      }} disabled={sitesLoading || sites.length === 0 || busy}>
        {sites.length === 0 && <option value="">{sitesLoading ? "正在读取站点" : "没有可用站点"}</option>}
        {sites.map((site) => <option key={site.id} value={site.id}>{site.name} · {site.origin}</option>)}
      </select><ChevronDown size={15} aria-hidden="true" /></div>
      {selectedSite && <span className={`proc-site-badge ${selectedSite.is_synthetic ? "synthetic" : ""}`}>{selectedSite.is_synthetic ? "合成演示站点" : "真实站点"}</span>}
      {!sitesLoading && sites.length === 0 && !sitesError && <span className="proc-site-help">先在“站点与审计”登记或选择站点。</span>}
    </section>

    {!sitesLoading && !sitesError && sites.length > 0 && <div className="proc-layout">
      <aside className="proc-set-sidebar" aria-label="问题集列表">
        <div className="proc-set-heading"><div><h2>问题集</h2><span className="count-label">{setsLoading ? "加载中" : `${sets.length} 个`}</span></div><button className="icon-button" type="button" onClick={onCreateOpen} title="新建问题集" aria-label="新建问题集"><Plus size={16} /></button></div>
        {setCreateForm}
        {setsError && <div className="proc-list-error" role="alert"><AlertCircle size={14} /><span>{setsError}</span><button className="text-button" type="button" onClick={() => void refreshSets()}>重试</button></div>}
        {setsLoading ? <div className="proc-set-loading"><span className="skeleton" /><span className="skeleton" /></div> : sets.length === 0 ? <div className="proc-list-empty"><FileText size={18} /><strong>还没有问题集</strong><span>创建草稿，逐条整理采购问题。</span><button type="button" className="button button-secondary small" onClick={onCreateOpen}><Plus size={14} />新建问题集</button></div> : <div className="proc-set-list">
          {sets.map((set) => <button key={set.id} type="button" className={`proc-set-item ${set.id === selectedSetId ? "selected" : ""}`} onClick={() => requestLoadVersion(set, set.current_version)} disabled={versionLoading || busy}>
            <span className="proc-set-name">{set.name}</span><span className="proc-set-meta"><span>v{set.current_version}</span><span className={`proc-state ${set.current_state === "frozen" ? "frozen" : "draft"}`}>{set.current_state === "frozen" ? "已冻结" : "草稿"}</span></span>
          </button>)}
        </div>}
      </aside>

      <main className="proc-editor" aria-label="采购问题编辑器" aria-busy={versionLoading}>
        {!selectedSet ? <div className="proc-editor-empty"><FileText size={24} /><strong>选择或新建一个问题集</strong><span>问题集按站点保存。已冻结版本不会被覆盖，可从冻结版本创建下一版草稿。</span></div> : <>
          <div className="proc-editor-header">
            <div className="proc-editor-title"><div><span className="proc-set-title-label">问题集</span><h2>{selectedSet.name}</h2></div><span className={`proc-state ${version?.state === "frozen" ? "frozen" : "draft"}`}>{version?.state === "frozen" ? "已冻结" : "草稿"}</span></div>
            <div className="proc-editor-actions">
              <label className="proc-version-select"><span>版本</span><select value={versionNumber} onChange={(event) => requestLoadVersion(selectedSet, Number(event.target.value))} disabled={versionLoading || busy} aria-label="选择问题集版本">{Array.from({ length: selectedSet.current_version }, (_, index) => index + 1).map((number) => <option key={number} value={number}>v{number}{number === selectedSet.current_version ? " · 当前" : ""}</option>)}</select></label>
              {selectedSet.current_state === "frozen" && isCurrentVersion && <button className="button button-secondary small" type="button" onClick={() => void handleCloneVersion()} disabled={busy || versionLoading}><FileText size={14} />创建下一版草稿</button>}
              {isEditable && <button className="button button-secondary small" type="button" onClick={() => void handleSave()} disabled={busy || !dirty || versionLoading}><Save size={14} />{busy ? "保存中" : "保存草稿"}</button>}
              {isEditable && <button className="button button-primary small" type="button" onClick={() => void handleFreeze()} disabled={busy || versionLoading || dirty || Boolean(freezeIssue)} title={freezeIssue || "冻结当前草稿"}>{busy ? <LoaderCircle size={14} className="spin" /> : <Snowflake size={14} />}冻结版本</button>}
            </div>
          </div>

              {versionLoading ? <div className="proc-loading"><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div> : versionError ? <div className="alert alert-error compact" role="alert"><AlertCircle size={15} /><span>{versionError}</span><button className="button button-secondary small" type="button" onClick={() => requestLoadVersion(selectedSet, versionNumber, true)}><RefreshCw size={13} />重新加载</button></div> : version && <>
            {notice && <div className="proc-notice" role="status"><Check size={15} /><span>{notice}</span></div>}
            {dirty && <div className="proc-dirty-note" role="status">有未保存的修改；切换时会先确认是否丢弃。</div>}
            <div className="proc-progress-row"><div className="proc-progress-copy"><strong>{questions.length} / 20 条问题</strong><span>{mappedCount} 条已关联页面{dirty ? " · 修改未保存" : ""}</span></div><div className="proc-progress" aria-label={`已填写 ${questions.length} 条，共 20 条`}><span style={{ width: `${Math.min(100, questions.length / 20 * 100)}%` }} /></div></div>
            {version.state === "frozen" && <div className="proc-readonly-note"><ShieldCheck size={14} /><span>冻结版本只读。需要修改时，请从最新冻结版本创建下一版草稿。</span></div>}
            {pagesError && <div className="alert alert-error compact" role="alert"><AlertCircle size={15} /><span>读取站点页面失败：{pagesError}</span><button type="button" className="button button-secondary small" onClick={() => { setPagesLoading(true); void listPages(siteId).then((result) => { setPages(result); setPagesError(""); }).catch((error) => setPagesError(errorMessage(error))).finally(() => setPagesLoading(false)); }}><RefreshCw size={13} />重试</button></div>}
            {!pagesLoading && !pagesError && pagesWithId.length === 0 && <div className="proc-mapping-warning"><CircleHelp size={15} /><span>本站还没有可映射的页面。完成一次页面采集后刷新此列表。</span></div>}
            {questions.length === 0 ? <div className="proc-questions-empty"><strong>草稿为空</strong><span>选择一个起始方式，后续都可以逐条编辑。</span>{isEditable && <div className="proc-empty-actions"><button className="button button-secondary small" type="button" onClick={loadValveTemplate}><FileText size={14} />载入阀门问题示例（20 条）</button><button className="text-button" type="button" onClick={addQuestion}><Plus size={14} />从空白开始</button></div>}<small>示例只包含采购问题，不代表供应商参数、认证或服务承诺。</small></div> : <div className="proc-question-list">
              {questions.map((question, index) => <article className="proc-question" key={question.rowKey}>
                <div className="proc-question-head"><div className="proc-question-number">{String(index + 1).padStart(2, "0")}</div><div className="proc-question-heading"><strong>采购问题</strong><span>{question.page_ids.length ? `${question.page_ids.length} 个页面` : "尚未关联页面"}</span></div>{isEditable && <button className="icon-button proc-remove" type="button" onClick={() => removeQuestion(question.rowKey)} title="删除此问题" aria-label={`删除第 ${index + 1} 条问题`}><Trash2 size={15} /></button>}</div>
                <div className="proc-question-fields">
                  <label className="field-label proc-question-text">采购者会怎么问<textarea value={question.question} onChange={(event) => updateQuestion(question.rowKey, (row) => ({ ...row, question: event.target.value }))} maxLength={2000} rows={2} placeholder="例如：How do I choose an actuator for a high-pressure steam line?" disabled={!isEditable} /></label>
                  <label className="field-label">产品<input value={question.product} onChange={(event) => updateQuestion(question.rowKey, (row) => ({ ...row, product: event.target.value }))} maxLength={200} placeholder="产品或系列" disabled={!isEditable} /></label>
                  <label className="field-label">用途<input value={question.use_case} onChange={(event) => updateQuestion(question.rowKey, (row) => ({ ...row, use_case: event.target.value }))} maxLength={300} placeholder="使用场景" disabled={!isEditable} /></label>
                  <label className="field-label">采购角色<input value={question.buyer_role} onChange={(event) => updateQuestion(question.rowKey, (row) => ({ ...row, buyer_role: event.target.value }))} maxLength={120} placeholder="例如：设备工程师" disabled={!isEditable} /></label>
                  <label className="field-label">采购阶段<input value={question.purchase_stage} onChange={(event) => updateQuestion(question.rowKey, (row) => ({ ...row, purchase_stage: event.target.value }))} maxLength={120} placeholder="例如：供应商筛选" disabled={!isEditable} /></label>
                  <label className="field-label">目标市场<input value={question.target_market} onChange={(event) => updateQuestion(question.rowKey, (row) => ({ ...row, target_market: event.target.value }))} maxLength={120} placeholder="国家或区域" disabled={!isEditable} /></label>
                  <label className="field-label">语言<input value={question.language} onChange={(event) => updateQuestion(question.rowKey, (row) => ({ ...row, language: event.target.value }))} maxLength={35} placeholder="例如：English" disabled={!isEditable} /></label>
                </div>
                <div className="proc-mapping-block">
                  <div className="proc-mapping-title"><div><strong>关联页面</strong><span>只显示当前站点采集到的页面</span></div><span>{question.page_ids.length} 已选</span></div>
                  {pagesLoading ? <div className="proc-pages-loading"><span className="skeleton" /><span className="skeleton" /></div> : pagesError ? <span className="proc-pages-inline-error">页面暂不可用</span> : pagesWithId.length > 0 ? <>
                    {pagesWithId.length > 4 && <label className="proc-page-search"><input value={pageSearch} onChange={(event) => setPageSearch(event.target.value)} placeholder="筛选 URL 或标题" aria-label="筛选可映射页面" /><span><CircleHelp size={12} />{pagesWithId.length} 页</span></label>}
                    <div className="proc-page-options">{matchingPages.length ? matchingPages.map((page) => {
                      const pageId = page.page_id as number;
                      const checked = question.page_ids.includes(pageId);
                      return <label className={`proc-page-option ${checked ? "checked" : ""}`} key={pageId}>
                        <input type="checkbox" checked={checked} disabled={!isEditable} onChange={(event) => updateQuestion(question.rowKey, (row) => ({ ...row, page_ids: event.target.checked ? [...row.page_ids, pageId] : row.page_ids.filter((id) => id !== pageId) }))} />
                        <span className="proc-page-option-copy"><strong>{page.title || pagePath(page)}</strong><small>{page.url}</small></span>
                        {page.is_synthetic && <span className="synthetic-label">合成页面</span>}
                      </label>;
                    }) : <span className="proc-no-pages-match">没有匹配页面</span>}</div>
                  </> : <span className="proc-pages-inline-error">暂无可映射页面</span>}
                </div>
              </article>)}
            </div>}
            {isEditable && <div className="proc-add-row"><button className="button button-secondary" type="button" onClick={addQuestion} disabled={questions.length >= 20 || busy}><Plus size={15} />添加问题</button><span>{questions.length >= 20 ? "已达到版本上限" : "最多 20 条；请使用页面中可核实的信息填写。"}</span></div>}
            {isEditable && freezeIssue && <div className="proc-freeze-guidance"><CircleHelp size={14} /><span>{freezeIssue}</span></div>}
          </>}
        </>}
      </main>
    </div>}
  </div>;
}
