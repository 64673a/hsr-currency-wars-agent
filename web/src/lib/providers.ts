export function isOpenCodeGo(baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl.trim());
    return url.hostname === 'opencode.ai' && ['/zen/go', '/inference/go'].some(
      (prefix) => url.pathname === prefix || url.pathname.startsWith(`${prefix}/`),
    );
  } catch {
    return false;
  }
}
