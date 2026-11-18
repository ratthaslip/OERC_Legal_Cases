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
  appealWorkflow: () => request('GET', '/appeals/workflow'),
  register: (params) => request('GET', '/laws/register', { params }),
  registerItem: (id) => request('GET', `/laws/register/${id}`),
  coverage: () => request('GET', '/laws/coverage'),
  adminLlm: () => request('GET', '/admin/llm'),
  saveAdminLlm: (body) => request('PUT', '/admin/llm', { body }),
  resetAdminLlm: () => request('DELETE', '/admin/llm'),
  testAdminLlm: () => request('POST', '/admin/llm/test'),
  adminModel: (name) => request('GET', `/admin/${name}`),
  saveAdminModel: (name, body) => request('PUT', `/admin/${name}`, { body }),
  resetAdminModel: (name) => request('DELETE', `/admin/${name}`),
  testAdminModel: (name) => request('POST', `/admin/${name}/test`),
  createEmbedIndex: (activate_when_ready = true) => request('POST', '/admin/embed/indexes', { body: { activate_when_ready } }),
  updateEmbedIndex: (id) => request('POST', `/admin/embed/indexes/${id}/update`),
  activateEmbedIndex: (id) => request('POST', `/admin/embed/indexes/${id}/activate`),
  deactivateEmbedIndex: () => request('POST', '/admin/embed/deactivate'),
  deleteEmbedIndex: (id) => request('DELETE', `/admin/embed/indexes/${id}`),
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

/** ดาวน์โหลดรายการทะเบียนเป็น Excel (ค่าเริ่มต้น: ยังไม่มีตัวบทในระบบ) */
export async function downloadRegisterExport(params = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''));
  const res = await fetch(`/api/v1/laws/register/export?${qs}`, { headers: { 'X-Demo-User': currentUser } });
  if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null));
  const name = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'register.xlsx';
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** เปิดไฟล์ต้นฉบับ (PDF) ในแท็บใหม่ — ต้องส่ง header ผู้ใช้ จึงโหลดเป็น blob ก่อน */
export async function openRawFile(sha) {
  const win = window.open('', '_blank');
  const res = await fetch(`/api/v1/raw/${sha}`, { headers: { 'X-Demo-User': currentUser } });
  if (!res.ok) {
    win?.close();
    throw new ApiError(res.status, await res.json().catch(() => null));
  }
  const url = URL.createObjectURL(await res.blob());
  if (win) win.location.href = url;
  else window.location.href = url;
}
