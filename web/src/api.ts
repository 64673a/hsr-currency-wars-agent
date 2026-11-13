// 后端接口(docs/TECH_DESIGN.md 第 13 节)的调用封装和数据类型。

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI 的校验错误:[{loc, msg}]
    return detail
      .map((d) => `${(d.loc ?? []).filter((x: unknown) => x !== 'body').join('.')}: ${d.msg}`)
      .join(';');
  }
  return JSON.stringify(detail);
}

export async function api<T = unknown>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const resp = await fetch(path, {
    method: init?.method ?? 'GET',
    headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!resp.ok) throw new ApiError(resp.status, data?.detail !== undefined ? detailText(data.detail) : resp.statusText);
  return data as T;
}

export interface Provider {
  name: string;
  format: 'openai' | 'anthropic';
  base_url: string;
  api_key: string;
  has_key?: boolean;
  extra_headers: Record<string, string>;
  anthropic_version: string;
  models?: string[];
}

export interface ModelProfile {
  name: string;
  provider: string;
  model: string;
  max_tokens: number | null;
  extra_body: Record<string, unknown>;
  extra_headers: Record<string, string>;
  tool_mode: 'native' | 'json';
  timeout_seconds: number;
}

export interface ModelInfo {
  id: string;
  display_name: string;
  owned_by: string;
  context_window: number | null;
}

export interface TestResult {
  ok: boolean;
  error?: string;
  status?: number;
  tool_call?: boolean;
  tool_arguments?: Record<string, unknown> | null;
  reasoning?: boolean;
  reasoning_preview?: string;
  text_preview?: string;
  model?: string;
  latency_s?: number;
  usage?: { input_tokens: number; output_tokens: number; cache_read_tokens: number; reasoning_tokens?: number };
}

export interface Roles {
  tactical: string | null;
  strategic: string | null;
  chooser: string | null;
  review: string | null;
}

export interface Settings {
  roles: Roles;
  agent: { use_guides: boolean; max_tactical_calls_per_stage: number; max_run_minutes: number };
  server: { host: string; port: number };
  knowledge: { max_lineups: number; request_interval: number };
}

export interface KnowledgeMeta {
  season_id?: string;
  sub_season_id?: string;
  game_version?: string;
  synced_at?: string;
  counts?: Record<string, number>;
  warnings?: string[];
}

export interface SyncState {
  running: boolean;
  stage: string;
  message: string;
  log: string[];
  result: Record<string, unknown> | null;
  error: string | null;
}

export type Item = Record<string, any> & { id: string; name: string; icon?: string };

export interface Memory {
  id: string;
  kind: '机制' | '打法' | '教训';
  title: string;
  content: string;
  triggers: { entities: string[]; nodes: string[]; screens: string[] };
  scope: string[];
  status: 'draft' | 'approved' | 'rejected' | 'archived';
  confidence: 'high' | 'medium' | 'low';
  source: string;
  evidence: string[];
  stats: { injected: number; helped: number; hurt: number };
  created: string;
  updated: string;
  origin?: string;
}

export const MEMORY_STATUS: Record<Memory['status'], string> = {
  draft: '待审核',
  approved: '已采纳',
  rejected: '驳回',
  archived: '已归档',
};
export const CONFIDENCE: Record<Memory['confidence'], string> = { high: '已核实', medium: '有依据未核实', low: '推测' };
