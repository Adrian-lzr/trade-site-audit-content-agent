import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowUpRight, Check, ChevronDown, CircleHelp, Edit3, ExternalLink, FileCheck2, FileText, Info, LoaderCircle, Plus, RefreshCw, Search, Send, ShieldCheck, BookOpen, X } from "lucide-react";
import {
  addChangeRevision,
  createContentGenerationTask,
  decideChangeApproval,
  Fact,
  getContentGenerationTask,
  getSnapshot,
  getProcurementQuestionSet,
  listContentGenerationTasks,
  listFacts,
  listKnowledgeEntries,
  listPages,
  listProcurementQuestionSets,
  listSites,
  PageSnapshot,
  ProcurementQuestion,
  ProcurementQuestionSetSummary,
  ProcurementQuestionSetVersion,
  Site,
  submitChangeForApproval,
  ContentGenerationTask,
  ContentGenerationTaskSummary,
  ContentGenerationItem,
  KnowledgeEntry,
  SnapshotDetail,
} from "./api";

type TaskDraftItem = {
  key: string;
  question: ProcurementQuestion;
  pageId: number;
  snapshot: PageSnapshot;
  requestSummary: string;
  requiredFactIds: number[];
};

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : "发生未知错误";
}

function knowledgeSourceKindLabel(value: string) {
  return ({
    official_guidance: "官方指导",
    official_regulator: "官方监管机构",
    official_trade_portal: "官方贸易查询",
    intergovernmental_data: "政府间组织数据",
    standards_body: "标准机构",
    internal_heuristic: "内部启发式",
  } as Record<string, string>)[value] || value;
}

function knowledgeSourceDateLabel(value: string) {
  if (value === "continuously maintained") return "持续维护";
  if (value === "continuously updated") return "持续更新";
  return value;
}

function knowledgeFreshnessLabel(value: string) {
  return value === "revalidate_before_use" ? "使用前复核" : value;
}

function knowledgeSearchMatches(entry: KnowledgeEntry, query: string) {
  const text = [entry.title, entry.summary, entry.scope, entry.market_scope, entry.claim_type, entry.source_kind, entry.topic_tags]
    .join(" ")
    .toLocaleLowerCase();
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  const marketAliases: Record<string, string[]> = {
    "united kingdom": ["united kingdom", "uk", "britain", "英国"],
    uk: ["united kingdom", "uk", "britain", "英国"],
    "united states": ["united states", "u.s.", "usa", "america", "美国"],
    usa: ["united states", "u.s.", "usa", "america", "美国"],
    us: ["united states", "u.s.", "usa", "america", "美国"],
    "european union": ["european union", "eu", "欧盟"],
    eu: ["european union", "eu", "欧盟"],
    germany: ["germany", "deutschland", "德国", "european union", "欧盟"],
    france: ["france", "法国", "european union", "欧盟"],
  };
  const aliases = marketAliases[needle];
  return aliases ? aliases.some((term) => text.includes(term)) : text.includes(needle);
}

function asDate(value?: string | null) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function isFactCurrent(fact: Fact, now = Date.now()) {
  if (fact.status !== "confirmed" || fact.visibility !== "public") return false;
  const validFrom = new Date(fact.valid_from).getTime();
  const validUntil = fact.valid_until ? new Date(fact.valid_until).getTime() : Number.POSITIVE_INFINITY;
  return Number.isFinite(validFrom) && validFrom <= now && validUntil > now;
}

function newestSnapshots(pages: PageSnapshot[]) {
  const seen = new Set<number>();
  return pages.filter((page) => {
    if (!Number.isInteger(page.page_id) || !page.page_id || seen.has(page.page_id)) return false;
    seen.add(page.page_id);
    return true;
  });
}

function snapshotId(snapshot: PageSnapshot) {
  const id = Number(snapshot.id);
  return Number.isSafeInteger(id) && id > 0 ? id : 0;
}

function factLocatorHref(locator: string) {
  return /^https?:\/\//i.test(locator) ? locator : "";
}

function fieldValue(value: unknown) {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return JSON.stringify(value, null, 2);
}

function factBindingKey(reference: { fact_id?: unknown; series_id?: unknown; version?: unknown }) {
  return `${String(reference.fact_id ?? "")}:${String(reference.series_id ?? "")}:${String(reference.version ?? "")}`;
}

function factBindingIsCurrent(reference: { fact_id?: unknown; series_id?: unknown; version?: unknown }, fact: Fact | undefined) {
  return Boolean(
    fact
      && String(fact.series_id) === String(reference.series_id ?? fact.series_id)
      && Number(fact.version) === Number(reference.version)
      && isFactCurrent(fact),
  );
}

function revisionFieldEntries(fieldDiff: Record<string, unknown>) {
  return Object.entries(fieldDiff || {}).filter(([field]) => !["claims", "claim_bindings", "validation_report", "missing_information", "answered_question_ids"].includes(field));
}

function revisionClaims(fieldDiff: Record<string, unknown>) {
  const candidate = fieldDiff?.claims ?? fieldDiff?.claim_bindings;
  if (!Array.isArray(candidate)) return [] as Array<Record<string, unknown>>;
  return candidate.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)));
}

function revisionMissingInformation(fieldDiff: Record<string, unknown>) {
  const candidate = fieldDiff?.missing_information;
  if (Array.isArray(candidate)) return candidate.map((item) => String(item)).filter(Boolean);
  if (typeof candidate === "string" && candidate.trim()) return [candidate.trim()];
  return [];
}

function snapshotContent(detail: SnapshotDetail) {
  const value = detail.content;
  if (typeof value === "string" && value.trim()) return value;
  const candidate = detail as SnapshotDetail & { body?: unknown; text?: unknown };
  if (typeof candidate.body === "string" && candidate.body.trim()) return candidate.body;
  if (typeof candidate.text === "string" && candidate.text.trim()) return candidate.text;
  return "";
}

function taskStatus(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "处理中",
    succeeded: "已完成",
    failed: "失败",
    needs_information: "需要补充资料",
    awaiting_review: "等待人工审核",
    approved: "已通过",
    rejected: "已退回修改",
  };
  return labels[status] || status;
}

function changeStatus(status: string) {
  const labels: Record<string, string> = {
    draft: "待提交审核",
    pending_approval: "等待审核决定",
    approved: "已通过",
    rejected: "已退回",
    failed: "审核不可用",
  };
  return labels[status] || status;
}

function statusClass(status: string) {
  if (["succeeded", "approved", "frozen"].includes(status)) return "good";
  if (["failed", "rejected", "needs_information"].includes(status)) return "bad";
  if (["running", "queued", "pending_approval", "awaiting_review", "draft"].includes(status)) return "working";
  return "muted";
}

export function ContentReviewWorkspace({
  workspaceKey,
  onDirtyChange,
  onOpenFactLibrary,
}: {
  workspaceKey: string;
  onDirtyChange?: (hasUnsavedDraft: boolean) => void;
  onOpenFactLibrary?: () => void;
}) {
  const [sites, setSites] = useState<Site[]>([]);
  const [siteId, setSiteId] = useState("");
  const [sitesLoading, setSitesLoading] = useState(true);
  const [sitesError, setSitesError] = useState("");
  const [pages, setPages] = useState<PageSnapshot[]>([]);
  const [pagesLoading, setPagesLoading] = useState(false);
  const [pagesError, setPagesError] = useState("");
  const [facts, setFacts] = useState<Fact[]>([]);
  const [factsLoading, setFactsLoading] = useState(false);
  const [factsError, setFactsError] = useState("");
  const [questionSets, setQuestionSets] = useState<ProcurementQuestionSetSummary[]>([]);
  const [questionSetsLoading, setQuestionSetsLoading] = useState(false);
  const [questionSetsError, setQuestionSetsError] = useState("");
  const [querySetId, setQuerySetId] = useState<number | null>(null);
  const [querySetVersion, setQuerySetVersion] = useState<ProcurementQuestionSetVersion | null>(null);
  const [frozenVersions, setFrozenVersions] = useState<ProcurementQuestionSetVersion[]>([]);
  const [querySetVersionLoading, setQuerySetVersionLoading] = useState(false);
  const [querySetVersionError, setQuerySetVersionError] = useState("");
  const [selectedPages, setSelectedPages] = useState<Record<number, number>>({});
  const [draftItems, setDraftItems] = useState<TaskDraftItem[]>([]);
  const [factSearch, setFactSearch] = useState("");
  const [taskSummaries, setTaskSummaries] = useState<ContentGenerationTaskSummary[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [tasksError, setTasksError] = useState("");
  const [taskDetail, setTaskDetail] = useState<ContentGenerationTask | null>(null);
  const [taskDetailLoading, setTaskDetailLoading] = useState(false);
  const [taskDetailError, setTaskDetailError] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [approvalComments, setApprovalComments] = useState<Record<number, string>>({});
  const [revisionEditors, setRevisionEditors] = useState<Record<number, boolean>>({});
  const [revisionDrafts, setRevisionDrafts] = useState<Record<number, string>>({});
  const [revisionEditErrors, setRevisionEditErrors] = useState<Record<number, string>>({});
  const [sourceSnapshot, setSourceSnapshot] = useState<SnapshotDetail | null>(null);
  const [sourceSnapshotId, setSourceSnapshotId] = useState<number | null>(null);
  const [sourceSnapshotLoading, setSourceSnapshotLoading] = useState(false);
  const [sourceSnapshotError, setSourceSnapshotError] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [notice, setNotice] = useState("");
  const [knowledgeEntries, setKnowledgeEntries] = useState<KnowledgeEntry[]>([]);
  const [knowledgeLoading, setKnowledgeLoading] = useState(true);
  const [knowledgeError, setKnowledgeError] = useState("");
  const [knowledgeSearch, setKnowledgeSearch] = useState("");

  const hasUnsavedDraft = draftItems.length > 0;
  const onDirtyChangeRef = useRef(onDirtyChange);
  onDirtyChangeRef.current = onDirtyChange;
  const siteIdRef = useRef(siteId);
  siteIdRef.current = siteId;
  const hasUnsavedDraftRef = useRef(hasUnsavedDraft);
  hasUnsavedDraftRef.current = hasUnsavedDraft;

  const selectedSite = sites.find((site) => site.id === siteId) || null;
  const latestPageList = useMemo(() => newestSnapshots(pages), [pages]);
  const pagesById = useMemo(() => new Map(latestPageList.map((page) => [page.page_id as number, page])), [latestPageList]);
  const eligibleFacts = useMemo(() => facts.filter((fact) => isFactCurrent(fact)), [facts]);
  const mappedQuestionsWithSnapshot = querySetVersion?.questions.filter((question) => pageChoices(question).length > 0).length || 0;
  const questionSetReady = Boolean(querySetVersion?.state === "frozen" && querySetVersion.questions.length > 0);
  const questionSetReadinessState = questionSetsLoading || querySetVersionLoading
    ? "loading"
    : questionSetsError || querySetVersionError
      ? "error"
      : questionSetReady ? "ready" : "attention";
  const snapshotReadinessState = pagesLoading || querySetVersionLoading
    ? "loading"
    : pagesError ? "error" : mappedQuestionsWithSnapshot > 0 ? "ready" : "attention";
  const factsReadinessState = factsLoading ? "loading" : factsError ? "error" : eligibleFacts.length > 0 ? "ready" : "attention";
  const questionSetReadinessText = questionSetsLoading || querySetVersionLoading
    ? "正在读取冻结版本。"
    : questionSetsError ? "问题集读取失败，请刷新资料后重试。"
      : querySetVersionError && !querySetVersion ? querySetVersionError
        : questionSetReady ? `v${querySetVersion!.version} 已冻结，包含 ${querySetVersion!.questions.length} 个问题。`
          : querySetVersion ? "当前版本未冻结，不能创建内容任务。" : "选择问题集后检查冻结状态。";
  const snapshotReadinessText = pagesLoading || querySetVersionLoading
    ? "正在检查问题对应的页面快照。"
    : pagesError ? "页面快照读取失败，请刷新资料后重试。"
      : !querySetVersion ? "选择有冻结版本的问题集后检查页面映射。"
        : mappedQuestionsWithSnapshot > 0 ? `${mappedQuestionsWithSnapshot} 个问题关联了带内容校验值的页面快照。`
          : "关联页面还没有可用快照；请到站点与审计完成采集后刷新资料。";
  const factsReadinessText = factsLoading
    ? "正在读取企业事实。"
    : factsError ? "企业事实读取失败，请刷新资料后重试。"
      : eligibleFacts.length > 0 ? `${eligibleFacts.length} 条事实已确认、可公开且当前有效。`
        : "没有符合条件的事实；需先导入，并确认其可公开且处于有效期内。";
  const filteredFacts = useMemo(() => {
    const needle = factSearch.trim().toLowerCase();
    if (!needle) return eligibleFacts;
    return eligibleFacts.filter((fact) => `${fact.subject} ${fact.predicate} ${fact.value || ""} ${fact.source_id}`.toLowerCase().includes(needle));
  }, [eligibleFacts, factSearch]);
  const filteredKnowledgeEntries = useMemo(() => {
    return knowledgeEntries.filter((entry) => knowledgeSearchMatches(entry, knowledgeSearch));
  }, [knowledgeEntries, knowledgeSearch]);

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

  function confirmDiscardTaskDraft(action: string) {
    if (draftItems.length === 0) return true;
    return window.confirm(`当前内容任务草稿有 ${draftItems.length} 项，包含改稿要求、事实选择和页面快照绑定。${action}会清空这些项目，确定继续吗？`);
  }

  const refreshSites = useCallback(async () => {
    setSitesLoading(true);
    setSitesError("");
    try {
      const result = await listSites();
      const activeSiteId = siteIdRef.current;
      const activeSiteRemoved = Boolean(activeSiteId && !result.some((site) => site.id === activeSiteId));
      if (activeSiteRemoved && hasUnsavedDraftRef.current) {
        const discard = window.confirm("刷新后当前站点已不在列表中。切换到其他站点会清空当前任务草稿，确定继续吗？");
        if (!discard) {
          setSites(result);
          setNotice("当前站点不在最新列表中；任务草稿已保留。");
          return;
        }
        setDraftItems([]);
      }
      setSites(result);
      setSiteId((current) => result.some((site) => site.id === current) ? current : result[0]?.id || "");
    } catch (error) {
      setSitesError(messageOf(error));
    } finally {
      setSitesLoading(false);
    }
  }, []);

  useEffect(() => { void refreshSites(); }, [refreshSites]);

  useEffect(() => {
    let cancelled = false;
    setKnowledgeLoading(true);
    setKnowledgeError("");
    void listKnowledgeEntries().then((entries) => {
      if (!cancelled) setKnowledgeEntries(entries);
    }).catch((error) => {
      if (!cancelled) setKnowledgeError(messageOf(error));
    }).finally(() => {
      if (!cancelled) setKnowledgeLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const refreshTasks = useCallback(async (activeSite = siteId) => {
    if (!activeSite) return [];
    setTasksLoading(true);
    setTasksError("");
    try {
      const result = await listContentGenerationTasks(workspaceKey, activeSite);
      setTaskSummaries(result);
      return result;
    } catch (error) {
      setTasksError(messageOf(error));
      return [];
    } finally {
      setTasksLoading(false);
    }
  }, [siteId, workspaceKey]);

  useEffect(() => {
    let cancelled = false;
    setPages([]);
    setFacts([]);
    setQuestionSets([]);
    setQuerySetId(null);
    setQuerySetVersion(null);
    setFrozenVersions([]);
    setDraftItems([]);
    setTaskSummaries([]);
    setTaskDetail(null);
    setPagesError("");
    setFactsError("");
    setQuestionSetsError("");
    setTasksError("");
    setNotice("");
    if (!siteId) return () => { cancelled = true; };
    setPagesLoading(true);
    setFactsLoading(true);
    setQuestionSetsLoading(true);
    setTasksLoading(true);
    void Promise.allSettled([
      listPages(siteId),
      listFacts(workspaceKey),
      listProcurementQuestionSets(workspaceKey, siteId),
      listContentGenerationTasks(workspaceKey, siteId),
    ]).then(([pageResult, factResult, setResult, taskResult]) => {
      if (cancelled) return;
      if (pageResult.status === "fulfilled") setPages(pageResult.value);
      else setPagesError(messageOf(pageResult.reason));
      if (factResult.status === "fulfilled") setFacts(factResult.value);
      else setFactsError(messageOf(factResult.reason));
      if (setResult.status === "fulfilled") setQuestionSets(setResult.value);
      else setQuestionSetsError(messageOf(setResult.reason));
      if (taskResult.status === "fulfilled") setTaskSummaries(taskResult.value);
      else setTasksError(messageOf(taskResult.reason));
      setPagesLoading(false);
      setFactsLoading(false);
      setQuestionSetsLoading(false);
      setTasksLoading(false);
    });
    return () => { cancelled = true; };
  }, [siteId, workspaceKey]);

  useEffect(() => {
    let cancelled = false;
    setQuerySetVersion(null);
    setFrozenVersions([]);
    setQuerySetVersionError("");
    setSelectedPages({});
    if (!siteId || querySetId === null) return () => { cancelled = true; };
    setQuerySetVersionLoading(true);
    void getProcurementQuestionSet(workspaceKey, siteId, querySetId).then((detail) => {
      if (cancelled) return;
      const frozen = [...detail.versions].filter((version) => version.state === "frozen").sort((left, right) => right.version - left.version);
      setFrozenVersions(frozen);
      setQuerySetVersion(frozen[0] || null);
      if (!frozen.length) setQuerySetVersionError("该问题集还没有已冻结版本。请先补齐 20 条问题并冻结，再创建内容任务。");
    }).catch((error) => {
      if (!cancelled) setQuerySetVersionError(messageOf(error));
    }).finally(() => {
      if (!cancelled) setQuerySetVersionLoading(false);
    });
    return () => { cancelled = true; };
  }, [querySetId, siteId, workspaceKey]);

  async function refreshInputs() {
    if (!siteId) return;
    setPagesLoading(true);
    setFactsLoading(true);
    setQuestionSetsLoading(true);
    const [pageResult, factResult, setResult] = await Promise.allSettled([
      listPages(siteId),
      listFacts(workspaceKey),
      listProcurementQuestionSets(workspaceKey, siteId),
    ]);
    if (pageResult.status === "fulfilled") { setPages(pageResult.value); setPagesError(""); }
    else setPagesError(messageOf(pageResult.reason));
    if (factResult.status === "fulfilled") { setFacts(factResult.value); setFactsError(""); }
    else setFactsError(messageOf(factResult.reason));
    if (setResult.status === "fulfilled") { setQuestionSets(setResult.value); setQuestionSetsError(""); }
    else setQuestionSetsError(messageOf(setResult.reason));
    setPagesLoading(false);
    setFactsLoading(false);
    setQuestionSetsLoading(false);
    await refreshTasks(siteId);
  }

  function pageChoices(question: ProcurementQuestion) {
    return question.page_mappings.flatMap((mapping) => {
      const snapshot = pagesById.get(mapping.page_id);
      return snapshot && snapshotId(snapshot) && snapshot.content_hash
        ? [{ mapping, snapshot }]
        : [];
    });
  }

  function addDraftItem(question: ProcurementQuestion) {
    const choices = pageChoices(question);
    const pageId = selectedPages[question.id] || choices[0]?.mapping.page_id;
    const selected = choices.find((item) => item.mapping.page_id === pageId);
    if (!selected) {
      setNotice("该问题关联的页面没有可用快照。请先完成页面采集并刷新页面列表。");
      return;
    }
    if (draftItems.length >= 5) {
      setNotice("每个内容任务最多包含 5 个问题与页面组合。");
      return;
    }
    if (draftItems.some((item) => item.question.id === question.id && item.pageId === selected.mapping.page_id)) {
      setNotice("该问题与页面组合已经加入本次任务。");
      return;
    }
    setDraftItems((current) => [...current, {
      key: `${question.id}-${selected.mapping.page_id}-${Date.now()}`,
      question,
      pageId: selected.mapping.page_id,
      snapshot: selected.snapshot,
      requestSummary: question.question,
      requiredFactIds: [],
    }]);
    setNotice("");
  }

  function updateDraftItem(key: string, update: (item: TaskDraftItem) => TaskDraftItem) {
    setDraftItems((current) => current.map((item) => item.key === key ? update(item) : item));
  }

  function removeDraftItem(key: string) {
    setDraftItems((current) => current.filter((item) => item.key !== key));
  }

  function validateTask() {
    if (!querySetVersion || querySetVersion.state !== "frozen") return "请选择已冻结的问题集版本。";
    if (draftItems.length === 0) return "至少加入一个问题与页面组合。";
    const invalid = draftItems.findIndex((item) => !item.requestSummary.trim() || item.requestSummary.trim().length > 800 || item.requiredFactIds.length === 0 || !snapshotId(item.snapshot) || !item.snapshot.content_hash);
    if (invalid >= 0) return `第 ${invalid + 1} 项需要填写不超过 800 字的任务说明、选择至少一个事实，并绑定有效页面快照。`;
    return "";
  }

  async function handleCreateTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!siteId || !querySetId || !querySetVersion) return;
    const issue = validateTask();
    if (issue) { setNotice(issue); return; }
    setBusyAction("create-task");
    setNotice("");
    try {
      const created = await createContentGenerationTask(workspaceKey, siteId, querySetId, querySetVersion.version, draftItems.map((item) => ({
        question_id: item.question.id,
        page_id: item.pageId,
        expected_snapshot_id: snapshotId(item.snapshot),
        expected_snapshot_hash: item.snapshot.content_hash || "",
        request_summary: item.requestSummary.trim(),
        required_fact_ids: item.requiredFactIds,
      })));
      const updated = await refreshTasks(siteId);
      setTaskDetail(created);
      setTaskDetailError("");
      setDraftItems([]);
      setNotice(`内容任务 #${created.id} 已创建，后台处理状态为“${taskStatus(created.status)}”。`);
      if (!updated.some((item) => item.id === created.id)) setTaskSummaries((current) => [created, ...current]);
    } catch (error) {
      setNotice(messageOf(error));
    } finally {
      setBusyAction("");
    }
  }

  async function loadTask(taskId: number) {
    if (!siteId) return;
    setTaskDetailLoading(true);
    setTaskDetailError("");
    setTaskDetail(null);
    try {
      const detail = await getContentGenerationTask(workspaceKey, siteId, taskId);
      setTaskDetail(detail);
    } catch (error) {
      setTaskDetailError(messageOf(error));
    } finally {
      setTaskDetailLoading(false);
    }
  }

  async function refreshTaskDetail() {
    if (!siteId || !taskDetail) return;
    setTaskDetailLoading(true);
    setTaskDetailError("");
    try {
      setTaskDetail(await getContentGenerationTask(workspaceKey, siteId, taskDetail.id));
      await refreshTasks(siteId);
    } catch (error) {
      setTaskDetailError(messageOf(error));
    } finally {
      setTaskDetailLoading(false);
    }
  }

  async function openSourceSnapshot(snapshotId: number) {
    setSourceSnapshotId(snapshotId);
    setSourceSnapshot(null);
    setSourceSnapshotError("");
    setSourceSnapshotLoading(true);
    try {
      setSourceSnapshot(await getSnapshot(String(snapshotId), workspaceKey));
    } catch (error) {
      setSourceSnapshotError(messageOf(error));
    } finally {
      setSourceSnapshotLoading(false);
    }
  }

  function startRevisionEdit(item: ContentGenerationItem) {
    const revision = item.change_request.revision;
    if (!revision) return;
    setRevisionDrafts((current) => ({ ...current, [item.id]: JSON.stringify(revision.field_diff || {}, null, 2) }));
    setRevisionEditErrors((current) => ({ ...current, [item.id]: "" }));
    setRevisionEditors((current) => ({ ...current, [item.id]: true }));
  }

  function cancelRevisionEdit(itemId: number) {
    setRevisionEditors((current) => ({ ...current, [itemId]: false }));
    setRevisionEditErrors((current) => ({ ...current, [itemId]: "" }));
  }

  async function saveRevisionEdit(item: ContentGenerationItem) {
    const change = item.change_request;
    const revision = change.revision;
    if (!revision) return;
    let fieldDiff: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(revisionDrafts[item.id] || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("改稿必须是 JSON 对象。");
      fieldDiff = parsed as Record<string, unknown>;
    } catch (error) {
      setRevisionEditErrors((current) => ({ ...current, [item.id]: error instanceof Error ? error.message : "改稿 JSON 无效。" }));
      return;
    }
    setBusyAction(`edit-${item.id}`);
    setRevisionEditErrors((current) => ({ ...current, [item.id]: "" }));
    try {
      await addChangeRevision({
        changeId: change.id,
        workspaceKey,
        expectedVersion: change.version,
        baseSnapshotId: revision.base_snapshot_id,
        baseContentHash: revision.base_content_hash,
        fieldDiff,
        factVersions: revision.fact_versions,
      });
      await loadTask(taskDetail!.id);
      setRevisionEditors((current) => ({ ...current, [item.id]: false }));
      setNotice(`变更 #${change.id} 已保存为新修订，请重新提交审核。旧批准不会沿用。`);
    } catch (error) {
      setRevisionEditErrors((current) => ({ ...current, [item.id]: messageOf(error) }));
    } finally {
      setBusyAction("");
    }
  }

  async function handleSubmitForApproval(item: ContentGenerationItem) {
    const change = item.change_request;
    if (!change.current_revision_id || !change.revision) {
      setTaskDetailError("该内容任务还没有可提交的改稿版本。请刷新任务状态。");
      return;
    }
    setBusyAction(`submit-${item.id}`);
    setTaskDetailError("");
    try {
      await submitChangeForApproval(change.id, change.version, workspaceKey);
      await loadTask(taskDetail!.id);
      setNotice(`变更 #${change.id} 已提交人工审核。`);
    } catch (error) {
      setTaskDetailError(messageOf(error));
    } finally {
      setBusyAction("");
    }
  }

  async function handleApproval(item: ContentGenerationItem, decision: "approved" | "rejected") {
    const change = item.change_request;
    const revision = change.revision;
    if (!revision || !change.current_revision_id) {
      setTaskDetailError("改稿版本信息不完整，不能提交审核决定。");
      return;
    }
    const reviewerHint = reviewer.trim() || "current-session";
    setBusyAction(`${decision}-${item.id}`);
    setTaskDetailError("");
    try {
      await decideChangeApproval({
        changeId: change.id,
        workspaceKey,
        reviewer: reviewerHint,
        decision,
        revisionId: revision.id,
        revisionHash: revision.content_hash,
        expectedVersion: change.version,
        comment: approvalComments[item.id],
      });
      await loadTask(taskDetail!.id);
      setNotice(decision === "approved" ? `变更 #${change.id} 已通过。` : `变更 #${change.id} 已退回。`);
    } catch (error) {
      setTaskDetailError(messageOf(error));
    } finally {
      setBusyAction("");
    }
  }

  const createIssue = validateTask();

  return <div className="page-wrap content-review-wrap">
    <div className="page-heading">
      <div><div className="eyebrow">事实约束改稿 <span>·</span> 人工审核</div><h1>内容任务</h1><p>用已冻结的采购问题、当前页面快照和已确认公开事实生成可审核的改稿草案。</p></div>
      <div className="heading-actions"><button className="button button-secondary" type="button" onClick={() => void refreshInputs()} disabled={!siteId || pagesLoading || factsLoading || tasksLoading}><RefreshCw size={15} className={pagesLoading || factsLoading || tasksLoading ? "spin" : ""} />刷新资料</button></div>
    </div>

    {sitesError && <div className="alert alert-error" role="alert"><AlertCircle size={16} /><div><strong>站点列表加载失败</strong><span>{sitesError}</span></div><button className="icon-button subtle" type="button" onClick={() => void refreshSites()} aria-label="重试站点列表"><RefreshCw size={14} /></button></div>}

    <section className="proc-site-bar" aria-label="选择站点">
      <label className="proc-site-label" htmlFor="content-site-select">站点</label>
      <div className="proc-site-select-wrap"><select id="content-site-select" value={siteId} onChange={(event) => {
        const nextSiteId = event.target.value;
        if (nextSiteId === siteId || !confirmDiscardTaskDraft("切换站点")) return;
        setDraftItems([]);
        setSiteId(nextSiteId);
        setNotice("");
      }} disabled={sitesLoading || sites.length === 0 || Boolean(busyAction)}>
        {sites.length === 0 && <option value="">{sitesLoading ? "正在读取站点" : "没有可用站点"}</option>}
        {sites.map((site) => <option key={site.id} value={site.id}>{site.name} · {site.origin}</option>)}
      </select><ChevronDown size={15} aria-hidden="true" /></div>
      {selectedSite && <span className={`proc-site-badge ${selectedSite.is_synthetic ? "synthetic" : ""}`}>{selectedSite.is_synthetic ? "合成演示站点" : "真实站点"}</span>}
      {!sitesLoading && sites.length === 0 && !sitesError && <span className="proc-site-help">请先登记站点并完成页面采集。</span>}
    </section>

    {!sitesLoading && !sitesError && sites.length > 0 && <section className="content-readiness" aria-labelledby="content-readiness-title">
      <div className="content-readiness-heading"><h2 id="content-readiness-title">创建前检查</h2><span>3 项条件</span></div>
      <ul className="content-readiness-list">
        <li className={`content-readiness-item ${questionSetReadinessState}`}>
          <span className="content-readiness-icon" aria-hidden="true">{questionSetReadinessState === "ready" ? <Check size={14} /> : questionSetReadinessState === "loading" ? <LoaderCircle size={14} className="spin" /> : questionSetReadinessState === "error" ? <AlertCircle size={14} /> : <CircleHelp size={14} />}</span>
          <span className="content-readiness-copy"><strong>冻结的问题集</strong><span>{questionSetReadinessText}</span></span>
        </li>
        <li className={`content-readiness-item ${snapshotReadinessState}`}>
          <span className="content-readiness-icon" aria-hidden="true">{snapshotReadinessState === "ready" ? <FileCheck2 size={14} /> : snapshotReadinessState === "loading" ? <LoaderCircle size={14} className="spin" /> : snapshotReadinessState === "error" ? <AlertCircle size={14} /> : <CircleHelp size={14} />}</span>
          <span className="content-readiness-copy"><strong>可用页面快照</strong><span>{snapshotReadinessText}</span></span>
        </li>
        <li className={`content-readiness-item facts ${factsReadinessState}`}>
          <span className="content-readiness-icon" aria-hidden="true">{factsReadinessState === "ready" ? <ShieldCheck size={14} /> : factsReadinessState === "loading" ? <LoaderCircle size={14} className="spin" /> : factsReadinessState === "error" ? <AlertCircle size={14} /> : <CircleHelp size={14} />}</span>
          <span className="content-readiness-copy"><strong>可公开的当前事实</strong><span>{factsReadinessText}</span><small>创建任务后，系统还会校验所选事实是否与问题相关。</small></span>
          {eligibleFacts.length === 0 && !factsLoading && <button className="content-readiness-link" type="button" onClick={onOpenFactLibrary} disabled={!onOpenFactLibrary}>前往事实库 <ArrowUpRight size={13} /></button>}
        </li>
      </ul>
    </section>}

    <section className="knowledge-panel" aria-labelledby="knowledge-panel-title">
      <div className="knowledge-panel-heading">
        <div><span className="knowledge-panel-icon" aria-hidden="true"><BookOpen size={15} /></span><div><h2 id="knowledge-panel-title">写作边界参考</h2><p>外部指导用于核对政策与来源边界，不是企业事实。</p></div></div>
        <span className="knowledge-panel-count">{knowledgeLoading ? "读取中" : `${filteredKnowledgeEntries.length}/${knowledgeEntries.length} 条`}</span>
      </div>
      {knowledgeError && <div className="content-inline-note warning" role="alert"><AlertCircle size={14} /><span>知识参考读取失败：{knowledgeError}</span></div>}
      {!knowledgeLoading && !knowledgeError && <>
      <label className="knowledge-search"><Search size={14} aria-hidden="true" /><input type="search" aria-label="筛选知识来源、主题或市场" placeholder="筛选来源、主题或市场" value={knowledgeSearch} onChange={(event) => setKnowledgeSearch(event.target.value)} /></label>
      <div className="knowledge-entry-list">{filteredKnowledgeEntries.map((entry) => <details className="knowledge-entry" key={entry.id}>
        <summary><strong>{entry.title}</strong><span>{knowledgeSourceDateLabel(entry.source_date)}</span></summary>
        <p>{entry.summary}</p>
        <div className="knowledge-entry-scope"><strong>知识类型</strong><span>{entry.entry_type === "external_guidance" ? "外部指导（非企业事实）" : entry.entry_type}</span></div>
        <div className="knowledge-entry-scope"><strong>来源类型</strong><span>{knowledgeSourceKindLabel(entry.source_kind)}</span></div>
        <div className="knowledge-entry-scope"><strong>市场范围</strong><span>{entry.market_scope}</span></div>
        <div className="knowledge-entry-scope"><strong>适用范围</strong><span>{entry.scope}</span></div>
        <div className="knowledge-entry-tags">{entry.topic_tags.split(",").filter(Boolean).map((tag) => <span key={tag}>{tag.trim()}</span>)}</div>
        <div className="knowledge-entry-source"><span>{entry.license} · 最近核验：{entry.last_verified_at} · {knowledgeFreshnessLabel(entry.freshness_policy)}</span>{entry.source_url && <a href={entry.source_url} target="_blank" rel="noreferrer">打开来源 <ArrowUpRight size={12} /></a>}</div>
      </details>)}</div>
      {filteredKnowledgeEntries.length === 0 && <div className="knowledge-empty">没有匹配的来源</div>}
      </>}
    </section>

    {!sitesLoading && !sitesError && sites.length > 0 && <div className="content-review-grid">
      <section className="content-builder" aria-label="创建内容任务">
        <div className="content-section-heading"><div><span className="section-kicker">01</span><div><h2>选择采购问题</h2><p>只使用已冻结版本与显式页面映射。</p></div></div></div>
        <div className="content-source-selects">
          <label className="field-label">问题集<select value={querySetId ?? ""} onChange={(event) => {
            const nextQuerySetId = event.target.value ? Number(event.target.value) : null;
            if (nextQuerySetId === querySetId || !confirmDiscardTaskDraft("切换问题集")) return;
            setQuerySetId(nextQuerySetId);
            setSelectedPages({});
            setDraftItems([]);
            setNotice("");
          }} disabled={questionSetsLoading || busyAction !== "" || questionSets.length === 0}>
            <option value="">{questionSetsLoading ? "正在读取问题集" : questionSets.length ? "选择问题集" : "暂无问题集"}</option>
            {questionSets.map((set) => <option key={set.id} value={set.id}>{set.name} · v{set.current_version}{set.current_state === "frozen" ? " · 已冻结" : " · 当前为草稿"}</option>)}
          </select></label>
          {querySetVersion && <label className="field-label">冻结版本<select value={querySetVersion.version} onChange={(event) => {
            const nextVersion = frozenVersions.find((version) => version.version === Number(event.target.value)) || null;
            if (!nextVersion || nextVersion.version === querySetVersion.version || !confirmDiscardTaskDraft("切换冻结版本")) return;
            setQuerySetVersion(nextVersion);
            setSelectedPages({});
            setDraftItems([]);
            setNotice("");
          }} disabled={querySetVersionLoading || busyAction !== ""}>{frozenVersions.map((version) => <option key={version.version} value={version.version}>v{version.version} · 已冻结</option>)}</select></label>}
        </div>
        {questionSetsError && <div className="alert alert-error compact"><AlertCircle size={14} /><span>{questionSetsError}</span></div>}
        {querySetVersionLoading && <div className="content-loading"><span className="skeleton" /><span className="skeleton" /></div>}
        {querySetVersionError && !querySetVersionLoading && <div className="content-inline-note warning" role="status"><CircleHelp size={14} /><span>{querySetVersionError}</span></div>}
        {pagesError && <div className="content-inline-note warning" role="alert"><AlertCircle size={14} /><span>读取页面快照失败：{pagesError}</span></div>}
        {factsError && <div className="content-inline-note warning" role="alert"><AlertCircle size={14} /><span>读取企业事实失败：{factsError}</span></div>}

        {querySetVersion && <>
          {querySetVersion.state !== "frozen" && <div className="content-inline-note warning"><ShieldCheck size={14} /><span>当前问题集版本未冻结，不能用于创建内容任务。</span></div>}
          <div className="content-question-list">
            {querySetVersion.questions.map((question) => {
              const choices = pageChoices(question);
              const selectedPageId = selectedPages[question.id] || choices[0]?.mapping.page_id || "";
              return <article className="content-question-source" key={question.id}>
                <div className="content-question-prompt"><span className="content-question-num">{String(question.position).padStart(2, "0")}</span><strong>{question.question}</strong></div>
                <div className="content-question-context"><span>{question.product}</span><span>{question.use_case}</span><span>{question.buyer_role}</span><span>{question.target_market} · {question.language}</span></div>
                <div className="content-question-map-row">
                  <label className="content-page-select"><span>关联页面</span><select value={selectedPageId} onChange={(event) => setSelectedPages((current) => ({ ...current, [question.id]: Number(event.target.value) }))} disabled={choices.length === 0 || busyAction !== ""}>
                    {question.page_mappings.length === 0 && <option value="">没有页面映射</option>}
                    {question.page_mappings.map((mapping) => {
                      const snapshot = pagesById.get(mapping.page_id);
                      const available = Boolean(snapshot && snapshotId(snapshot) && snapshot.content_hash);
                      return <option key={mapping.page_id} value={mapping.page_id} disabled={!available}>{mapping.canonical_url}{available ? "" : " · 无可用快照"}</option>;
                    })}
                  </select></label>
                  <button className="button button-secondary small" type="button" onClick={() => addDraftItem(question)} disabled={!choices.length || draftItems.length >= 5 || busyAction !== ""}><Plus size={13} />加入任务</button>
                </div>
                {!choices.length && <div className="content-no-snapshot">映射页面没有当前快照。刷新页面列表，或先在“站点与审计”运行采集。</div>}
              </article>;
            })}
          </div>

          <form className="content-task-form" onSubmit={(event) => void handleCreateTask(event)}>
            <div className="content-section-heading"><div><span className="section-kicker">02</span><div><h2>设置本次任务</h2><p>{draftItems.length} / 5 个问题与页面组合</p></div></div><button className="button button-secondary small" type="button" onClick={() => setDraftItems([])} disabled={!draftItems.length || busyAction !== ""}>清空</button></div>
            {draftItems.length === 0 ? <div className="content-task-empty"><FileText size={17} /><span>从上方选择问题与页面加入任务。</span></div> : <div className="content-task-items">
              {draftItems.map((item, index) => <article className="content-task-item" key={item.key}>
                <div className="content-task-item-head"><strong>{index + 1}. {item.question.question}</strong><button className="text-button danger-text" type="button" onClick={() => removeDraftItem(item.key)} disabled={busyAction !== ""}>移除</button></div>
                <div className="content-binding"><span>目标页面</span><strong>{item.snapshot.title || item.snapshot.url}</strong><small>{item.snapshot.url}</small><code>快照 #{snapshotId(item.snapshot)} · {item.snapshot.content_hash}</code></div>
                <label className="field-label">本次改稿要求<textarea value={item.requestSummary} onChange={(event) => updateDraftItem(item.key, (current) => ({ ...current, requestSummary: event.target.value }))} maxLength={800} rows={2} placeholder="说明需要补充或修订的内容" disabled={busyAction !== ""} /><span className="field-hint">最多 800 字；任务会在后台运行，结果需要人工审核，不会自动发布。</span></label>
                <div className="content-facts-heading"><strong>选择事实依据</strong><span>{item.requiredFactIds.length} 项已选</span></div>
                {factsLoading ? <div className="content-fact-loading"><span className="skeleton" /></div> : eligibleFacts.length === 0 ? <div className="content-no-facts">没有当前有效的“已确认、可公开”事实。请先在站点与审计页导入并确认事实。</div> : <>
                  {eligibleFacts.length > 5 && <label className="content-fact-search"><input value={factSearch} onChange={(event) => setFactSearch(event.target.value)} placeholder="筛选事实、产品或来源" aria-label="筛选可用事实" /><span>{filteredFacts.length} / {eligibleFacts.length}</span></label>}
                  <div className="content-fact-options">{filteredFacts.map((fact) => {
                    const checked = item.requiredFactIds.includes(fact.id);
                    const href = factLocatorHref(fact.source_locator);
                    return <label className={`content-fact-option ${checked ? "checked" : ""}`} key={fact.id}>
                      <input type="checkbox" checked={checked} disabled={busyAction !== ""} onChange={(event) => updateDraftItem(item.key, (current) => ({ ...current, requiredFactIds: event.target.checked ? [...current.requiredFactIds, fact.id] : current.requiredFactIds.filter((id) => id !== fact.id) }))} />
                      <span className="content-fact-copy"><strong>{fact.subject} · {fact.predicate}</strong><span>{fact.value}{fact.unit ? ` ${fact.unit}` : ""}</span><small>{fact.source_id} · v{fact.version} · {asDate(fact.valid_from)}</small></span>
                      {href && <a href={href} target="_blank" rel="noreferrer" title="打开事实来源" aria-label={`打开事实 ${fact.id} 的来源`} onClick={(event) => event.stopPropagation()}><ArrowUpRight size={13} /></a>}
                    </label>;
                  })}{filteredFacts.length === 0 && <span className="content-no-facts">没有匹配的当前有效事实。</span>}</div>
                </>}
              </article>)}
            </div>}
            {notice && <div className="content-inline-note" role="status"><Check size={14} /><span>{notice}</span></div>}
            {draftItems.length > 0 && createIssue && <div className="content-inline-note warning" role="status"><CircleHelp size={14} /><span>{createIssue}</span></div>}
            <div className="content-submit-row"><span>每个内容任务最多 5 项；只提交事实 ID 与快照绑定信息。</span><button className="button button-primary" type="submit" disabled={!siteId || Boolean(createIssue) || busyAction !== "" || querySetVersion?.state !== "frozen"}>{busyAction === "create-task" ? <LoaderCircle size={15} className="spin" /> : <Send size={15} />}{busyAction === "create-task" ? "创建中" : "创建内容任务"}</button></div>
          </form>
        </>}
      </section>

      <aside className="content-task-panel" aria-label="内容任务与人工审核">
        <div className="content-section-heading"><div><span className="section-kicker">03</span><div><h2>任务与审核</h2><p>{tasksLoading ? "正在读取" : `${taskSummaries.length} 个任务`}</p></div></div><button className="icon-button" type="button" onClick={() => { void refreshTasks(); if (taskDetail) void refreshTaskDetail(); }} disabled={tasksLoading || taskDetailLoading} title="刷新任务状态" aria-label="刷新任务状态"><RefreshCw size={14} className={tasksLoading || taskDetailLoading ? "spin" : ""} /></button></div>
        {tasksError && <div className="content-inline-note warning" role="alert"><AlertCircle size={14} /><span>{tasksError}</span></div>}
        {tasksLoading ? <div className="content-loading"><span className="skeleton" /><span className="skeleton" /></div> : taskSummaries.length === 0 ? <div className="content-task-empty"><FileText size={17} /><span>当前站点还没有内容任务。</span></div> : <div className="content-task-list">
          {taskSummaries.map((task) => <button className={`content-task-summary ${taskDetail?.id === task.id ? "selected" : ""}`} type="button" key={task.id} onClick={() => void loadTask(task.id)} disabled={taskDetailLoading}>
            <span><strong>任务 #{task.id}</strong><small>{asDate(task.created_at)}</small></span><span className={`content-status ${statusClass(task.status)}`}>{taskStatus(task.status)}</span>
          </button>)}
        </div>}
        {taskDetailError && <div className="content-inline-note warning" role="alert"><AlertCircle size={14} /><span>{taskDetailError}</span></div>}
        {taskDetailLoading && <div className="content-loading"><span className="skeleton" /><span className="skeleton" /></div>}
        {!taskDetailLoading && taskDetail && <div className="content-task-detail">
          <div className="content-detail-heading"><div><strong>任务 #{taskDetail.id}</strong><span className={`content-status ${statusClass(taskDetail.status)}`}>{taskStatus(taskDetail.status)}</span></div><button className="icon-button" type="button" title="重新读取任务" aria-label="重新读取任务" onClick={() => void refreshTaskDetail()}><RefreshCw size={13} /></button></div>
          {taskDetail.last_error && <div className="content-last-error">{taskDetail.last_error}</div>}
          <label className="reviewer-field content-reviewer">审核身份（生产环境由已验证身份决定）<input value={reviewer} onChange={(event) => setReviewer(event.target.value)} maxLength={200} placeholder="演示模式可填写标识" /><small>服务端会从 OIDC / 工作区 Membership 解析 reviewer；此字段不能提升权限。</small></label>
          <div className="content-result-items">{taskDetail.items.map((item) => <ReviewItem
            key={item.id}
            item={item}
            currentSnapshot={pagesById.get(item.page_id)}
            facts={facts}
            busyAction={busyAction}
            comment={approvalComments[item.id] || ""}
            editing={Boolean(revisionEditors[item.id])}
            editDraft={revisionDrafts[item.id] || ""}
            editError={revisionEditErrors[item.id] || ""}
            onComment={(value) => setApprovalComments((current) => ({ ...current, [item.id]: value }))}
            onOpenSource={(snapshotId) => void openSourceSnapshot(snapshotId)}
            onStartEdit={() => startRevisionEdit(item)}
            onCancelEdit={() => cancelRevisionEdit(item.id)}
            onEditDraft={(value) => setRevisionDrafts((current) => ({ ...current, [item.id]: value }))}
            onSaveEdit={() => void saveRevisionEdit(item)}
            onSubmit={() => void handleSubmitForApproval(item)}
            onDecide={(decision) => void handleApproval(item, decision)}
          />)}</div>
        </div>}
      </aside>
    </div>}

    {sourceSnapshotId !== null && <div className="modal-backdrop evidence-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSourceSnapshotId(null); }}>
      <section className="evidence-dialog content-source-dialog" role="dialog" aria-modal="true" aria-labelledby="content-source-title" aria-busy={sourceSnapshotLoading}>
        <div className="evidence-heading"><div><span className="dialog-icon"><FileCheck2 size={18} /></span><div><h2 id="content-source-title">原页面证据</h2><p>{sourceSnapshot?.url || `快照 #${sourceSnapshotId}`}</p></div></div><button className="icon-button" type="button" onClick={() => setSourceSnapshotId(null)} aria-label="关闭原页面证据"><X size={17} /></button></div>
        <div className="evidence-body">
          {sourceSnapshotLoading ? <SnapshotLoading /> : sourceSnapshotError ? <InlineRetry message={sourceSnapshotError} onRetry={() => void openSourceSnapshot(sourceSnapshotId)} /> : sourceSnapshot ? <SnapshotEvidence detail={sourceSnapshot} /> : null}
        </div>
      </section>
    </div>}
  </div>;
}

function ReviewItem({
  item,
  currentSnapshot,
  facts,
  busyAction,
  comment,
  editing,
  editDraft,
  editError,
  onComment,
  onOpenSource,
  onStartEdit,
  onCancelEdit,
  onEditDraft,
  onSaveEdit,
  onSubmit,
  onDecide,
}: {
  item: ContentGenerationItem;
  currentSnapshot?: PageSnapshot;
  facts: Fact[];
  busyAction: string;
  comment: string;
  editing: boolean;
  editDraft: string;
  editError: string;
  onComment: (value: string) => void;
  onOpenSource: (snapshotId: number) => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onEditDraft: (value: string) => void;
  onSaveEdit: () => void;
  onSubmit: () => void;
  onDecide: (decision: "approved" | "rejected") => void;
}) {
  const change = item.change_request;
  const revision = change.revision;
  const terminalApprovalState = ["approved", "rejected"].includes(change.state) ? change.state : null;
  const displayStatus = terminalApprovalState || item.status;
  const approvedFactVersions = revision?.fact_versions || [];
  const factsById = new Map(facts.map((fact) => [fact.id, fact]));
  const showFacts = approvedFactVersions.map((reference) => {
    const fact = factsById.get(reference.fact_id);
    return { reference, fact: fact && factBindingIsCurrent(reference, fact) ? fact : null };
  });
  const staleSnapshot = revision?.base_snapshot_id !== item.snapshot_id
    || revision?.base_content_hash !== item.snapshot_hash
    || (currentSnapshot && (snapshotId(currentSnapshot) !== item.snapshot_id || currentSnapshot.content_hash !== item.snapshot_hash));
  const claims = revision ? revisionClaims(revision.field_diff) : [];
  const missingInformation = revision ? revisionMissingInformation(revision.field_diff) : [];
  const unsupportedClaims = claims.filter((claim) => {
    const fact = factsById.get(Number(claim.fact_id));
    return !fact || !factBindingIsCurrent(claim, fact);
  });
  const canSubmit = item.status === "awaiting_review" && change.state === "draft" && Boolean(revision);
  const canDecide = item.status === "awaiting_review" && change.state === "pending_approval" && Boolean(revision);

  return <article className="content-review-item">
    <div className="content-review-item-head"><div><span className={`content-status ${statusClass(displayStatus)}`}>{taskStatus(displayStatus)}</span><strong>{item.question}</strong></div><small>{item.canonical_url}</small></div>
    <div className="content-review-binding"><span>原始页面快照</span><code>#{item.snapshot_id} · {item.snapshot_hash}</code><button type="button" className="text-button content-source-button" onClick={() => onOpenSource(item.snapshot_id)}><FileCheck2 size={12} />查看证据</button><span>工作流 thread_id</span><code>{item.thread_id}</code></div>
    <p className="content-review-summary">{item.request_summary}</p>
    <div className="content-evidence-strip" aria-label="证据状态">
      <span className="content-evidence-chip"><ShieldCheck size={12} />{showFacts.length} 条事实绑定</span>
      <span className={`content-evidence-chip ${staleSnapshot ? "stale" : "good"}`}><FileCheck2 size={12} />{staleSnapshot ? "页面快照已变化" : "页面快照一致"}</span>
      <span className={`content-evidence-chip ${unsupportedClaims.length ? "stale" : claims.length ? "good" : "muted"}`}><Info size={12} />{claims.length ? `${claims.length} 条声明` : "声明定位未提供"}</span>
    </div>
    {item.status === "needs_information" && <div className="content-inline-note warning"><CircleHelp size={14} /><span>缺少可用信息或事实已失效。请刷新任务与企业事实，核对来源后再处理。</span></div>}
    {item.status === "failed" && <div className="content-inline-note warning"><AlertCircle size={14} /><span>本项处理失败；检查任务错误信息并刷新状态。</span></div>}
    {staleSnapshot && <div className="content-inline-note warning"><AlertCircle size={14} /><span>改稿基于旧页面快照，不能按当前证据直接批准；请刷新任务或重新生成。</span></div>}
    {missingInformation.length > 0 && <div className="content-inline-note warning"><CircleHelp size={14} /><span>待补资料：{missingInformation.join("；")}</span></div>}
    {revision && <>
      <div className="content-review-subheading"><strong>待审字段</strong><span>版本 {revision.revision} · {revision.state} · schema {revision.schema_version || "未记录"}</span></div>
      {editing ? <div className="content-edit-panel"><label className="field-label">编辑当前修订（JSON 字段对象）<textarea value={editDraft} onChange={(event) => onEditDraft(event.target.value)} rows={8} disabled={busyAction !== ""} spellCheck={false} /><span className="field-hint">保存会创建新 revision，清除旧批准并要求重新提交审核。服务端会重新校验事实、页面快照和字段白名单。</span></label>{editError && <div className="content-inline-note warning" role="alert"><AlertCircle size={14} />{editError}</div>}<div className="content-review-edit-actions"><button type="button" className="button button-secondary small" onClick={onCancelEdit} disabled={busyAction !== ""}><X size={13} />取消</button><button type="button" className="button button-primary small" onClick={onSaveEdit} disabled={busyAction !== ""}>{busyAction === `edit-${item.id}` ? <LoaderCircle size={13} className="spin" /> : <Check size={13} />}保存新修订</button></div></div> : <div className="content-diff-list">{revisionFieldEntries(revision.field_diff || {}).map(([field, value]) => <div className="content-diff-row" key={field}><strong>{field}</strong><pre>{fieldValue(value)}</pre></div>)}{revisionFieldEntries(revision.field_diff || {}).length === 0 && <span className="content-no-facts">该版本没有字段改动。</span>}</div>}
      {!editing && <button type="button" className="text-button content-edit-button" onClick={onStartEdit} disabled={busyAction !== "" || terminalApprovalState === "approved"}><Edit3 size={12} />编辑并生成新修订</button>}
      {claims.length > 0 && <><div className="content-review-subheading"><strong>声明与证据绑定</strong><span>{claims.length} 条</span></div><div className="content-claim-list">{claims.map((claim, index) => { const fact = factsById.get(Number(claim.fact_id)); const valid = Boolean(fact && factBindingIsCurrent(claim, fact)); return <div className={`content-claim-row ${valid ? "valid" : "stale"}`} key={`${String(claim.claim_id || index)}:${factBindingKey(claim)}`}><span><strong>{String(claim.claim_id || `声明 ${index + 1}`)}</strong><small>{String(claim.field_path || "未提供字段路径")} · fact #{String(claim.fact_id || "?")} · v{String(claim.fact_version || claim.version || "?")}</small></span><span>{valid ? "已绑定当前事实" : "事实缺失或已失效"}</span></div>; })}</div></>}
      <div className="content-review-subheading"><strong>引用事实</strong><span>{showFacts.length} 项</span></div>
      {showFacts.length === 0 ? <span className="content-no-facts">没有记录事实版本。</span> : <div className="content-review-facts">{showFacts.map(({ reference, fact }) => {
        const href = fact ? factLocatorHref(fact.source_locator) : "";
        return <div className="content-review-fact" key={`${reference.fact_id}:${reference.version}`}>
          <span><strong>{fact ? `${fact.subject} · ${fact.predicate}` : `企业事实 #${reference.fact_id}`}</strong><small>v{reference.version}{fact && fact.version !== reference.version ? ` · 当前 v${fact.version}` : ""}{!fact ? " · 已不再满足公开有效条件" : ""}</small></span>
          {href ? <a href={href} target="_blank" rel="noreferrer" title="打开事实来源" aria-label={`打开事实 ${reference.fact_id} 的来源`}><ArrowUpRight size={13} /></a> : <span className="content-source-unavailable" title="当前 API 未提供可打开的 URL"><Info size={12} />来源定位</span>}
        </div>;
      })}</div>}
      <div className="content-revision-hash"><span>改稿 Hash</span><code>{revision.content_hash}</code></div>
    </>}
    {change.approvals.length > 0 && <div className="content-approval-history"><strong>审核记录</strong>{change.approvals.map((approval) => <div key={approval.id}><span className={`content-status ${statusClass(approval.decision)}`}>{approval.decision === "approved" ? "已通过" : "已退回"}</span><span>{approval.reviewer} · {asDate(approval.created_at)}</span>{approval.comment && <small>{approval.comment}</small>}</div>)}</div>}
    {canSubmit && <div className="content-review-actions"><p>检查页面快照、建议字段和事实来源后，提交至人工审核队列。{staleSnapshot ? "当前快照已变化，需刷新后再提交。" : ""}</p><button className="button button-primary small" type="button" onClick={onSubmit} disabled={busyAction !== "" || staleSnapshot || unsupportedClaims.length > 0}>{busyAction === `submit-${item.id}` ? <LoaderCircle size={13} className="spin" /> : <Send size={13} />}提交审核</button></div>}
    {canDecide && <div className="content-review-decision"><label className="field-label">审核意见<textarea value={comment} onChange={(event) => onComment(event.target.value)} rows={2} maxLength={2000} placeholder="可选；退回时说明修改原因" disabled={busyAction !== ""} /></label><div><button className="button button-secondary small" type="button" onClick={() => onDecide("rejected")} disabled={busyAction !== ""}>{busyAction === `rejected-${item.id}` ? <LoaderCircle size={13} className="spin" /> : null}退回修改</button><button className="button button-primary small" type="button" onClick={() => onDecide("approved")} disabled={busyAction !== "" || staleSnapshot || unsupportedClaims.length > 0}>{busyAction === `approved-${item.id}` ? <LoaderCircle size={13} className="spin" /> : <Check size={13} />}通过</button></div>{(staleSnapshot || unsupportedClaims.length > 0) && <p className="content-review-blocker">审批已禁用：{staleSnapshot ? "页面快照已变化" : "声明未绑定当前事实"}。</p>}</div>}
    {!canSubmit && !canDecide && item.status === "awaiting_review" && !terminalApprovalState && <div className="content-inline-note"><ShieldCheck size={14} /><span>{changeStatus(change.state)}。请刷新任务状态以获取最新审核记录。</span></div>}
    {item.status !== "awaiting_review" && !["needs_information", "failed"].includes(item.status) && !revision && <div className="content-inline-note"><LoaderCircle size={14} className={item.status === "running" || item.status === "queued" ? "spin" : ""} /><span>后台任务尚未返回可审核改稿。刷新任务状态查看进度。</span></div>}
  </article>;
}

function SnapshotEvidence({ detail }: { detail: SnapshotDetail }) {
  const body = snapshotContent(detail);
  const attentionRules = (detail.rule_results || []).filter((rule) => ["fail", "unknown", "needs_review"].includes(rule.status));
  return <div className="content-source-evidence">
    <div className="content-source-meta"><span>状态 <strong>{detail.status_code ?? "--"}</strong></span><span>采集时间 <strong>{asDate(detail.fetched_at)}</strong></span><span>规则集 <strong>{detail.rule_set_version || "--"}</strong></span><span className={`source-badge ${detail.is_synthetic ? "source-fixture" : "source-live"}`}><span />{detail.is_synthetic ? "合成演示数据" : "真实采集数据"}</span></div>
    <div className="content-source-hashes"><div><span>原始内容 hash</span><code>{detail.content_hash || "--"}</code></div><div><span>metadata hash</span><code>{detail.metadata_hash || "--"}</code></div><div><span>body hash</span><code>{detail.body_hash || "--"}</code></div><div><span>parser</span><code>{detail.parser_version || "未记录"}</code></div></div>
    <div className="content-source-url-list"><span>请求地址</span><code>{detail.requested_url || detail.url || "--"}</code><span>最终地址</span><code>{detail.final_url || detail.url || "--"}</code></div>
    <div className="content-source-canonical"><strong>Canonical 证据</strong><span>声明：{detail.declared_canonical?.length ? detail.declared_canonical.join(" · ") : "未发现"}</span><span>规范化：{detail.normalized_canonical?.length ? detail.normalized_canonical.map((value) => value || "无效").join(" · ") : "未记录"}</span></div>
    {attentionRules.length > 0 ? <details className="content-source-rules" open><summary>需关注规则 · {attentionRules.length}</summary>{attentionRules.map((rule) => <div className="content-source-rule" key={`${rule.rule_id}:${rule.version}`}><span className={`content-status ${rule.status === "fail" ? "bad" : "working"}`}>{rule.status === "fail" ? "问题" : rule.status === "needs_review" ? "待复核" : "未确认"}</span><strong>{rule.rule_id}</strong><p>{rule.message}</p><pre>{formatEvidenceValue(rule.evidence)}</pre></div>)}</details> : <div className="content-inline-note"><Check size={14} /><span>当前快照没有 fail、unknown 或 needs_review 规则结果。</span></div>}
    <details className="content-source-body"><summary>原文/正文片段</summary>{body ? <pre>{body.slice(0, 12000)}</pre> : <div className="content-inline-note warning"><Info size={14} /><span>当前 API 只返回 immutable 快照元数据与规则证据，未提供正文片段；不能把“已采集”当作正文证据。</span></div>}</details>
  </div>;
}

function SnapshotLoading() {
  return <div className="content-loading" aria-label="证据加载中"><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div>;
}

function InlineRetry({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="content-inline-note warning" role="alert"><AlertCircle size={14} /><span>{message}</span><button type="button" className="button button-secondary small" onClick={onRetry}><RefreshCw size={13} />重试</button></div>;
}

function formatEvidenceValue(value: unknown) {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "--";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
