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

/** 毫秒时间戳 → HH:MM，用于消息侧的时刻铭牌 */
export function formatClock(ts: number): string {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}
