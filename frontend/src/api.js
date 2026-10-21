// เรียก API ของ backend — ผู้ใช้จำลองส่งผ่าน header X-Demo-User (ระบบจริงใช้ token จาก SSO แทน)
let currentUser = 'lgl1';
export const setApiUser = (u) => { currentUser = u; };

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.detail && typeof body.detail === 'string' ? body.detail : `HTTP ${status}`);
    this.status = status;
    this.code = body?.code;
  }
}

async function request(method, path, { params, body, headers } = {}) {
  const qs = params
    ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString()
    : '';
  const res = await fetch(`/api/v1${path}${qs}`, {
    method,
    headers: { 'X-Demo-User': currentUser, ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export const api = {
  me: () => request('GET', '/me'),
  instruments: (category) => request('GET', '/laws/instruments', { params: { category } }),
  searchLaws: (q, as_of) => request('GET', '/laws/search', { params: { q, as_of } }),
  provision: (eli, as_of) => request('GET', `/laws/provisions/${eli}`, { params: { as_of } }),
  appeals: (params) => request('GET', '/appeals', { params }),
  appeal: (id) => request('GET', `/appeals/${id}`),
  registerAppeal: (body) => request('POST', '/appeals', { body }),
  assign: (id, v, body) => request('POST', `/appeals/${id}:assign`, { body, headers: { 'If-Match': String(v) } }),
  completeStep: (id, v, step, body) =>
    request('POST', `/appeals/${id}/steps/${step}:complete`, { body, headers: { 'If-Match': String(v) } }),
  revert: (id, v, reason) => request('POST', `/appeals/${id}/steps:revert`, { body: { reason }, headers: { 'If-Match': String(v) } }),
  milestone: (id, v, body) => request('POST', `/appeals/${id}/milestones`, { body, headers: { 'If-Match': String(v) } }),
  addRequest: (id, v, body) => request('POST', `/appeals/${id}/requests`, { body, headers: { 'If-Match': String(v) } }),
  respondRequest: (id, v, rid, body) =>
    request('POST', `/appeals/${id}/requests/${rid}:respond`, { body, headers: { 'If-Match': String(v) } }),
  cases: (params) => request('GET', '/court-cases', { params }),
  courtCase: (id) => request('GET', `/court-cases/${id}`),
  assistantStatus: () => request('GET', '/assistant/status')
};

/** ถามผู้ช่วยแบบ streaming (Server-Sent Events) — onEvent(name, data) ถูกเรียกทุก event */
export async function askAssistant(question, asOf, onEvent, signal) {
  const res = await fetch('/api/v1/assistant/answers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Demo-User': currentUser },
    body: JSON.stringify({ question, as_of: asOf || null }),
    signal
  });
  if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null));
  if (!res.body) throw new Error('เบราว์เซอร์ไม่รองรับการรับคำตอบแบบ streaming');
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let gotDone = false;
  const emit = (block) => {
    const name = /^event: ?(.*)$/m.exec(block)?.[1]?.trim();
    const raw = /^data: ?(.*)$/m.exec(block)?.[1];
    if (!name || raw == null) return;
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return; // ข้าม event ที่ไม่ใช่ JSON (เช่น proxy แทรก keep-alive)
    }
    if (name === 'done') gotDone = true;
    if (name === 'error') throw new Error(data?.detail || 'เกิดข้อผิดพลาดระหว่างสร้างคำตอบ');
    onEvent(name, data);
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      emit(block);
    }
  }
  if (buf.trim()) emit(buf);
  if (!gotDone) throw new Error('การเชื่อมต่อกับเซิร์ฟเวอร์ขาดก่อนได้คำตอบครบ (ดู docker compose logs api)');
}
