import { ChangeEvent, FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowDownToLine,
  ArrowUpRight,
  ChevronRight,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  Eye,
  FileSearch,
  FileSpreadsheet,
  Globe2,
  LoaderCircle,
  Plus,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  SquareArrowOutUpRight,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import {
  AuditRun,
  confirmFact,
  createAudit,
  createSite,
  Fact,
  getAuditRun,
  getHealth,
  getSnapshot,
  Health,
  listPages,
  listFacts,
  listSites,
  PageSnapshot,
  importFact,
  importFactsCsv,
  rejectFact,
  RuleResult,
  SnapshotDetail,
  Site,
  Session,
  WorkspaceMembership,
  clearApiContext,
  getSession,
  listWorkspaces,
  setApiContext,
} from "./api";
import { ProcurementWorkspace } from "./ProcurementWorkspace";
import { ContentReviewWorkspace } from "./ContentReviewWorkspace";
import { VisibilityWorkspace } from "./VisibilityWorkspace";

const WORKSPACE_ID = import.meta.env.VITE_WORKSPACE_ID || "demo-workspace";

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : "发生未知错误";
}

function parseSiteAddress(value: string) {
  const trimmed = value.trim();
  const withProtocol = /^[a-z][a-z\d+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  let parsed: URL;
  try {
    parsed = new URL(withProtocol);
  } catch {
    throw new Error("请输入有效的网址，例如 acme.com 或 https://acme.com");
  }
  if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("请使用 http 或 https 地址。");
  if (parsed.username || parsed.password) throw new Error("请移除网址中的登录凭据。");
  if (parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("请输入站点根地址；需要限制目录时请使用高级设置中的允许路径。");
  }
  return { origin: parsed.origin, suggestedName: parsed.hostname.replace(/^www\./i, "") };
}

function siteNameFromAddress(value: string) {
  try {
    return parseSiteAddress(value).suggestedName;
  } catch {
    return "";
  }
}

function asDate(value?: string) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function factStatusText(status: string) {
  const labels: Record<string, string> = {
    proposed: "待确认",
    confirmed: "已确认",
    expired: "已过期",
    rejected: "已拒绝",
  };
  return labels[status] || status;
}

function factIsCurrent(fact: Fact, now = Date.now()) {
  if (fact.status !== "confirmed") return false;
  const from = new Date(fact.valid_from).getTime();
  const until = fact.valid_until ? new Date(fact.valid_until).getTime() : Number.POSITIVE_INFINITY;
  return Number.isFinite(from) && from <= now && until > now;
}

function factCanUsePublicly(fact: Fact, now = Date.now()) {
  return fact.visibility === "public" && factIsCurrent(fact, now);
}

function factLocatorHref(locator: string) {
  return /^https?:\/\//i.test(locator) ? locator : "";
}

function statusTone(status?: string) {
  const value = (status || "").toLowerCase();
  if (["completed", "complete", "succeeded", "success", "done", "pass"].includes(value)) return "good";
  if (["failed", "error", "cancelled"].includes(value)) return "bad";
  if (["running", "queued", "pending", "started"].includes(value)) return "working";
  return "muted";
}

function statusText(status?: string) {
  const labels: Record<string, string> = {
    completed: "已完成",
    complete: "已完成",
    succeeded: "已完成",
    success: "已完成",
    done: "已完成",
    running: "执行中",
    queued: "排队中",
    pending: "排队中",
    started: "执行中",
    failed: "失败",
    error: "失败",
    cancelled: "已取消",
  };
  return labels[(status || "").toLowerCase()] || status || "未知";
}

function isActive(status?: string) {
  return ["running", "queued", "pending", "started"].includes((status || "").toLowerCase());
}

function getRunProgress(run: AuditRun) {
  if (typeof run.progress === "number") return Math.min(100, Math.max(0, run.progress));
  if (run.pages_total && typeof run.pages_completed === "number") {
    return Math.round((run.pages_completed / run.pages_total) * 100);
  }
  return statusTone(run.status) === "good" ? 100 : 0;
}

function ruleStatusText(status: string) {
  const labels: Record<string, string> = {
    pass: "通过",
    fail: "问题",
    unknown: "未确认",
    not_applicable: "不适用",
    needs_review: "待复核",
  };
  return labels[status] || status;
}

function ruleStatusTone(status: string) {
  if (status === "pass") return "good";
  if (status === "fail") return "bad";
  if (status === "needs_review") return "review";
  return "muted";
}

const RULE_COPY: Record<string, { title: string; guidance: string }> = {
  http_status_redirect: { title: "响应状态与跳转", guidance: "确认页面返回预期状态，并检查跳转链是否指向正确的最终地址。" },
  robots_access: { title: "抓取权限（robots）", guidance: "检查 robots.txt 与页面抓取策略，确保目标页面允许预期的抓取访问。" },
  index_directives: { title: "索引指令", guidance: "统一 robots meta 与 X-Robots-Tag 的索引设置，避免误阻止收录。" },
  sitemap_validity: { title: "站点地图", guidance: "发布有效的同源 sitemap，并包含需要公开的规范 URL。" },
  canonical_target: { title: "规范链接", guidance: "为页面设置唯一且有效的 canonical，并确认它指向首选域名。" },
  title_present: { title: "页面标题", guidance: "补充简洁、能区分页面内容的 HTML title。" },
  duplicate_titles: { title: "标题唯一性", guidance: "检查重复标题，让每个页面标题能准确说明对应内容。" },
  meta_description: { title: "摘要描述", guidance: "为页面补充与内容匹配的 meta description，便于业务人员检查摘要。" },
  heading_structure: { title: "主标题结构", guidance: "检查页面主标题层级；这是可读性提示，不直接代表排名惩罚。" },
  internal_links: { title: "站内链接", guidance: "修复确认失效的站内链接；网络暂时无法确认的链接请稍后复核。" },
  image_alt: { title: "图片替代文本", guidance: "为有意义的图片补充 alt；装饰图片可使用空 alt。" },
  jsonld_consistency: { title: "结构化数据", guidance: "修正无效或不完整的 JSON-LD，并对照页面可见内容人工确认。" },
};

function ruleCopy(ruleId: string) {
  return RULE_COPY[ruleId] || { title: ruleId, guidance: "请结合下方证据字段复核此规则。" };
}

type RuleSummary = {
  total: number;
  problem: number;
  review: number;
  unknown: number;
  pass: number;
  attention: number;
};

function countValue(value?: number) {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
}

function summarizeSnapshotRules(snapshot: {
  finding_count?: number;
  rule_count?: number;
  rule_problem_count?: number;
  rule_review_count?: number;
  rule_unknown_count?: number;
  rule_results?: RuleResult[];
}): RuleSummary {
  const results = snapshot.rule_results || [];
  if (results.length > 0) {
    const problem = results.filter((rule) => rule.status === "fail").length;
    const review = results.filter((rule) => rule.status === "needs_review").length;
    const unknown = results.filter((rule) => rule.status === "unknown").length;
    const pass = results.filter((rule) => rule.status === "pass").length;
    return { total: results.length, problem, review, unknown, pass, attention: problem + review + unknown };
  }

  // `finding_count` predates the rule counters and represents the same fail bucket.
  // Taking the maximum keeps old and new API fields from double-counting problems.
  const problem = Math.max(countValue(snapshot.finding_count), countValue(snapshot.rule_problem_count));
  const review = countValue(snapshot.rule_review_count);
  const unknown = countValue(snapshot.rule_unknown_count);
  const total = Math.max(countValue(snapshot.rule_count), problem + review + unknown);
  return { total, problem, review, unknown, pass: Math.max(0, total - problem - review - unknown), attention: problem + review + unknown };
}

function attentionLabel(summary: RuleSummary) {
  if (!summary.attention) return "当前没有需关注项";
  const parts = [`${summary.attention} 个需关注`];
  if (summary.review) parts.push(`${summary.review} 待复核`);
  if (summary.unknown) parts.push(`${summary.unknown} 未确认`);
  return parts.join(" · ");
}

export function App() {
  const [workspaceKey, setWorkspaceKey] = useState(WORKSPACE_ID);
  const [session, setSession] = useState<Session | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceMembership[]>([]);
  const [identityInput, setIdentityInput] = useState(import.meta.env.VITE_LOCAL_USER || "");
  const [identityError, setIdentityError] = useState("");
  const [identityBusy, setIdentityBusy] = useState(false);
  const [activeView, setActiveView] = useState<"sites" | "procurement" | "content" | "visibility">("sites");
  const [workspaceDirty, setWorkspaceDirty] = useState(false);
  const [pendingView, setPendingView] = useState<"sites" | "procurement" | "content" | "visibility" | null>(null);
  const [scrollToFactLibrary, setScrollToFactLibrary] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState("");
  const [sites, setSites] = useState<Site[]>([]);
  const [sitesLoading, setSitesLoading] = useState(true);
  const [sitesError, setSitesError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [pages, setPages] = useState<PageSnapshot[]>([]);
  const [pagesLoading, setPagesLoading] = useState(false);
  const [pagesError, setPagesError] = useState("");
  const [auditRun, setAuditRun] = useState<AuditRun | null>(null);
  const [auditBusy, setAuditBusy] = useState(false);
  const [auditError, setAuditError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [siteName, setSiteName] = useState("");
  const [siteOrigin, setSiteOrigin] = useState("");
  const [allowedPaths, setAllowedPaths] = useState("/");
  const [siteAdvancedOpen, setSiteAdvancedOpen] = useState(false);
  const [isSynthetic, setIsSynthetic] = useState(false);
  const [formError, setFormError] = useState("");
  const [siteSaving, setSiteSaving] = useState(false);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  const [detailId, setDetailId] = useState("");
  const [detail, setDetail] = useState<SnapshotDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [detailReloadCount, setDetailReloadCount] = useState(0);
  const [detailRuleFilter, setDetailRuleFilter] = useState<"attention" | "all">("attention");
  const [facts, setFacts] = useState<Fact[]>([]);
  const [factsLoading, setFactsLoading] = useState(true);
  const [factsError, setFactsError] = useState("");
  const [factFormOpen, setFactFormOpen] = useState(false);
  const [factSubject, setFactSubject] = useState("");
  const [factPredicate, setFactPredicate] = useState("");
  const [factValue, setFactValue] = useState("");
  const [factUnit, setFactUnit] = useState("");
  const [factSourceId, setFactSourceId] = useState("manual");
  const [factSourceLocator, setFactSourceLocator] = useState("");
  const [factVisibility, setFactVisibility] = useState<Fact["visibility"]>("internal_only");
  const [factReviewer, setFactReviewer] = useState(import.meta.env.VITE_LOCAL_USER || "demo-user");
  const [factSaving, setFactSaving] = useState(false);
  const [factActionId, setFactActionId] = useState("");
  const [factNotice, setFactNotice] = useState("");
  const factCsvInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!pendingView) return;
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") cancelViewChange();
    };
    window.addEventListener("keydown", dismissOnEscape);
    return () => window.removeEventListener("keydown", dismissOnEscape);
  }, [pendingView]);

  const selectedSite = sites.find((site) => site.id === selectedId) || null;
  const suggestedSiteName = siteNameFromAddress(siteOrigin);
  const visiblePages = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return pages;
    return pages.filter((page) => `${page.url} ${page.title || ""}`.toLowerCase().includes(needle));
  }, [pages, query]);

  const refreshSites = useCallback(async (preserveSelection = true) => {
    setSitesLoading(true);
    setSitesError("");
    try {
      const result = await listSites(workspaceKey);
      setSites(result);
      setSelectedId((current) => {
        if (preserveSelection && result.some((site) => site.id === current)) return current;
        return result[0]?.id || "";
      });
    } catch (error) {
      setSitesError(messageOf(error));
    } finally {
      setSitesLoading(false);
    }
  }, [workspaceKey]);

  const refreshHealth = useCallback(async () => {
    setHealthError("");
    try {
      setHealth(await getHealth());
    } catch (error) {
      setHealth(null);
      setHealthError(messageOf(error));
    }
  }, []);

  const refreshFacts = useCallback(async () => {
    setFactsLoading(true);
    setFactsError("");
    try {
      setFacts(await listFacts(workspaceKey));
    } catch (error) {
      setFactsError(messageOf(error));
    } finally {
      setFactsLoading(false);
    }
  }, [workspaceKey]);

  const refreshSession = useCallback(async () => {
    setIdentityError("");
    try {
      const current = await getSession();
      const choices = await listWorkspaces();
      setSession(current);
      setWorkspaces(choices);
      setWorkspaceKey((active: string) => choices.some((item) => item.id === active) ? active : (choices[0]?.id || WORKSPACE_ID));
    } catch (error) {
      setIdentityError(messageOf(error));
      setSession(null);
      setWorkspaces([]);
    }
  }, []);

  async function applyIdentity() {
    setIdentityBusy(true);
    clearApiContext();
    setApiContext({ localUser: identityInput.trim() || undefined, workspaceKey });
    setSelectedId("");
    setSites([]);
    setPages([]);
    setFacts([]);
    setAuditRun(null);
    setDetailId("");
    try {
      await refreshSession();
      setFactReviewer(identityInput.trim() || "demo-user");
    } finally {
      setIdentityBusy(false);
    }
  }

  function changeWorkspace(next: string) {
    setWorkspaceKey(next);
    setApiContext({ workspaceKey: next });
    setSelectedId("");
    setSites([]);
    setFacts([]);
    setPages([]);
    setAuditRun(null);
    setDetailId("");
    setWorkspaceDirty(false);
  }

  useEffect(() => {
    setApiContext({ workspaceKey });
    void refreshSession();
    void refreshHealth();
    void refreshSites(false);
    void refreshFacts();
  }, [refreshFacts, refreshHealth, refreshSites, refreshSession, workspaceKey]);

  const refreshPages = useCallback(async () => {
    if (!selectedId) {
      setPages([]);
      return;
    }
    setPagesLoading(true);
    setPagesError("");
    try {
      setPages(await listPages(selectedId, workspaceKey));
    } catch (error) {
      setPagesError(messageOf(error));
      setPages([]);
    } finally {
      setPagesLoading(false);
    }
  }, [selectedId, workspaceKey]);

  useEffect(() => {
    void refreshPages();
  }, [refreshPages]);

  useEffect(() => {
    if (!detailId) {
      setDetail(null);
      setDetailError("");
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError("");
    getSnapshot(detailId, workspaceKey)
      .then((result) => { if (!cancelled) setDetail(result); })
      .catch((error) => { if (!cancelled) setDetailError(messageOf(error)); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [detailId, detailReloadCount, workspaceKey]);

  useEffect(() => {
    if (!detailId) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDetailId("");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [detailId]);

  useEffect(() => {
    if (!auditRun || !isActive(auditRun.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const current = await getAuditRun(auditRun.id, workspaceKey);
        setAuditRun(current);
        if (!isActive(current.status)) {
          setAuditBusy(false);
          void refreshPages();
        }
      } catch (error) {
        setAuditError(messageOf(error));
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [auditRun, refreshPages, workspaceKey]);

  async function handleCreateSite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    let siteAddress: ReturnType<typeof parseSiteAddress>;
    try {
      siteAddress = parseSiteAddress(siteOrigin);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "站点地址无效");
      return;
    }
    const paths = [...new Set(allowedPaths.split(/\r?\n|,/).map((path) => path.trim()).filter(Boolean))];
    if (paths.length === 0 || paths.some((path) => !path.startsWith("/"))) {
      setSiteAdvancedOpen(true);
      setFormError("允许路径至少填写一个以 / 开头的路径，例如 / 或 /products。");
      return;
    }
    setSiteSaving(true);
    try {
      const created = await createSite({
        workspace_id: workspaceKey,
        name: siteName.trim() || siteAddress.suggestedName,
        origin: siteAddress.origin,
        allowed_paths: paths,
        is_synthetic: isSynthetic,
      });
      await refreshSites(false);
      setSelectedId(created.id);
      setSiteName("");
      setSiteOrigin("");
      setAllowedPaths("/");
      setSiteAdvancedOpen(false);
      setIsSynthetic(false);
      setFormOpen(false);
      setNotice("站点已登记");
      window.setTimeout(() => setNotice(""), 2800);
    } catch (error) {
      setFormError(messageOf(error));
    } finally {
      setSiteSaving(false);
    }
  }

  async function handleAudit() {
    if (!selectedSite) return;
    setAuditBusy(true);
    setAuditError("");
    setAuditRun(null);
    try {
      const run = await createAudit(selectedSite.id, workspaceKey);
      setAuditRun(run);
      if (!isActive(run.status)) {
        setAuditBusy(false);
        void refreshPages();
      }
    } catch (error) {
      setAuditError(messageOf(error));
      setAuditBusy(false);
    }
  }

  async function handleImportFact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFactsError("");
    setFactSaving(true);
    try {
      await importFact({
        workspace_id: workspaceKey,
        subject: factSubject.trim(),
        predicate: factPredicate.trim(),
        value: factValue.trim(),
        unit: factUnit.trim() || undefined,
        source_id: factSourceId.trim(),
        source_locator: factSourceLocator.trim(),
        visibility: factVisibility,
      });
      await refreshFacts();
      setFactSubject("");
      setFactPredicate("");
      setFactValue("");
      setFactUnit("");
      setFactSourceId("manual");
      setFactSourceLocator("");
      setFactVisibility("internal_only");
      setFactFormOpen(false);
      setFactNotice("事实已导入，等待确认");
      window.setTimeout(() => setFactNotice(""), 3200);
    } catch (error) {
      setFactsError(messageOf(error));
    } finally {
      setFactSaving(false);
    }
  }

  async function handleImportFactsCsv(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    setFactsError("");
    setFactSaving(true);
    try {
      const imported = await importFactsCsv(workspaceKey, file);
      await refreshFacts();
      setFactNotice(`已导入 ${imported.length} 条待确认事实`);
      window.setTimeout(() => setFactNotice(""), 3600);
    } catch (error) {
      setFactsError(messageOf(error));
    } finally {
      input.value = "";
      setFactSaving(false);
    }
  }

  async function handleFactReview(fact: Fact, action: "confirm" | "reject") {
    const reviewer = factReviewer.trim();
    if (!reviewer) {
      setFactsError("请填写审核人标识后再操作");
      return;
    }
    setFactsError("");
    setFactActionId(`${fact.id}:${action}`);
    try {
      if (action === "confirm") await confirmFact(fact.id, reviewer, fact.version, workspaceKey);
      else await rejectFact(fact.id, reviewer, fact.version, workspaceKey);
      await refreshFacts();
      setFactNotice(action === "confirm" ? "事实已确认。公开改稿还要求可公开且当前有效；确认不会更改可见性。" : "事实已拒绝");
      window.setTimeout(() => setFactNotice(""), 3600);
    } catch (error) {
      setFactsError(messageOf(error));
    } finally {
      setFactActionId("");
    }
  }

  const serviceOnline = Boolean(health && !healthError);
  const fixtureMode = health?.fixture === true || health?.mode === "fixture" || health?.mode === "demo";
  const pageSummary = useMemo(() => pages.reduce<RuleSummary>((summary, page) => {
    const current = summarizeSnapshotRules(page);
    return {
      total: summary.total + current.total,
      problem: summary.problem + current.problem,
      review: summary.review + current.review,
      unknown: summary.unknown + current.unknown,
      pass: summary.pass + current.pass,
      attention: summary.attention + current.attention,
    };
  }, { total: 0, problem: 0, review: 0, unknown: 0, pass: 0, attention: 0 }), [pages]);
  const issuePages = useMemo(() => pages.filter((page) => summarizeSnapshotRules(page).attention > 0), [pages]);
  const evidenceSummary = useMemo(() => detail ? summarizeSnapshotRules(detail) : null, [detail]);
  const visibleEvidenceRules = useMemo(() => {
    const results = detail?.rule_results || [];
    if (detailRuleFilter === "all") return results;
    return results.filter((rule) => ["fail", "needs_review", "unknown"].includes(rule.status));
  }, [detail, detailRuleFilter]);
  const factGroups = useMemo(() => ({
    proposed: facts.filter((fact) => fact.status === "proposed"),
    confirmed: facts.filter((fact) => fact.status === "confirmed"),
    expired: facts.filter((fact) => fact.status === "expired"),
    rejected: facts.filter((fact) => fact.status === "rejected"),
  }), [facts]);
  const sourceLabel = fixtureMode || selectedSite?.is_synthetic ? "合成演示数据" : serviceOnline ? "真实采集数据" : "来源待确认";

  function scrollToSnapshots() {
    document.getElementById("snapshot-list")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  useEffect(() => {
    if (activeView !== "sites" || !scrollToFactLibrary) return;
    document.getElementById("facts")?.scrollIntoView({ behavior: "smooth", block: "start" });
    setScrollToFactLibrary(false);
  }, [activeView, scrollToFactLibrary]);

  function requestViewChange(nextView: "sites" | "procurement" | "content" | "visibility") {
    if (nextView === activeView) return;
    if (activeView !== "sites" && workspaceDirty) {
      setPendingView(nextView);
      return;
    }
    setActiveView(nextView);
  }

  function cancelViewChange() {
    setPendingView(null);
    setScrollToFactLibrary(false);
  }

  function openFactLibrary() {
    setScrollToFactLibrary(true);
    requestViewChange("sites");
  }

  function confirmViewChange() {
    if (!pendingView) return;
    setWorkspaceDirty(false);
    setActiveView(pendingView);
    setPendingView(null);
  }

  const workflowSteps = [
    {
      number: "01",
      title: "登记站点",
      detail: sites.length ? `${sites.length} 个站点可用` : "先登记一个域名",
      state: sites.length ? "complete" : "active",
      icon: <Globe2 size={16} />,
      onClick: () => setFormOpen(true),
      disabled: false,
    },
    {
      number: "02",
      title: "运行检测",
      detail: auditBusy ? "正在读取页面" : selectedSite ? "检查当前站点" : "选择站点后运行",
      state: !selectedSite ? "blocked" : auditBusy ? "active" : pages.length ? "complete" : "active",
      icon: <Play size={16} />,
      onClick: () => void handleAudit(),
      disabled: !selectedSite || auditBusy || !serviceOnline,
    },
    {
      number: "03",
      title: "查看问题",
      detail: !pages.length ? "完成检测后查看" : attentionLabel(pageSummary),
      state: !pages.length ? "blocked" : pageSummary.attention ? "active" : "complete",
      icon: <TriangleAlert size={16} />,
      onClick: scrollToSnapshots,
      disabled: !pages.length,
    },
    {
      number: "04",
      title: "打开证据",
      detail: issuePages.length ? "查看规则与原始字段" : "从问题行打开",
      state: !issuePages.length ? "blocked" : "active",
      icon: <FileSearch size={16} />,
      onClick: () => issuePages[0] && setDetailId(issuePages[0].id),
      disabled: !issuePages.length,
    },
  ] as const;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="Trade Visibility 工作台首页">
          <span className="brand-mark"><Activity size={19} strokeWidth={2.4} /></span>
          <span className="brand-copy"><strong>Trade Visibility</strong><small>站点运营工作台</small></span>
        </a>
        <div className="workspace-switcher" title={`工作区：${workspaceKey}`}>
          <span className="workspace-avatar">TV</span>
          <label className="workspace-label"><small>当前工作区</small><select value={workspaceKey} onChange={(event) => changeWorkspace(event.target.value)} aria-label="切换工作区"><option value="" disabled>选择工作区</option>{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}</select></label>
          <ChevronDown size={15} />
        </div>
        <div className="nav-label">工作区</div>
        <nav className="side-nav" aria-label="主导航">
          <button className={`nav-item ${activeView === "sites" ? "active" : ""}`} type="button" onClick={() => requestViewChange("sites")} aria-current={activeView === "sites" ? "page" : undefined}><Globe2 size={17} />站点与审计</button>
          <button className={`nav-item ${activeView === "procurement" ? "active" : ""}`} type="button" onClick={() => requestViewChange("procurement")} aria-current={activeView === "procurement" ? "page" : undefined}><ShieldCheck size={17} />采购问题</button>
          <button className={`nav-item ${activeView === "content" ? "active" : ""}`} type="button" onClick={() => requestViewChange("content")} aria-current={activeView === "content" ? "page" : undefined}><FileSearch size={17} />内容改稿</button>
          <button className={`nav-item ${activeView === "visibility" ? "active" : ""}`} type="button" onClick={() => requestViewChange("visibility")} aria-current={activeView === "visibility" ? "page" : undefined}><Eye size={17} />可见性监测</button>
        </nav>
        <div className="sidebar-bottom">
          <div className="side-health">
            <span className={`health-dot ${serviceOnline ? "online" : "offline"}`} />
            <span>{serviceOnline ? "API 服务已连接" : "API 服务不可用"}</span>
            <button type="button" className="icon-button subtle" onClick={() => void refreshHealth()} title="重新检查 API" aria-label="重新检查 API"><RefreshCw size={14} /></button>
          </div>
          <div className="sidebar-foot">Trade Visibility Agent <span>v0.1</span></div>
        </div>
      </aside>

      <main className="main-content" id="top">
        <header className="topbar">
          <div className="breadcrumbs"><span>工作区</span><span className="crumb-divider">/</span><strong>{activeView === "sites" ? "站点与审计" : activeView === "procurement" ? "采购问题" : activeView === "content" ? "内容改稿" : "可见性监测"}</strong></div>
          <div className="topbar-actions">
            {fixtureMode && <span className="mode-label"><span className="mode-dot" />离线演示数据</span>}
            {activeView === "sites" && <button className="icon-button" type="button" title="刷新数据" aria-label="刷新数据" onClick={() => { void refreshHealth(); void refreshSites(); void refreshPages(); void refreshFacts(); }}><RefreshCw size={16} /></button>}
            <form className="identity-form" onSubmit={(event) => { event.preventDefault(); void applyIdentity(); }}><input value={identityInput} onChange={(event) => setIdentityInput(event.target.value)} placeholder="本地身份（demo/test）" aria-label="本地身份" /><button className="button button-secondary small" type="submit" disabled={identityBusy}>{identityBusy ? "加载中" : "应用身份"}</button></form><span className="user-avatar" title={session?.source || "demo"}>{(session?.user_id || "demo").slice(0, 2).toUpperCase()}</span>
          </div>
        </header>

        {identityError && <div className="alert compact" role="alert"><AlertCircle size={15} /><div><strong>身份或工作区加载失败</strong><span>{identityError}</span></div></div>}
        {activeView === "sites" ? <div className="page-wrap" id="sites">
          <div className="page-heading">
            <div>
              <div className="eyebrow">网站接入 <span>·</span> 技术审计</div>
              <h1>站点与页面快照</h1>
              <p>管理允许采集的站点，查看审计任务和页面证据。</p>
            </div>
            <div className="heading-actions">
              <button className="button button-secondary" type="button" onClick={() => setFormOpen(true)}><Plus size={16} />登记站点</button>
              <button className="button button-primary" type="button" onClick={() => void handleAudit()} disabled={!selectedSite || auditBusy || !serviceOnline} title={!serviceOnline ? "API 服务连接后可创建任务" : undefined}>
                {auditBusy ? <LoaderCircle className="spin" size={16} /> : <Activity size={16} />}
                {auditBusy ? "审计进行中" : "运行审计"}
              </button>
            </div>
          </div>

          <section className="workflow-panel" aria-labelledby="workflow-title">
            <div className="workflow-heading">
              <div>
                <span className="workflow-kicker">快速开始</span>
                <h2 id="workflow-title">从站点到证据，四步完成一次检查</h2>
                <p>按顺序操作即可；每一步都会保留可追溯的页面快照。</p>
              </div>
              <span className={`source-badge ${sourceLabel === "真实采集数据" ? "source-live" : sourceLabel === "合成演示数据" ? "source-fixture" : "source-unknown"}`}>
                <span />{sourceLabel}
              </span>
            </div>
            <div className="workflow-steps">
              {workflowSteps.map((step, index) => (
                <div className="workflow-step-wrap" key={step.number}>
                  <button
                    type="button"
                    className={`workflow-step ${step.state}`}
                    onClick={step.onClick}
                    disabled={step.disabled}
                    aria-label={`${step.title}：${step.detail}`}
                  >
                    <span className="workflow-number">{step.state === "complete" ? <Check size={14} /> : step.number}</span>
                    <span className="workflow-step-copy"><strong>{step.title}</strong><small>{step.detail}</small></span>
                    <span className="workflow-step-icon">{step.icon}</span>
                  </button>
                  {index < workflowSteps.length - 1 && <ChevronRight className="workflow-arrow" size={15} aria-hidden="true" />}
                </div>
              ))}
            </div>
          </section>

          {(healthError || sitesError) && (
            <div className="alert alert-error" role="alert">
              <AlertCircle size={17} />
              <div><strong>{healthError ? "无法连接后端 API" : "站点列表加载失败"}</strong><span>{healthError || sitesError}</span></div>
              <button type="button" className="icon-button subtle" onClick={() => { void refreshHealth(); void refreshSites(); }} aria-label="重试"><RefreshCw size={15} /></button>
            </div>
          )}

          {(notice || factNotice) && <div className="toast"><Check size={16} />{notice || factNotice}</div>}

          <section className="overview-grid" aria-label="站点概览">
            <article className="metric-panel">
              <div className="metric-top"><span>已登记站点</span><span className="metric-icon mint"><Globe2 size={17} /></span></div>
              <div className="metric-value">{sitesLoading ? <span className="skeleton number-skeleton" /> : sites.length}</div>
              <div className="metric-caption">当前工作区</div>
            </article>
            <article className="metric-panel">
              <div className="metric-top"><span>页面快照</span><span className="metric-icon blue"><ArrowDownToLine size={17} /></span></div>
              <div className="metric-value">{pagesLoading ? <span className="skeleton number-skeleton" /> : pages.length}</div>
              <div className="metric-caption">{selectedSite ? selectedSite.origin : "选择站点后查看"}</div>
            </article>
            <article className="metric-panel">
              <div className="metric-top"><span>需关注</span><span className="metric-icon amber"><TriangleAlert size={17} /></span></div>
              <div className="metric-value">{pagesLoading ? <span className="skeleton number-skeleton" /> : pageSummary.attention}</div>
              <div className="metric-caption">{pagesLoading ? "加载规则结果" : `${pageSummary.problem} 问题 · ${pageSummary.review} 待复核 · ${pageSummary.unknown} 未确认`}</div>
            </article>
            <article className="metric-panel service-panel">
              <div className="metric-top"><span>服务状态</span><span className={`service-pill ${serviceOnline ? "is-online" : "is-offline"}`}><span />{serviceOnline ? "运行正常" : "未连接"}</span></div>
              <div className="service-detail">{fixtureMode ? "Fixture 演示模式" : health?.version ? `版本 ${health.version}` : serviceOnline ? "后端 API 已响应" : "等待 FastAPI 启动"}</div>
              <div className="metric-caption">{health?.status ? `状态：${health.status}` : ""}</div>
            </article>
          </section>

          {auditRun && (
            <section className={`run-banner ${statusTone(auditRun.status)}`} aria-live="polite">
              <div className="run-icon">{isActive(auditRun.status) ? <LoaderCircle className="spin" size={18} /> : statusTone(auditRun.status) === "bad" ? <AlertCircle size={18} /> : <Check size={18} />}</div>
              <div className="run-body">
                <div className="run-title"><strong>审计任务</strong><code>{auditRun.id}</code><span className={`status-pill ${statusTone(auditRun.status)}`}>{statusText(auditRun.status)}</span></div>
                <div className="progress-track"><span style={{ width: `${getRunProgress(auditRun)}%` }} /></div>
              </div>
              <div className="run-meta">{auditRun.pages_completed ?? 0}{auditRun.pages_total ? ` / ${auditRun.pages_total}` : ""} 页</div>
              {auditError && <span className="run-error">{auditError}</span>}
            </section>
          )}

          {auditError && !auditRun && <div className="alert alert-error compact"><AlertCircle size={16} /><span>{auditError}</span></div>}

          <section className="site-section" aria-label="站点列表">
            <div className="section-heading">
              <div><h2>站点</h2><span className="count-label">{sitesLoading ? "加载中" : `${sites.length} 个`}</span></div>
              {sites.length > 0 && <button type="button" className="text-button" onClick={() => setFormOpen(true)}><Plus size={15} />添加站点</button>}
            </div>
            <div className="site-strip">
              {sitesLoading ? <SiteLoading /> : sitesError ? <InlineRetry message={sitesError} onRetry={() => void refreshSites()} /> : sites.length === 0 ? (
                <div className="empty-sites"><div className="empty-icon"><Globe2 size={19} /></div><div><strong>还没有登记站点</strong><span>添加允许采集的站点后即可创建审计任务。</span></div><button className="button button-secondary small" type="button" onClick={() => setFormOpen(true)}><Plus size={15} />登记站点</button></div>
              ) : sites.map((site) => (
                <button key={site.id} type="button" className={`site-card ${site.id === selectedId ? "selected" : ""}`} onClick={() => { setSelectedId(site.id); setAuditRun(null); setAuditError(""); }}>
                  <span className="site-card-icon"><Globe2 size={17} /></span>
                  <span className="site-card-main"><strong>{site.name}{site.is_synthetic && <span className="synthetic-label">合成演示</span>}</strong><small>{site.origin}</small></span>
                  <span className={`site-status ${statusTone(site.status)}`}><span />{site.status ? statusText(site.status) : "已接入"}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="snapshot-section" id="snapshot-list" aria-label="页面快照">
            <div className="section-heading snapshot-heading">
              <div><h2>页面快照</h2><span className="count-label">{selectedSite ? selectedSite.name : "选择站点"}{!pagesLoading && selectedSite ? ` · ${pages.length} 页` : ""}</span></div>
              <div className="table-actions">
                <label className="search-box"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 URL 或标题" aria-label="搜索快照" /></label>
                <button type="button" className="icon-button" onClick={() => void refreshPages()} disabled={!selectedId || pagesLoading} title="刷新快照" aria-label="刷新快照"><RefreshCw size={15} className={pagesLoading ? "spin" : ""} /></button>
              </div>
            </div>
            {selectedSite && pages.length > 0 && <div className="snapshot-context"><span className="context-dot" />当前显示 <strong>{sourceLabel}</strong> · 快照只读，不会修改站点内容</div>}
            <div className="table-wrap">
              {pagesLoading ? <SnapshotLoading /> : pagesError ? <InlineRetry message={pagesError} onRetry={() => void refreshPages()} /> : !selectedSite ? (
                <EmptyState icon={<Globe2 size={20} />} title="选择一个站点" detail="选择上方站点后查看已采集的页面快照。" />
              ) : visiblePages.length === 0 ? (
                <EmptyState icon={<ArrowDownToLine size={19} />} title={pages.length === 0 ? "暂无页面快照" : "没有匹配的页面"} detail={pages.length === 0 ? "运行一次审计任务后，采集到的页面会显示在这里。" : "调整搜索词后重试。"} />
              ) : (
                <table>
                  <thead><tr><th>页面</th><th>HTTP</th><th>快照时间</th><th>采集通道</th><th>需关注</th><th aria-label="页面操作" /></tr></thead>
                  <tbody>{visiblePages.map((page) => {
                    const summary = summarizeSnapshotRules(page);
                    return (
                      <tr key={page.id}>
                        <td><div className="page-cell"><strong>{page.title || "未提取标题"}{page.is_synthetic && <span className="synthetic-label">合成演示</span>}</strong><span>{page.url}</span></div></td>
                        <td><span className={`http-code ${page.status_code && page.status_code < 400 ? "ok" : page.status_code ? "error" : "unknown"}`}>{page.status_code ?? "--"}</span></td>
                        <td className="time-cell">{asDate(page.fetched_at)}</td>
                        <td><span className="channel-label">{page.channel || "HTTP"}</span></td>
                        <td><div className="finding-cell" title={`${summary.problem} 个问题，${summary.review} 个待复核，${summary.unknown} 个未确认`}><span className={`finding-count ${summary.attention > 0 ? "has-findings" : ""}`}>{summary.attention}</span>{summary.problem > 0 && <span className="signal-label problem">{summary.problem} 问题</span>}{summary.review > 0 && <span className="signal-label review">{summary.review} 待复核</span>}{summary.unknown > 0 && <span className="signal-label unknown">{summary.unknown} 未确认</span>}</div></td>
                        <td><div className="row-actions"><button className="icon-button row-icon-button" type="button" onClick={() => setDetailId(page.id)} title="查看页面审计证据" aria-label={`查看 ${page.url} 的审计证据`}><Eye size={14} /></button>{/^https?:\/\//i.test(page.url) && <a className="row-link" href={page.url} target="_blank" rel="noreferrer" title="在新窗口打开页面"><SquareArrowOutUpRight size={14} /></a>}</div></td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              )}
            </div>
            {pages.length > 0 && <div className="table-footer"><span>显示 {visiblePages.length} / {pages.length} 个快照</span><span title="快照用于审计证据，列表不会直接修改网站内容。">只读页面证据 <CircleHelp size={13} /></span></div>}
          </section>

          <section className="facts-section" id="facts" aria-labelledby="facts-title">
            <div className="section-heading facts-heading">
              <div><h2 id="facts-title">企业事实</h2><span className="count-label">{factsLoading ? "加载中" : `${facts.length} 条`}</span></div>
              <div className="facts-actions">
                <a className="button button-secondary small" href="/fact-import-template.csv" download title="下载 CSV 导入模板"><FileSpreadsheet size={14} />CSV 模板</a>
                <button type="button" className="button button-secondary small" onClick={() => factCsvInputRef.current?.click()} disabled={factSaving}><Upload size={14} />批量导入</button>
                <button type="button" className="button button-secondary small" onClick={() => setFactFormOpen(true)} disabled={factSaving}><Plus size={14} />单条录入</button>
                <input ref={factCsvInputRef} type="file" accept=".csv,text/csv" onChange={(event) => void handleImportFactsCsv(event)} hidden aria-label="选择事实 CSV 文件" />
              </div>
            </div>
            <div className="facts-intro"><ShieldCheck size={14} /><span>只有<strong>已确认、可公开且当前有效</strong>的事实可用于公开改稿。确认操作不会更改可见性；来源定位保持可追溯，不代表当前用户拥有额外审核权限。</span><label className="reviewer-field">审核人标识<input value={factReviewer} onChange={(event) => setFactReviewer(event.target.value)} aria-label="审核人标识" /></label></div>
            {factsError && <div className="alert alert-error facts-alert" role="alert"><AlertCircle size={15} /><span>{factsError}</span><button type="button" className="icon-button subtle" onClick={() => void refreshFacts()} aria-label="重试事实列表"><RefreshCw size={14} /></button></div>}
            {factsLoading ? <div className="facts-loading" aria-label="事实加载中"><span className="skeleton" /><span className="skeleton" /></div> : <div className="fact-groups">
              <FactGroup title="待确认" status="proposed" facts={factGroups.proposed} actionId={factActionId} onReview={(fact, action) => void handleFactReview(fact, action)} />
              <FactGroup title="已确认" status="confirmed" facts={factGroups.confirmed} actionId={factActionId} onReview={(fact, action) => void handleFactReview(fact, action)} />
              <FactGroup title="已过期" status="expired" facts={factGroups.expired} actionId={factActionId} onReview={(fact, action) => void handleFactReview(fact, action)} />
              {factGroups.rejected.length > 0 && <FactGroup title="已拒绝" status="rejected" facts={factGroups.rejected} actionId={factActionId} onReview={(fact, action) => void handleFactReview(fact, action)} />}
            </div>}
          </section>

          <footer className="page-footer"><span>演示结果仅代表当前采集快照，不代表搜索引擎收录或排名。</span><a href="https://developers.google.com/search/docs/appearance/ai-features" target="_blank" rel="noreferrer">关于搜索表现 <ArrowUpRight size={13} /></a></footer>
        </div> : activeView === "procurement" ? <ProcurementWorkspace workspaceKey={workspaceKey} onDirtyChange={setWorkspaceDirty} /> : activeView === "content" ? <ContentReviewWorkspace workspaceKey={workspaceKey} onDirtyChange={setWorkspaceDirty} onOpenFactLibrary={openFactLibrary} /> : <VisibilityWorkspace workspaceKey={workspaceKey} sites={sites} sitesLoading={sitesLoading} />}
      </main>

      {pendingView && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) cancelViewChange(); }}>
        <section className="site-dialog leave-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="leave-dialog-title" aria-describedby="leave-dialog-description">
          <div className="dialog-heading"><div><span className="dialog-icon leave-dialog-icon"><TriangleAlert size={18} /></span><div><h2 id="leave-dialog-title">离开当前工作区？</h2><p>尚有内容未保存</p></div></div><button className="icon-button" type="button" onClick={cancelViewChange} aria-label="继续编辑"><X size={17} /></button></div>
          <p className="leave-dialog-description" id="leave-dialog-description">{activeView === "procurement" ? "当前采购问题或问题集名称" : "当前内容任务中的问题、事实和页面绑定"}尚未提交。离开后，这些草稿会被清除。</p>
          <div className="dialog-note leave-dialog-note"><TriangleAlert size={15} /><span>选择“继续编辑”会保留当前草稿。</span></div>
          <div className="dialog-actions"><button type="button" className="button button-secondary" autoFocus onClick={cancelViewChange}>继续编辑</button><button type="button" className="button button-danger" onClick={confirmViewChange}>离开并丢弃</button></div>
        </section>
      </div>}

      {formOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !siteSaving) setFormOpen(false); }}>
        <section className="site-dialog" role="dialog" aria-modal="true" aria-labelledby="site-dialog-title">
          <div className="dialog-heading"><div><span className="dialog-icon"><Globe2 size={18} /></span><div><h2 id="site-dialog-title">登记站点</h2><p>输入网址即可开始；采集范围可以按需调整。</p></div></div><button className="icon-button" type="button" onClick={() => setFormOpen(false)} disabled={siteSaving} aria-label="关闭"><X size={17} /></button></div>
          <form onSubmit={(event) => void handleCreateSite(event)}>
            <label className="field-label">网站地址<input autoFocus value={siteOrigin} onChange={(event) => setSiteOrigin(event.target.value)} placeholder="acme.com 或 https://acme.com" inputMode="url" autoComplete="url" aria-describedby="site-address-hint" required /><span id="site-address-hint" className="field-hint">粘贴网站根地址；未填写协议时默认使用 https。</span></label>
            {siteOrigin.trim() && <p className="site-name-preview"><Globe2 size={14} /><span>站点名称：<strong>{siteName.trim() || suggestedSiteName || "输入有效网址后自动生成"}</strong></span></p>}
            <details className="site-advanced-options" open={siteAdvancedOpen} onToggle={(event) => setSiteAdvancedOpen(event.currentTarget.open)}>
              <summary>高级设置</summary>
              <div className="site-advanced-content">
                <span className="field-hint">默认允许采集整个网站。需要限定目录或修改站点名称时再设置。</span>
                <label className="field-label">站点名称（可选）<input value={siteName} onChange={(event) => setSiteName(event.target.value)} placeholder={suggestedSiteName || "按网站地址自动生成"} maxLength={120} /></label>
                <label className="field-label">允许路径<textarea value={allowedPaths} onChange={(event) => setAllowedPaths(event.target.value)} rows={2} placeholder="每行一个，例如 /products" /><span className="field-hint">每行一个，以 / 开头；默认 `/` 表示整个网站。</span></label>
                <label className="synthetic-option"><input type="checkbox" checked={isSynthetic} onChange={(event) => setIsSynthetic(event.target.checked)} /><span><strong>合成演示站点</strong><small>仅用于本地 fixture；只允许登记本机回环地址。</small></span></label>
              </div>
            </details>
            {formError && <div className="form-error" role="alert"><AlertCircle size={15} />{formError}</div>}
            <div className="dialog-note"><ShieldCheck size={15} /><span>采集范围由后端校验。此表单不会触发真实站点发布。</span></div>
            <div className="dialog-actions"><button type="button" className="button button-secondary" onClick={() => setFormOpen(false)} disabled={siteSaving}>取消</button><button type="submit" className="button button-primary" disabled={siteSaving}>{siteSaving ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}{siteSaving ? "正在登记" : "确认登记"}</button></div>
          </form>
        </section>
      </div>}

      {factFormOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !factSaving) setFactFormOpen(false); }}>
        <section className="site-dialog fact-dialog" role="dialog" aria-modal="true" aria-labelledby="fact-dialog-title">
          <div className="dialog-heading"><div><span className="dialog-icon"><ShieldCheck size={18} /></span><div><h2 id="fact-dialog-title">导入企业事实</h2><p>导入后先进入“待确认”，不会直接用于公开改稿。</p></div></div><button className="icon-button" type="button" onClick={() => setFactFormOpen(false)} disabled={factSaving} aria-label="关闭"><X size={17} /></button></div>
          <form onSubmit={(event) => void handleImportFact(event)}>
            <div className="fact-form-grid"><label className="field-label">主体<input autoFocus value={factSubject} onChange={(event) => setFactSubject(event.target.value)} placeholder="例如：Acme 运动器械" maxLength={500} required /></label><label className="field-label">属性<input value={factPredicate} onChange={(event) => setFactPredicate(event.target.value)} placeholder="例如：最大承重" maxLength={200} required /></label></div>
            <div className="fact-form-grid"><label className="field-label">值<input value={factValue} onChange={(event) => setFactValue(event.target.value)} placeholder="例如：150" required /></label><label className="field-label">单位（可选）<input value={factUnit} onChange={(event) => setFactUnit(event.target.value)} placeholder="kg / mm / 件" maxLength={100} /></label></div>
            <label className="field-label">来源标识<input value={factSourceId} onChange={(event) => setFactSourceId(event.target.value)} placeholder="报价单-2026-09" maxLength={255} required /><span className="field-hint">填写文件名、来源系统或页面编号，便于复核。</span></label>
            <label className="field-label">来源定位<input value={factSourceLocator} onChange={(event) => setFactSourceLocator(event.target.value)} placeholder="https://example.com/spec#load" maxLength={2048} required /><span className="field-hint">支持 URL 或内部定位文本；URL 可在事实列表中打开。</span></label>
            <fieldset className="visibility-choice">
              <legend>可见性</legend>
              <label className={`visibility-option ${factVisibility === "public" ? "selected" : ""}`}>
                <input type="radio" name="fact-visibility" value="public" checked={factVisibility === "public"} onChange={() => setFactVisibility("public")} />
                <span><strong>可公开</strong><small>审核确认且当前有效后，可用于公开改稿。</small></span>
              </label>
              <label className={`visibility-option ${factVisibility === "internal_only" ? "selected" : ""}`}>
                <input type="radio" name="fact-visibility" value="internal_only" checked={factVisibility === "internal_only"} onChange={() => setFactVisibility("internal_only")} />
                <span><strong>仅内部</strong><small>只在工作区保留，不用于公开改稿。</small></span>
              </label>
            </fieldset>
            <div className="dialog-note"><ShieldCheck size={15} /><span>导入者不会自动获得审核权限。确认时会记录你填写的审核人标识，并校验事实版本。</span></div>
            <div className="dialog-actions"><button type="button" className="button button-secondary" onClick={() => setFactFormOpen(false)} disabled={factSaving}>取消</button><button type="submit" className="button button-primary" disabled={factSaving}>{factSaving ? <LoaderCircle size={15} className="spin" /> : <Plus size={15} />}{factSaving ? "正在导入" : "导入待确认"}</button></div>
          </form>
        </section>
      </div>}

      {detailId && <div className="modal-backdrop evidence-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDetailId(""); }}>
        <section className="evidence-dialog" role="dialog" aria-modal="true" aria-labelledby="evidence-title" aria-busy={detailLoading}>
          <div className="evidence-heading">
            <div><span className="dialog-icon"><ShieldCheck size={18} /></span><div><h2 id="evidence-title">页面审计证据</h2><p>{detail?.url || "读取冻结快照与规则结果"}</p></div></div>
            <button className="icon-button" type="button" onClick={() => setDetailId("")} aria-label="关闭证据详情"><X size={17} /></button>
          </div>
          <div className="evidence-body">
            {detailLoading ? <SnapshotLoading /> : detailError ? <InlineRetry message={detailError} onRetry={() => setDetailReloadCount((count) => count + 1)} /> : detail ? <>
              <div className="evidence-meta">
                <span>HTTP <strong>{detail.status_code ?? "--"}</strong></span>
                <span>采集时间 <strong>{asDate(detail.fetched_at)}</strong></span>
                <span>规则集 <strong>{detail.rule_set_version || "未记录"}</strong></span>
                <span className={`source-badge ${detail.is_synthetic ? "source-fixture" : "source-live"}`}><span />{detail.is_synthetic ? "合成演示数据" : "真实采集数据"}</span>
              </div>
              <div className="evidence-hash"><span>内容 Hash</span><code>{detail.content_hash}</code></div>
              <div className="evidence-results-controls">
                <div className="evidence-summary">
                  <strong>{evidenceSummary?.total || 0} 条规则结果</strong>
                  <span className="summary-problem">{evidenceSummary?.problem || 0} 个问题</span>
                  <span className="summary-review">{evidenceSummary?.review || 0} 个待复核</span>
                  <span className="summary-unknown">{evidenceSummary?.unknown || 0} 个未确认</span>
                  <span>{evidenceSummary?.pass || 0} 条通过</span>
                </div>
                <div className="evidence-filter" role="group" aria-label="筛选规则结果">
                  <button type="button" aria-pressed={detailRuleFilter === "attention"} onClick={() => setDetailRuleFilter("attention")}>需关注 {evidenceSummary?.attention || 0}</button>
                  <button type="button" aria-pressed={detailRuleFilter === "all"} onClick={() => setDetailRuleFilter("all")}>全部 {evidenceSummary?.total || 0}</button>
                </div>
              </div>
              <div className="rule-list">
                {visibleEvidenceRules.length ? visibleEvidenceRules.map((rule) => {
                  const copy = ruleCopy(rule.rule_id);
                  return <article className="rule-item" key={`${rule.rule_id}:${rule.version}`}>
                    <div className="rule-item-heading"><div><strong>{copy.title}</strong><span><code className="rule-id">{rule.rule_id}</code> · v{rule.version} · {rule.scope}</span></div><span className={`rule-status ${ruleStatusTone(rule.status)}`}>{ruleStatusText(rule.status)}</span></div>
                    <p className="rule-localized">{copy.guidance}</p>
                    {rule.severity && <span className="rule-severity">严重度：{rule.severity}</span>}
                    <details className="rule-original"><summary>原始规则说明</summary><p>{rule.message}</p>{rule.remediation_hint && <p><strong>原始处理建议：</strong>{rule.remediation_hint}</p>}</details>
                    <div className="rule-remediation"><strong>处理建议</strong><span>{copy.guidance}</span></div>
                    <details className="rule-evidence" open={rule.status === "fail" || rule.status === "needs_review" || rule.status === "unknown"}><summary>证据字段 · {Object.keys(rule.evidence || {}).length}</summary><dl>{Object.entries(rule.evidence || {}).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{formatEvidenceValue(value)}</dd></div>)}</dl></details>
                  </article>;
                }) : detail.rule_results?.length && detailRuleFilter === "attention"
                  ? <div className="evidence-empty">当前没有需关注的规则结果。选择“全部”可查看其他项目。</div>
                  : <div className="evidence-empty">该快照没有完整规则评估记录。请重新运行审计以生成版本化规则结果。</div>}
              </div>
            </> : null}
          </div>
        </section>
      </div>}
    </div>
  );
}

function FactGroup({
  title,
  status,
  facts,
  actionId,
  onReview,
}: {
  title: string;
  status: Fact["status"];
  facts: Fact[];
  actionId: string;
  onReview: (fact: Fact, action: "confirm" | "reject") => void;
}) {
  return <section className={`fact-group fact-group-${status}`} aria-label={title}>
    <div className="fact-group-heading"><div><h3>{title}</h3><span>{facts.length} 条</span></div><span className="fact-status-dot" /></div>
    {facts.length === 0 ? <div className="fact-group-empty">暂无{title}事实</div> : <div className="fact-list">{facts.map((fact) => <FactRow key={fact.id} fact={fact} actionId={actionId} onReview={onReview} />)}</div>}
  </section>;
}

function FactRow({ fact, actionId, onReview }: { fact: Fact; actionId: string; onReview: (fact: Fact, action: "confirm" | "reject") => void }) {
  const current = factIsCurrent(fact);
  const publicReady = factCanUsePublicly(fact);
  const locatorHref = factLocatorHref(fact.source_locator);
  const action = actionId.startsWith(`${fact.id}:`) ? actionId.slice(`${fact.id}:`.length) : "";
  return <article className="fact-row">
    <div className="fact-main"><strong className="fact-subject-name">{fact.subject}</strong><div className="fact-indicators"><span className="fact-status-pill">{factStatusText(fact.status)}{fact.status === "confirmed" && !current ? " · 未生效" : ""}</span><span className={`fact-visibility-pill ${fact.visibility === "public" ? "public" : "internal"}`}>{fact.visibility === "public" ? "可公开" : "仅内部"}</span>{publicReady && <span className="fact-public-ready">公开改稿可用</span>}</div><div className="fact-claim"><span>{fact.predicate}</span><strong>{fact.value || "--"}</strong>{fact.unit && <span>{fact.unit}</span>}</div></div>
    <div className="fact-source"><span>{fact.source_id}</span>{locatorHref ? <a href={locatorHref} target="_blank" rel="noreferrer" title="打开来源定位">{fact.source_locator}<ArrowUpRight size={12} /></a> : <span>{fact.source_locator}</span>}</div>
    <div className="fact-meta"><span>版本 v{fact.version}</span><span>生效 {asDate(fact.valid_from)}</span>{fact.valid_until && <span>截止 {asDate(fact.valid_until)}</span>}</div>
    {fact.status === "proposed" && <div className="fact-actions"><button type="button" className="button button-primary small" onClick={() => onReview(fact, "confirm")} disabled={Boolean(actionId)}>{action === "confirm" ? <LoaderCircle size={13} className="spin" /> : <Check size={13} />}{action === "confirm" ? "确认中" : "确认"}</button><button type="button" className="button button-secondary small" onClick={() => onReview(fact, "reject")} disabled={Boolean(actionId)}>{action === "reject" ? <LoaderCircle size={13} className="spin" /> : <X size={13} />}{action === "reject" ? "拒绝中" : "拒绝"}</button></div>}
  </article>;
}

function SiteLoading() {
  return <div className="site-loading" aria-label="站点加载中"><span className="skeleton" /><span className="skeleton" /></div>;
}

function SnapshotLoading() {
  return <div className="snapshot-loading" aria-label="快照加载中">{[0, 1, 2].map((item) => <div className="snapshot-skeleton" key={item}><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div>)}</div>;
}

function EmptyState({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return <div className="empty-state"><span className="empty-icon">{icon}</span><strong>{title}</strong><span>{detail}</span></div>;
}

function InlineRetry({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="inline-error"><AlertCircle size={17} /><span>{message}</span><button type="button" className="button button-secondary small" onClick={onRetry}><RefreshCw size={14} />重试</button></div>;
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
