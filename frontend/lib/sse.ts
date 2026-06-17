export type SSEEvent = { event: string; data: any };

export async function* streamSSE(
  url: string,
  init: RequestInit,
): AsyncGenerator<SSEEvent> {
  const res = await fetch(url, init);
  if (res.status === 401) throw new Error("unauthorized");
  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => "");
    throw new Error(`stream failed: ${res.status} ${t.slice(0, 200)}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // Normalize line endings so the framing logic only needs to look for "\n\n".
    buf = buf.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = parseEvent(raw);
      if (ev) yield ev;
    }
  }
  // Flush any trailing event that didn't end in a blank line.
  buf += decoder.decode();
  if (buf.trim()) {
    const ev = parseEvent(buf);
    if (ev) yield ev;
  }
}

function parseEvent(raw: string): SSEEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line) continue;
    // SSE comments begin with ":".
    if (line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let val = colon === -1 ? "" : line.slice(colon + 1);
    // Spec says strip a single leading space, not all whitespace.
    if (val.startsWith(" ")) val = val.slice(1);
    if (field === "event") event = val;
    else if (field === "data") dataLines.push(val);
    // "id" and "retry" are intentionally ignored — we don't reconnect.
  }
  if (!dataLines.length) return null;
  const text = dataLines.join("\n");
  try { return { event, data: JSON.parse(text) }; }
  catch { return { event, data: text }; }
}
