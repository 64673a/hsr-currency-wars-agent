// 小工具:提示条、JSON 文本框解析。

export type ToastVariant = 'info' | 'success' | 'warning' | 'error';

// arc-toast 支持文档级事件,页面上放一个 <Toast /> 即可在任何地方弹出提示
export function toast(message: string, variant: ToastVariant = 'info') {
  document.dispatchEvent(new CustomEvent('arc-toast', { detail: { message, variant } }));
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function parseJsonObject(text: string, what: string): Record<string, any> {
  if (!text.trim()) return {};
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    throw new Error(`${what} 不是合法 JSON:${errorText(e)}`);
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${what} 必须是 JSON 对象`);
  return value as Record<string, any>;
}

export function pretty(value: unknown): string {
  if (!value || (typeof value === 'object' && Object.keys(value as object).length === 0)) return '';
  return JSON.stringify(value, null, 2);
}

export function splitList(text: string): string[] {
  return text
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}
