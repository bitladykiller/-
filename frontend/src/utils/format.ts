export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 轻量安全格式化：换行、粗体、行内代码 */
export function formatMessageHtml(text: string): string {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\n/g, "<br/>");
  return s;
}

export function formatTime(iso: string): string {
  const t = iso.replace("T", " ");
  return t.length > 16 ? t.slice(0, 16) : t;
}
