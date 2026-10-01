export type Health = {
  status?: string;
  mode?: string;
  fixture?: boolean;
  fixture_available?: boolean;
  version?: string;
  [key: string]: unknown;
};

export type KnowledgeEntry = {
  id: string;
  entry_type: "external_guidance" | string;
  claim_type: string;
  title: string;
  summary: string;
  scope: string;
  market_scope: string;
  source_url: string;
  source_date: string;
  accessed_at: string;
  valid_until: string;
  license: string;
  source_kind: string;
  topic_tags: string;
  freshness_policy: string;
  last_verified_at: string;
};

export type Site = {
  id: string;
  workspace_id?: string;
  name: string;
  origin: string;
  allowed_paths?: string[];
  is_synthetic?: boolean;
  status?: string;
  created_at?: string;
};

export type PageSnapshot = {
  id: string;
  page_id?: number;
  url: string;
  final_url?: string;
  title?: string;
  content_hash?: string;
  status_code?: number;
  status?: string;
  fetched_at?: string;
  channel?: string;
  finding_count?: number;
  rule_count?: number;
  rule_problem_count?: number;
  rule_review_count?: number;
  rule_unknown_count?: number;
  is_synthetic?: boolean;
};

export type RuleResult = {
  rule_id: string;
  version: string;
  scope: string;
  status: "pass" | "fail" | "unknown" | "not_applicable" | "needs_review";
  severity: string;
  message: string;
  evidence: Record<string, unknown>;
  remediation_hint?: string | null;
};

export type SnapshotDetail = PageSnapshot & {
  page_id: number;
  job_id: number | null;
  content_type?: string | null;
  rule_set_version: string;
  rule_results: RuleResult[];
};

export type Fact = {
  id: number;
  workspace_id: number;
  series_id: string;
  parent_id: number | null;
  subject: string;
  predicate: string;
  value: string | null;
  unit: string | null;
  source_id: string;
  source_locator: string;
  visibility: "public" | "internal_only";
  status: "proposed" | "confirmed" | "rejected" | "expired" | string;
  version: number;
  valid_from: string;
  valid_until: string | null;
  reviewer: string | null;
  reviewed_at: string | null;
  created_at: string;
};

export type AuditRun = {
  id: string;
  site_id?: string;
  status: string;
  progress?: number;
  pages_total?: number;
  pages_completed?: number;
  findings_count?: number;
  error?: string;
  created_at?: string;
  completed_at?: string;
};

export type ProcurementQuestionInput = {
  question: string;
  product: string;
  use_case: string;
  buyer_role: string;
  purchase_stage: string;
  target_market: string;
  language: string;
  page_ids: number[];
};

export type ProcurementQuestionPageMapping = {
  page_id: number;
  position: number;
  canonical_url: string;
};

export type ProcurementQuestion = Omit<ProcurementQuestionInput, "page_ids"> & {
  id: number;
  position: number;
  page_mappings: ProcurementQuestionPageMapping[];
};

export type ProcurementQuestionSetSummary = {
  id: number;
  workspace_id: number;
  site_id: number;
  name: string;
  current_version: number;
  current_state: "draft" | "frozen" | string;
  created_at: string;
};

export type ProcurementQuestionSet = ProcurementQuestionSetSummary & {
  versions: ProcurementQuestionSetVersion[];
};

export type ProcurementQuestionSetVersion = {
  id: number;
  question_set_id: number;
  version: number;
  edit_version: number;
  state: "draft" | "frozen" | string;
  created_at: string;
  frozen_at: string | null;
  questions: ProcurementQuestion[];
};

export type ContentGenerationItemInput = {
  question_id: number;
  page_id: number;
  expected_snapshot_id: number;
  expected_snapshot_hash: string;
  request_summary: string;
  required_fact_ids: number[];
};

export type ChangeRevision = {
  id: number;
  change_request_id: number;
  revision: number;
  state: string;
  base_snapshot_id: number | null;
  base_content_hash: string | null;
  field_diff: Record<string, unknown>;
  fact_versions: Array<{ fact_id: number; series_id: string; version: number }>;
  content_hash: string;
  created_at: string;
};

export type ChangeApproval = {
  id: number;
  change_request_id: number;
  revision_id: number;
  revision_hash: string;
  reviewer: string;
  decision: string;
  comment: string | null;
  created_at: string;
};

export type ChangeRequest = {
  id: number;
  workspace_id: number;
  site_id: number;
  state: string;
  version: number;
  current_revision_id: number | null;
  title: string | null;
  created_at: string;
  updated_at: string;
  revision: ChangeRevision | null;
  approvals: ChangeApproval[];
};

export type ContentGenerationItem = {
  id: number;
  task_id: number;
  question_id: number;
  question: string;
  page_id: number;
  canonical_url: string;
  change_request_id: number;
  snapshot_id: number;
  snapshot_hash: string;
  request_summary: string;
  required_fact_ids: number[];
  status: string;
  thread_id: string;
  created_at: string;
  change_request: ChangeRequest;
};

export type ContentGenerationTaskSummary = {
  id: number;
  workspace_id: number;
  site_id: number;
  question_set_version_id: number;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
};

export type ContentGenerationTask = ContentGenerationTaskSummary & { items: ContentGenerationItem[] };

export type VisibilityProvider = "fixture" | "structured_http" | "manual_capture";

export type VisibilityCitation = {
  url: string;
  title?: string | null;
  source?: string | null;
  [key: string]: unknown;
};

export type VisibilitySample = {
  id: number;
  run_id?: number;
  question_id: number;
  position: number;
  question?: string | null;
  brand_query: boolean;
  status: "succeeded" | "failed" | "unavailable" | string;
  raw_response?: string | null;
  answer_text?: string | null;
  citations?: VisibilityCitation[] | string[];
  mentioned_domains?: string[];
  provider_request_id?: string | null;
  model?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_usd?: string | number | null;
  error_code?: string | null;
  error_message?: string | null;
  completed_at?: string | null;
  created_at?: string;
};

export type VisibilityMetrics = {
  brand_mention_rate?: number | null;
  site_citation_rate?: number | null;
  sampling_success_rate?: number | null;
  question_coverage_rate?: number | null;
  successful_samples?: number;
  failed_samples?: number;
  unavailable_samples?: number;
  [key: string]: unknown;
};

export type VisibilityRun = {
  id: number | string;
  workspace_id?: number | string;
  site_id: number | string;
  question_set_id?: number;
  question_set_version_id?: number;
  provider: string;
  provider_kind?: string;
  provider_model?: string | null;
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | string;
  is_synthetic?: boolean;
  market?: string;
  language?: string;
  planned_samples?: number;
  successful_samples?: number;
  failed_samples?: number;
  unavailable_samples?: number;
  total_cost_usd?: string | number;
  budget_usd?: string | number | null;
  capability?: Record<string, unknown>;
  capability_json?: Record<string, unknown> | string;
  metrics?: VisibilityMetrics;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at?: string;
  samples?: VisibilitySample[];
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const DEFAULT_WORKSPACE_ID = import.meta.env.VITE_WORKSPACE_ID || "demo-workspace";

function describeApiError(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (item && typeof item === "object") {
        const error = item as Record<string, unknown>;
        const location = Array.isArray(error.loc) ? error.loc.join(".") : "";
        return `${location ? `${location}: ` : ""}${String(error.msg || "invalid value")}`;
      }
      return String(item);
    }).join("; ");
  }
  if (value && typeof value === "object") {
    const detail = value as Record<string, unknown>;
    if (typeof detail.row === "number") {
      const errors = Array.isArray(detail.errors)
        ? detail.errors.map((item) => {
          if (!item || typeof item !== "object") return String(item);
          const error = item as Record<string, unknown>;
          return `${String(error.field || "")} ${String(error.message || "invalid value")}`.trim();
        }).join("; ")
        : "";
      return `CSV 第 ${detail.row} 行：${errors || String(detail.message || "数据无效")}`;
    }
    if (Array.isArray(detail.missing_columns) || Array.isArray(detail.unknown_columns)) {
      const missing = Array.isArray(detail.missing_columns) ? detail.missing_columns.join(", ") : "无";
      const unknown = Array.isArray(detail.unknown_columns) ? detail.unknown_columns.join(", ") : "无";
      return `CSV 表头不匹配，缺少：${missing}；不支持：${unknown}`;
    }
    return JSON.stringify(detail);
  }
  return "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown; message?: unknown };
      detail = describeApiError(body.detail ?? body.message) || detail;
    } catch {
      // Keep the status message when the server has no JSON error body.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function unwrapList<T>(payload: unknown, key: string): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (payload && typeof payload === "object") {
    const value = (payload as Record<string, unknown>)[key];
    if (Array.isArray(value)) return value as T[];
    const data = (payload as Record<string, unknown>).data;
    if (Array.isArray(data)) return data as T[];
  }
  return [];
}

export async function getHealth() {
  return request<Health>("/api/health");
}

export async function listKnowledgeEntries() {
  return request<KnowledgeEntry[]>("/api/knowledge");
}

export async function listSites(workspaceKey = DEFAULT_WORKSPACE_ID) {
  const payload = await request<unknown>(`/api/sites?workspace_id=${encodeURIComponent(workspaceKey)}`);
  return unwrapList<Site>(payload, "sites");
}

export async function createSite(input: {
  workspace_id: string;
  name: string;
  origin: string;
  allowed_paths: string[];
  is_synthetic: boolean;
}) {
  return request<Site>("/api/sites", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function createAudit(siteId: string, workspaceKey = DEFAULT_WORKSPACE_ID) {
  return request<AuditRun>(`/api/workspaces/${encodeURIComponent(workspaceKey)}/sites/${encodeURIComponent(siteId)}/audit-runs`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function getAuditRun(runId: string, workspaceKey = DEFAULT_WORKSPACE_ID) {
  return request<AuditRun>(`/api/workspaces/${encodeURIComponent(workspaceKey)}/audit-runs/${encodeURIComponent(runId)}`);
}

export async function listPages(siteId: string, workspaceKey = DEFAULT_WORKSPACE_ID) {
  const payload = await request<unknown>(`/api/workspaces/${encodeURIComponent(workspaceKey)}/sites/${encodeURIComponent(siteId)}/pages`);
  return unwrapList<PageSnapshot>(payload, "pages");
}

function querySetPath(workspaceKey: string, siteId: string, setId?: number) {
  const collection = `/api/workspaces/${encodeURIComponent(workspaceKey)}/sites/${encodeURIComponent(siteId)}/query-sets`;
  return setId === undefined ? collection : `${collection}/${encodeURIComponent(setId)}`;
}

export async function listProcurementQuestionSets(workspaceKey: string, siteId: string) {
  const payload = await request<unknown>(querySetPath(workspaceKey, siteId));
  return unwrapList<ProcurementQuestionSetSummary>(payload, "query_sets");
}

export async function createProcurementQuestionSet(workspaceKey: string, siteId: string, input: {
  name: string;
  questions: ProcurementQuestionInput[];
}) {
  return request<ProcurementQuestionSetSummary>(querySetPath(workspaceKey, siteId), {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getProcurementQuestionSet(workspaceKey: string, siteId: string, setId: number) {
  return request<ProcurementQuestionSet>(querySetPath(workspaceKey, siteId, setId));
}

export async function getProcurementQuestionSetVersion(workspaceKey: string, siteId: string, setId: number, version: number) {
  return request<ProcurementQuestionSetVersion>(`${querySetPath(workspaceKey, siteId, setId)}/versions/${encodeURIComponent(version)}`);
}

export async function createProcurementQuestionSetVersion(workspaceKey: string, siteId: string, setId: number, expectedVersion: number) {
  return request<ProcurementQuestionSetVersion>(`${querySetPath(workspaceKey, siteId, setId)}/versions`, {
    method: "POST",
    body: JSON.stringify({ expected_version: expectedVersion }),
  });
}

export async function updateProcurementQuestionSetVersion(workspaceKey: string, siteId: string, setId: number, version: number, expectedVersion: number, questions: ProcurementQuestionInput[]) {
  return request<ProcurementQuestionSetVersion>(`${querySetPath(workspaceKey, siteId, setId)}/versions/${encodeURIComponent(version)}`, {
    method: "PUT",
    body: JSON.stringify({ expected_version: expectedVersion, questions }),
  });
}

export async function freezeProcurementQuestionSetVersion(workspaceKey: string, siteId: string, setId: number, version: number, expectedVersion: number) {
  return request<ProcurementQuestionSetVersion>(`${querySetPath(workspaceKey, siteId, setId)}/versions/${encodeURIComponent(version)}/freeze`, {
    method: "POST",
    body: JSON.stringify({ expected_version: expectedVersion }),
  });
}

export async function listContentGenerationTasks(workspaceKey: string, siteId: string) {
  const path = `/api/workspaces/${encodeURIComponent(workspaceKey)}/sites/${encodeURIComponent(siteId)}/content-tasks`;
  const payload = await request<unknown>(path);
  return unwrapList<ContentGenerationTaskSummary>(payload, "tasks");
}

export async function createContentGenerationTask(workspaceKey: string, siteId: string, setId: number, versionNumber: number, items: ContentGenerationItemInput[]) {
  const path = `/api/workspaces/${encodeURIComponent(workspaceKey)}/sites/${encodeURIComponent(siteId)}/query-sets/${encodeURIComponent(setId)}/versions/${encodeURIComponent(versionNumber)}/content-tasks`;
  return request<ContentGenerationTask>(path, {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

export async function getContentGenerationTask(workspaceKey: string, siteId: string, taskId: number) {
  const path = `/api/workspaces/${encodeURIComponent(workspaceKey)}/sites/${encodeURIComponent(siteId)}/content-tasks/${encodeURIComponent(taskId)}`;
  return request<ContentGenerationTask>(path);
}

export async function submitChangeForApproval(changeId: number, expectedVersion: number, workspaceKey = DEFAULT_WORKSPACE_ID) {
  const path = `/api/changes/${encodeURIComponent(changeId)}/submit-approval?workspace_id=${encodeURIComponent(workspaceKey)}`;
  return request<ChangeRequest>(path, {
    method: "POST",
    body: JSON.stringify({ expected_version: expectedVersion }),
  });
}

export async function decideChangeApproval(input: {
  changeId: number;
  workspaceKey?: string;
  reviewer: string;
  decision: "approved" | "rejected";
  revisionId: number;
  revisionHash: string;
  expectedVersion: number;
  comment?: string;
}) {
  const { changeId, workspaceKey = DEFAULT_WORKSPACE_ID, reviewer, decision, revisionId, revisionHash, expectedVersion, comment } = input;
  const path = `/api/changes/${encodeURIComponent(changeId)}/approval?workspace_id=${encodeURIComponent(workspaceKey)}`;
  return request<ChangeRequest>(path, {
    method: "POST",
    body: JSON.stringify({
      reviewer,
      decision,
      revision_id: revisionId,
      revision_hash: revisionHash,
      expected_version: expectedVersion,
      comment: comment || null,
    }),
  });
}

export async function getSnapshot(snapshotId: string, workspaceKey = DEFAULT_WORKSPACE_ID) {
  return request<SnapshotDetail>(`/api/workspaces/${encodeURIComponent(workspaceKey)}/snapshots/${encodeURIComponent(snapshotId)}`);
}

export async function listFacts(workspaceKey = DEFAULT_WORKSPACE_ID) {
  return request<Fact[]>(`/api/workspaces/${encodeURIComponent(workspaceKey)}/facts`);
}

export async function importFact(input: {
  workspace_id: string;
  subject: string;
  predicate: string;
  value: string;
  unit?: string;
  source_id: string;
  source_locator: string;
  visibility: "public" | "internal_only";
  valid_from?: string;
  valid_until?: string;
}) {
  return request<Fact>(`/api/workspaces/${encodeURIComponent(input.workspace_id)}/facts`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function importFactsCsv(workspaceKey: string, file: File) {
  if (file.size > 1_000_000) throw new Error("CSV 文件不能超过 1 MB");
  return request<Fact[]>(`/api/workspaces/${encodeURIComponent(workspaceKey)}/facts/import-csv`, {
    method: "POST",
    headers: { "Content-Type": "text/csv" },
    body: await file.text(),
  });
}

export async function confirmFact(factId: number, reviewer: string, expectedVersion?: number, workspaceKey = DEFAULT_WORKSPACE_ID) {
  return request<Fact>(`/api/workspaces/${encodeURIComponent(workspaceKey)}/facts/${encodeURIComponent(factId)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ reviewer, expected_version: expectedVersion }),
  });
}

export async function rejectFact(factId: number, reviewer: string, expectedVersion?: number, workspaceKey = DEFAULT_WORKSPACE_ID) {
  return request<Fact>(`/api/workspaces/${encodeURIComponent(workspaceKey)}/facts/${encodeURIComponent(factId)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reviewer, expected_version: expectedVersion }),
  });
}

function visibilityRunsPath(workspaceKey: string, siteId: string, runId?: number | string) {
  const collection = `/api/workspaces/${encodeURIComponent(workspaceKey)}/sites/${encodeURIComponent(siteId)}/visibility-runs`;
  return runId === undefined ? collection : `${collection}/${encodeURIComponent(runId)}`;
}

export async function listVisibilityRuns(workspaceKey: string, siteId: string) {
  const payload = await request<unknown>(visibilityRunsPath(workspaceKey, siteId));
  return unwrapList<VisibilityRun>(payload, "runs");
}

export async function getVisibilityRun(workspaceKey: string, siteId: string, runId: number | string) {
  return request<VisibilityRun>(visibilityRunsPath(workspaceKey, siteId, runId));
}

export async function createVisibilityRun(workspaceKey: string, siteId: string, input: {
  question_set_version_id: number;
  provider: VisibilityProvider;
  provider_kind?: string;
  market: string;
  language: string;
  brand_terms?: string[];
  max_samples?: number;
  budget_usd?: number;
  idempotency_key?: string;
}) {
  return request<VisibilityRun>(visibilityRunsPath(workspaceKey, siteId), {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function executeVisibilityRun(workspaceKey: string, siteId: string, runId: number | string) {
  return request<VisibilityRun>(`${visibilityRunsPath(workspaceKey, siteId, runId)}/execute`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}
