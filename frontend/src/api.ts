// 后端 API 封装：登录后以 Authorization: Bearer <令牌> 访问；令牌失效时回到登录页。
const TOKEN_KEY = 'tfos.token';
let token: string | null = (() => {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
})();
let onUnauthorized: () => void = () => {};

export function setToken(t: string | null) {
  token = t;
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 隐私模式下只在内存中保存 */
  }
}
export const hasToken = () => !!token;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}
const authHeaders = (): Record<string, string> => (token ? { Authorization: `Bearer ${token}` } : {});

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = authHeaders();
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const res = await fetch(url, { method, headers, body: payload });
  if (res.status === 401 && !url.startsWith('/api/auth/')) {
    setToken(null);
    onUnauthorized();
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* 非 JSON 错误 */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body: unknown = {}) => request<T>('POST', url, body),
  put: <T>(url: string, body: unknown) => request<T>('PUT', url, body),
};

/** 带账号头下载文件（Excel 导出）。 */
export async function download(url: string, fallbackName: string) {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const cd = res.headers.get('Content-Disposition') || '';
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : fallbackName;
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- 类型 ----------

export interface NavItem { id: string; label: string }
export interface NavGroup { label: string; items: NavItem[] }
export interface Me {
  user_id: string; username: string; name: string; role: 'finance' | 'team' | 'dept' | 'admin'; who: string;
  team_id: string | null; dept: string | null; scope_label: string; role_name: string; nav: NavGroup[]; must_change: number;
}
export interface AccountRow {
  user_id: string; username: string | null; name: string; role: string; role_name: string; who: string; team_id: string | null;
  dept: string | null; scope_label: string; must_change: number; enabled: number; last_login: string | null; created_at: string | null;
}

export interface Kpi {
  revenue: number; contract: number; fixed: number; variable: number; total_cost: number; gross_margin: number; net_profit: number;
}
export interface OverviewWell extends Kpi { well_id: string; block: string; well_type: string; team: string; phase: string; remark: string }
export interface Overview { period: string; wells: OverviewWell[]; sum: Omit<Kpi, 'contract'>; show_revenue: boolean; scope: string }

export interface TreeNode {
  code: string; name: string; level: number; is_leaf: boolean; parent: string | null; dept: string | null;
  calc_type: string | null; col: string | null; behavior: string; amount: number; origins: string[];
}
export interface WellInfo {
  well_id: string; market: string; block: string; well_category: string; well_type: string; design_depth: number;
  actual_depth: number; client: string; contract_amount: number; status: string; team: string; phase: string;
  rig_type: string; captain: string; team_no?: string; remark?: string; import_sources?: string[];
}
export interface WellResult { info: WellInfo; kpi: Kpi; tree: TreeNode[]; show_revenue: boolean; can_export: boolean; period: string }

export interface EventRow {
  event_id: number; date: string; qty: string; price: string; factor: string; amount: number; rule: string; source: string;
  origin: string; status: string; reversal_of: number | null;
}
export interface ItemEvents {
  item: { code: string; name: string; behavior: string; dept: string; calc_type: string; col: string };
  events: EventRow[]; total: number;
}
export interface KV { k: string; v: string }
export interface Trace {
  event_id: number; title: string; amount: number; formula: string; origin: string; status: string; note?: string;
  rule_lines: KV[]; clause: { ref: string; pre: string; hit: string; post: string } | null; src_title: string; src_lines: KV[];
}
export interface ExportRow { k: string; v: string | number | null; kind: 'plain' | 'bold' | 'section'; level: number }

export interface Check { level: '阻断' | '警告' | '提示'; rule: string; message: string }
export interface LineDef {
  key: string; section: string; group: string; label: string; unit: string; fact_type: string; item_code: string; item_name: string;
  dept: string | null; needs_doc: boolean; core: boolean; rules: string[]; priced: boolean; recent: boolean; show: boolean;
}
export interface ReportSection { id: string; title: string; fact: string; how: string; drives: string }
export interface ReportCatalog {
  sections: ReportSection[]; work_conditions: string[];
  not_collected: { reason: string; items: { item_code: string; item_name: string }[] }[];
}
export interface Report {
  report_id: number | null; well_id: string; team_id: string; report_date: string; version: number; status: string; phase: string;
  depth_start: number; depth_end: number; hours: Record<string, number>; lines: Record<string, number>; notes: Record<string, string>;
  reporter: string | null; carried?: boolean;
  review_comment?: string | null; supersedes_report_id?: number | null;
  header: { well_id: string; date: string; team: string; block: string; well_type: string; phase: string; in_period: boolean };
  line_defs: LineDef[]; checks: Check[];
  history: { action: string; operator: string; operated_at: string; comment: string | null }[];
  batch?: { created: number; reversed: number; amount_change: number };
}
export interface ReviewRow {
  report_id: number; well_id: string; report_date: string; version: number; status: string; phase: string; depth_start: number;
  depth_end: number; reporter: string; submitted_at: string; team: string; reviewer: string | null; reviewed_at: string | null;
  review_comment: string | null; alerts: { level: string; message: string }[];
}

export interface RuleRow {
  rule_id: number; rule_code: string; version: number; name: string; item_code: string; item_name: string; status: string;
  doc: string; clause_no: string; conf: string; origin: string; owner_dept: string;
}
export interface Condition { dimension: string; operator: string; value: string }
export interface FactorRule { dimension: string; operator: string; value: string | number; factor: number }
export interface RuleDetail {
  rule: {
    rule_id: number; rule_code: string; version: number; name: string; item_code: string; item_name: string; status: string;
    origin: string; ai_confidence: number | null; unit_price: number; unit: string; combine_mode: string; granularity: string;
    valid_from: string; valid_to: string | null; pricing_method: string; fact_type: string | null; owner_dept: string; priority: number;
  };
  params: { factor_rules?: FactorRule[]; line?: string; material?: string; note?: string };
  conditions: Condition[];
  fields: { label: string; value: string; level: string }[];
  clause: { doc: string; doc_type: string; no: string; page: string; pre: string; hit: string; post: string; hash: string } | null;
  tests: { well_id: string | null; period: string | null; expected_amount: number | null; calc_amount: number | null; deviation_rate: number | null; result: string }[];
  versions: { rule_id: number; version: number; status: string; unit_price: number; valid_from: string; valid_to: string | null }[];
  logs: { action: string; operator: string; operated_at: string; comment: string | null }[];
  hits: { count: number; amount: number };
  can_edit: boolean; can_confirm: boolean; can_publish: boolean; can_reject: boolean; can_new_version: boolean;
  options: { dimensions: string[]; operators: string[] };
  backtest?: { result: string; message: string };
  batch?: { created: number; reversed: number; amount_change: number } | null;
}

export interface Segment { id: number; phase: string; start_at: string; end_at: string | null; team_id: string; team: string }
export interface WellRow {
  well_id: string; market: string; block: string; well_category: string; well_type: string; design_depth: number;
  actual_depth: number; client: string; contract_amount: number; status: string; team: string; phase: string; segments: Segment[];
}

// ---------- 单井基础信息 ----------

export interface InfoDerived {
  pre_days: number; drill_days: number; completion_days: number; status: string; reg_lag: string; reg_lag_days: number | null;
  comp_lag: string | null; comp_lag_days: number | null; section_days: Record<string, number>; staff_total: number;
}
export interface InfoFields {
  well_id: string; period: string; market: string; captain: string; rig_type: string; client: string; block: string;
  well_category: string; well_type: string; spud_sections: number; design_depth: number;
  milestones: Record<string, string | null>; staff: Record<string, number>;
}
export interface InfoRecord extends InfoFields {
  report_id: number | null; team_id: string; team_no: string; version: number; status: string; creator: string;
  registered_at: string | null; completion_reported_at: string | null; submitted_at: string | null;
  source?: string; return_comment?: string | null; returned_by?: string | null; returned_at?: string | null;
  derived: InfoDerived; checks: Check[];
  history: { report_id: number; version: number; status: string; submitted_at: string | null; creator: string;
    source: string; return_comment: string | null; returned_by: string | null; returned_at: string | null }[];
  batch?: { created: number; reversed: number; amount_change: number } | null;
}
export interface InfoOptions {
  markets: string[]; categories: string[]; well_types: string[]; rigs: string[]; sections: string[];
  staff: { key: string; label: string }[]; clients: string[]; blocks: string[];
  team: { team_no: string; name: string; captain: string; rig_type: string } | null; completion_lag_limit: number;
}
export interface InfoMine {
  report_id: number; well_id: string; version: number; status: string; well_status: string; reg_lag: string;
  comp_lag: string | null; submitted_at: string | null; returned: boolean; return_comment: string | null;
}
export interface InfoSummaryRow extends InfoFields {
  report_id: number; team_id: string; team_no: string; version: number; creator: string; registered_at: string;
  completion_reported_at: string | null; submitted_at: string; derived: InfoDerived; source: string;
  pending_return: { report_id: number; return_comment: string; returned_by: string; returned_at: string } | null;
}
export interface InfoWellDetail extends InfoSummaryRow {
  segments: { phase: string; start_at: string; end_at: string | null; team: string }[];
  versions: { report_id: number; version: number; status: string; submitted_at: string; creator: string; team: string;
    source: string; return_comment: string | null }[];
}
export interface PendingReturn {
  report_id: number; well_id: string; version: number; source: string; return_comment: string; returned_by: string;
  returned_at: string; updated_at: string; team_no: string; team: string;
}
export interface ImportResult {
  rows: { row: number; well_id: string; team_no: string; action: '新增' | '更新' | '无变化' | '退回队伍' | '跳过'; new_team: boolean;
    status: string; skip: string | null; checks: Check[] }[];
  count: Record<string, number>; committed: boolean; new_teams: string[];
}

// ---------- 经营对标 ----------

export interface BenchGroup {
  keys: Record<string, string>; well_count: number; well_months: number;
  revenue: number; fixed: number; variable: number; total_cost: number; gross_margin: number; net_profit: number;
  footage: number; cost_with_footage: number;
  gm_rate: number | null; net_rate: number | null; avg_cost: number; avg_net: number; avg_revenue: number; cost_per_m: number | null;
  items: { well_id: string; period: string; market: string; block: string; team: string; revenue: number; total_cost: number;
    gross_margin: number; net_profit: number; footage: number; remark: string }[];
}
export interface Benchmark {
  period: string; periods: string[]; dims: string[]; dim_labels: Record<string, string>;
  filters: Record<'market' | 'block' | 'team', string[]>; options: Record<'market' | 'block' | 'team', { value: string; wells: number }[]>;
  filtered: boolean; groups: BenchGroup[]; scope: BenchGroup; total: BenchGroup;
}

// ---------- 单井经营结果模板导入 ----------

export interface ResultSheet {
  sheet: string; well_id: string; period: string; team_no: string; new_well: boolean; new_team: boolean;
  revenue: number; total_cost: number; net_profit: number; action: '导入' | '覆盖' | '无变化' | '阻断'; blocks: string[]; warns: string[];
}
export interface ResultImport { count: Record<string, number>; committed: boolean; sheets: ResultSheet[] }
