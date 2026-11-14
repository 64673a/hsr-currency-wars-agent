// 后端接口(docs/TECH_DESIGN.md 第 13 节)的调用封装和数据类型。

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc ?? []).filter((x: unknown) => x !== 'body').join('.')}: ${d.msg}`).join(';');
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

export const enc = encodeURIComponent;

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function parseJsonObject(text: string, what: string): Record<string, any> {
  if (!text.trim()) return {};
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    throw new Error(`${what}不是合法 JSON:${errorText(e)}`);
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${what}必须是 JSON 对象`);
  return value as Record<string, any>;
}

export function pretty(value: unknown): string {
  if (!value || (typeof value === 'object' && Object.keys(value as object).length === 0)) return '';
  return JSON.stringify(value, null, 2);
}

export function splitList(text: string): string[] {
  return text.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
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
  context_window: number | null;
}

export interface TestResult {
  ok: boolean;
  error?: string;
  tool_call?: boolean;
  reasoning?: boolean;
  model?: string;
  latency_s?: number;
  usage?: { input_tokens: number; output_tokens: number };
}

export type RoleKey = 'strategic' | 'tactical' | 'chooser' | 'review';

export interface Settings {
  roles: Record<RoleKey, string | null>;
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

export const MEMORY_STATUS: Record<Memory['status'], string> = { draft: '待审核', approved: '已采纳', rejected: '已驳回', archived: '已归档' };
export const CONFIDENCE: Record<Memory['confidence'], string> = { high: '已核实', medium: '有依据未核实', low: '推测' };

// ---------------------------------------------------------------- 对局

export interface PlayStatus {
  available: boolean;
  running: boolean;
  hotkey: string | null;
  stopping: boolean;
  run_id: string | null;
  profile: string | null;
  difficulty: string | null;
  round: number;
  rounds: number;
  result: string | null;
  error: string | null;
  log: string[];
}

export interface RunSummary {
  id: string;
  profile: string;
  model: string;
  difficulty: string;
  started_at: string;
  ended_at: string | null;
  result: string | null;
  live: boolean;
  steps: number;
  node: string | null;
  outcome: string | null;
  decisions: number;
}

export interface StepMark {
  type: 'click' | 'drag' | 'scroll';
  x: number;
  y: number;
  x2?: number;
  y2?: number;
}

export interface StepDecision {
  analysis: string;
  actions: { type: string; text?: string; reason?: string }[];
  run_notes: string | null;
  reasoning: string;
  error: string;
  latency_s: number | null;
  model: string;
  usage: { input_tokens?: number; output_tokens?: number; cache_read_tokens?: number } | null;
}

export interface StepState {
  hud?: { node?: string; hp?: number; gold?: number; level?: number; exp?: string; deployed?: string };
  board?: { zone: 'front' | 'back' | 'bench'; index: number; name: string | null; stars: number; score: number }[];
}

export interface StepRecord {
  step: number;
  ts?: string;
  screen?: string;
  node?: string | null;
  shot?: string;
  texts?: string[];
  decision?: StepDecision;
  actions?: string[];
  marks?: StepMark[];
  state?: StepState;
  battle?: { av: number | null; progress: number | null };
  battle_summary?: string;
  plane_nodes?: string;
  note?: string;
  error?: string;
}
