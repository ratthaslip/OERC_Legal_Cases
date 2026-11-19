import { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, CheckCircle2, Database, KeyRound, Lock, PlayCircle, PlugZap, Power, RefreshCw, RotateCcw, Save, Trash2, XCircle
} from 'lucide-react';
import { api } from '../api.js';
import { Card, ErrorBox, Label, Spinner, inputCls } from '../components/ui.jsx';

export const SourceTag = ({ src }) => (
  <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded ${src === 'db' ? 'bg-brand-100 text-brand-800' : 'bg-slate-100 text-slate-500'}`}>
    {src === 'db' ? 'ตั้งจากหน้านี้' : 'จาก .env'}
  </span>
);

const PROVIDERS = {
  rerank: {
    none: 'none — ไม่ rerank',
    legal: 'legal — คุณลักษณะทางกฎหมายในระบบ (ไม่เรียกโมเดล)',
    tei: 'TEI — Text Embeddings Inference (/rerank)',
    cohere: 'Cohere-compatible — /v1/rerank (Cohere, vLLM, Infinity)'
  },
  embed: {
    hash: 'hash — ในเครื่อง ไม่เรียกโมเดล (ค่าเริ่มต้น)',
    openai_compat: 'OpenAI-compatible — /embeddings (Ollama, vLLM, TEI, OpenAI)'
  }
};
const USES_URL = { rerank: (p) => p === 'tei' || p === 'cohere', embed: (p) => p === 'openai_compat' };
const FIELDS = { rerank: ['provider', 'base_url', 'model', 'candidates', 'model_weight', 'timeout_s'], embed: ['provider', 'base_url', 'model', 'timeout_s'] };
const GUARD = {
  rerank: ['reranker ภายนอก — ส่งให้โมเดลเฉพาะตัวบทกฎหมายสาธารณะ ผลค้นที่เป็นทะเบียน ตราสารภายใน และสำนวน ใช้คะแนนเดิม',
    'reranker ภายในเครือข่าย หรือไม่เรียกโมเดล — จัดอันดับผลค้นทุกประเภทที่ผู้ค้นมีสิทธิ์'],
  embed: ['โมเดลภายนอก — index ใหม่สร้างเวกเตอร์เฉพาะตัวบทกฎหมายสาธารณะ ข้อมูลภายในค้นด้วยคำตามเดิม (คำค้นถูกส่งไปฝังเวกเตอร์)',
    'โมเดลภายในเครือข่าย หรือ hash — สร้างเวกเตอร์ให้เนื้อหาทุกประเภท (การค้นยังกรองตามสิทธิ์เสมอ)']
};

function TestResult({ test }) {
  if (!test) return null;
  return (
    <div className={`p-3 rounded-lg text-xs ${test.ok ? 'bg-emerald-50 border border-emerald-200' : 'bg-rose-50 border border-rose-200'}`}>
      <div className="font-semibold flex items-center gap-1.5">
        {test.ok ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <XCircle className="w-4 h-4 text-rose-600" />}
        {test.ok ? `เชื่อมต่อได้ — ${test.latency_ms} ms` : 'เชื่อมต่อไม่ได้'}
        <span className="font-normal text-slate-500">({test.provider}{test.model ? ` · ${test.model}` : ''})</span>
      </div>
      {test.sample && <div className="mt-1 text-slate-700">{test.sample}</div>}
      {test.error && <div className="mt-1 text-rose-800 break-all">{test.error}</div>}
    </div>
  );
}

/** ฟอร์มค่าตั้ง reranker / embedding (โครงเดียวกับ LLM) */
export function ModelSection({ name, notify, onLoaded, children }) {
  const [cfg, setCfg] = useState(null);
  const [form, setForm] = useState({});
  const [key, setKey] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState(null);

  const load = (v) => {
    setCfg(v); onLoaded?.(v);
    setForm(Object.fromEntries(FIELDS[name].map((k) => [k, v[k]])));
    setKey('');
  };
  useEffect(() => { api.adminModel(name).then(load).catch(setError); }, [name]);

  const run = async (fn, msg) => {
    setBusy(true); setError(null);
    try { const v = await fn(); if (v) load(v); if (msg) notify?.(msg); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const changed = () => Object.fromEntries(Object.entries(form).filter(([k, v]) => String(v) !== String(cfg[k])));
  const save = () => {
    const body = { ...changed(), ...(key ? { api_key: key } : {}) };
    if (!Object.keys(body).length) return notify?.('ไม่มีค่าที่เปลี่ยน');
    return run(() => api.saveAdminModel(name, body), 'บันทึกแล้ว — มีผลภายใน 10 วินาที');
  };
  const doTest = async () => {
    setBusy(true); setTest(null);
    try { setTest(await api.testAdminModel(name)); } catch (e) { setError(e); } finally { setBusy(false); }
  };

  if (error && !cfg) return <ErrorBox error={error} />;
  if (!cfg) return <Spinner />;
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const usesUrl = USES_URL[name](form.provider);

  return (
    <Card className="p-5 space-y-4">
      <div>
        <Label>ผู้ให้บริการ <SourceTag src={cfg.source.provider} /></Label>
        <select className={inputCls} value={form.provider} onChange={set('provider')}>
          {cfg.providers.map((p) => <option key={p} value={p}>{PROVIDERS[name][p] || p}</option>)}
        </select>
      </div>

      {usesUrl && (
        <>
          <div>
            <Label>Endpoint <SourceTag src={cfg.source.base_url} /></Label>
            {cfg.allowed_base_urls.length > 0 ? (
              <select className={inputCls} value={form.base_url} onChange={set('base_url')}>
                {[...new Set([form.base_url, ...cfg.allowed_base_urls])].map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            ) : (
              <input className={inputCls} value={form.base_url} onChange={set('base_url')}
                placeholder={name === 'embed' ? 'http://host.docker.internal:11434/v1' : 'http://reranker:80'} />
            )}
            <p className="text-[11px] text-slate-500 mt-1">
              {name === 'embed' ? 'ระบบต่อท้าย /embeddings ให้เอง' : form.provider === 'tei' ? 'ระบบต่อท้าย /rerank ให้เอง' : 'ระบบต่อท้าย /v1/rerank ให้เอง'}
            </p>
          </div>
          <div className="grid sm:grid-cols-3 gap-3">
            <div className="sm:col-span-2">
              <Label>ชื่อโมเดล <SourceTag src={cfg.source.model} /></Label>
              <input className={inputCls} value={form.model} onChange={set('model')} />
            </div>
            <div>
              <Label>Timeout (วินาที) <SourceTag src={cfg.source.timeout_s} /></Label>
              <input type="number" className={inputCls} value={form.timeout_s} onChange={set('timeout_s')} />
            </div>
          </div>
          <div>
            <Label>
              API key <SourceTag src={cfg.source.api_key} />
              {cfg.has_key && <span className="ml-2 text-[11px] text-slate-500">มีคีย์แล้ว {cfg.key_hint || ''}</span>}
            </Label>
            <div className="flex gap-2">
              <input type="password" autoComplete="new-password" className={inputCls} value={key} onChange={(e) => setKey(e.target.value)}
                placeholder={cfg.has_key ? 'เว้นว่าง = ใช้คีย์เดิม' : 'ไม่ต้องใส่ถ้าเป็นบริการภายใน'} disabled={!cfg.secret_configured} />
              {cfg.source.api_key === 'db' && (
                <button onClick={() => run(() => api.saveAdminModel(name, { api_key: '' }), 'ลบคีย์แล้ว')} disabled={busy}
                  className="px-3 text-xs rounded-lg border border-slate-300 hover:bg-slate-50 whitespace-nowrap">ลบคีย์</button>
              )}
            </div>
            <p className="text-[11px] text-slate-500 mt-1 flex items-center gap-1">
              <KeyRound className="w-3 h-3" />
              {cfg.secret_configured ? 'เข้ารหัสก่อนเก็บ และไม่แสดงกลับบนหน้าจอ' : 'ตั้ง CONFIG_SECRET ใน .env ก่อน จึงจะบันทึก API key จากหน้านี้ได้'}
            </p>
          </div>
        </>
      )}

      {name === 'rerank' && form.provider !== 'none' && (
        <div className="grid sm:grid-cols-2 gap-3">
          <div>
            <Label>จำนวนผู้สมัครที่จัดอันดับใหม่ <SourceTag src={cfg.source.candidates} /></Label>
            <input type="number" min="5" max="100" className={inputCls} value={form.candidates} onChange={set('candidates')} />
          </div>
          {usesUrl && (
            <div>
              <Label>น้ำหนักคะแนนโมเดล (0–1) <SourceTag src={cfg.source.model_weight} /></Label>
              <input type="number" min="0" max="1" step="0.05" className={inputCls} value={form.model_weight} onChange={set('model_weight')} />
            </div>
          )}
        </div>
      )}

      <div className={`p-3 rounded-lg text-xs flex gap-2 ${cfg.external ? 'bg-amber-50 border border-amber-300 text-amber-900' : 'bg-emerald-50 border border-emerald-200 text-emerald-900'}`}>
        {cfg.external ? <AlertTriangle className="w-4 h-4 shrink-0" /> : <Lock className="w-4 h-4 shrink-0" />}
        {GUARD[name][cfg.external ? 0 : 1]} (ค่าที่บันทึกแล้ว)
      </div>

      {error && <ErrorBox error={error} />}

      <div className="flex flex-wrap gap-2 pt-1">
        <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
          <Save className="w-4 h-4" /> บันทึก
        </button>
        <button onClick={doTest} disabled={busy} className="px-4 py-2 rounded-lg border border-brand-300 text-brand-700 hover:bg-brand-50 text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
          <PlugZap className="w-4 h-4" /> ทดสอบ (ข้อความสมมติ)
        </button>
        <button onClick={() => run(() => api.resetAdminModel(name), 'กลับไปใช้ค่าจาก .env แล้ว')} disabled={busy}
          className="ml-auto px-3 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 text-xs flex items-center gap-1.5">
          <RotateCcw className="w-3.5 h-3.5" /> กลับไปใช้ค่าจาก .env
        </button>
      </div>
      <TestResult test={test} />
      {children}
      {cfg.updated_by && (
        <p className="text-[11px] text-slate-400">แก้ไขล่าสุดโดย {cfg.updated_by} · {new Date(cfg.updated_at).toLocaleString('th-TH')} · ทุกการแก้ไขบันทึกใน audit log</p>
      )}
    </Card>
  );
}

const STATUS_TH = { building: 'กำลังสร้าง', ready: 'พร้อมใช้', failed: 'ล้มเหลว' };
const STATUS_CLS = { building: 'bg-sky-100 text-sky-800', ready: 'bg-emerald-100 text-emerald-800', failed: 'bg-rose-100 text-rose-800' };

function Meter({ value, total }) {
  const pct = total ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <div className="flex items-center gap-2" title={`${value.toLocaleString()} / ${total.toLocaleString()}`}>
      <div className="flex-1 h-1.5 rounded-full bg-slate-200 overflow-hidden">
        <div className="h-full rounded-full bg-brand-600" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] text-slate-600 tabular-nums w-24 text-right">{value.toLocaleString()} / {total.toLocaleString()}</span>
    </div>
  );
}

/** embedding index: สร้างใหม่จากค่าที่บันทึก, อัปเดตหลัง seed, สลับใช้, ลบ */
export function EmbedIndexes({ notify }) {
  const [v, setV] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  const refresh = () => api.adminModel('embed').then(setV).catch(setError);
  useEffect(() => { refresh(); return () => clearTimeout(timer.current); }, []);
  // มีงานกำลังสร้าง → โหลดความคืบหน้าทุก 3 วินาที
  useEffect(() => {
    clearTimeout(timer.current);
    if (v?.indexes?.some((i) => i.status === 'building')) timer.current = setTimeout(refresh, 3000);
  }, [v]);

  const run = async (fn, msg) => {
    setBusy(true); setError(null);
    try { setV(await fn()); if (msg) notify?.(msg); } catch (e) { setError(e); } finally { setBusy(false); }
  };

  if (!v) return error ? <ErrorBox error={error} /> : <Spinner />;
  const canCreate = v.provider !== 'hash';

  return (
    <Card className="p-5 space-y-3">
      <div className="flex items-center gap-2">
        <Database className="w-4 h-4 text-brand-600" />
        <h3 className="font-semibold text-slate-900 text-sm">Embedding index</h3>
        <span className="text-[11px] text-slate-500">
          {v.active_index ? `ใช้ index #${v.active_index}` : 'ใช้ hash ตั้งต้น (ยังไม่มี index ที่ใช้งาน)'}
          {' · '}{v.pgvector ? 'pgvector (HNSW)' : 'ไม่มี pgvector — คำนวณในหน่วยความจำ'}
        </span>
      </div>
      <p className="text-xs text-slate-600">
        เปลี่ยนโมเดลแล้วกด “สร้าง index ใหม่” — ระบบสร้างเวกเตอร์เบื้องหลังและสลับใช้เมื่อพร้อม ระหว่างนี้ค้นด้วย index เดิม
        หลัง seed ข้อมูลใหม่ กด “อัปเดต” เพื่อสร้างเวกเตอร์เฉพาะเนื้อหาที่เพิ่มขึ้น (หรือ seed ทำให้อัตโนมัติ)
      </p>
      <div className="flex flex-wrap gap-2">
        <button onClick={() => run(() => api.createEmbedIndex(true), 'เริ่มสร้าง index ใหม่แล้ว')} disabled={busy || !canCreate}
          className="px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
          title={canCreate ? `${v.model} @ ${v.base_url}` : 'เลือกผู้ให้บริการ OpenAI-compatible และบันทึกก่อน'}>
          <PlayCircle className="w-3.5 h-3.5" /> สร้าง index ใหม่จากค่าที่บันทึก
        </button>
        {v.active_index && (
          <button onClick={() => run(() => api.deactivateEmbedIndex(), 'กลับไปใช้ hash แล้ว')} disabled={busy}
            className="px-3 py-1.5 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 text-xs flex items-center gap-1.5">
            <Power className="w-3.5 h-3.5" /> เลิกใช้ index (กลับไป hash)
          </button>
        )}
      </div>
      {error && <ErrorBox error={error} />}

      {v.indexes.length === 0 ? (
        <p className="text-xs text-slate-400">ยังไม่มี index</p>
      ) : (
        <div className="divide-y divide-slate-100 border border-slate-200 rounded-lg">
          {v.indexes.map((i) => (
            <div key={i.index_id} className="p-3 space-y-1.5">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-semibold text-slate-800">#{i.index_id}</span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] ${STATUS_CLS[i.status]}`}>{STATUS_TH[i.status] || i.status}</span>
                {i.active && <span className="px-1.5 py-0.5 rounded text-[10px] bg-brand-600 text-white">ใช้งานอยู่</span>}
                {i.external && <span className="px-1.5 py-0.5 rounded text-[10px] bg-amber-100 text-amber-900">ภายนอก · เฉพาะตัวบทสาธารณะ</span>}
                <span className="text-slate-600">{i.model}</span>
                <span className="text-slate-400 break-all">{i.base_url}</span>
                {i.dim && <span className="text-slate-400">{i.dim} มิติ{i.ann ? ' · HNSW' : ''}</span>}
                <span className="ml-auto flex gap-1">
                  {i.status !== 'building' && (
                    <button onClick={() => run(() => api.updateEmbedIndex(i.index_id), 'เริ่มอัปเดตแล้ว')} disabled={busy}
                      className="p-1 rounded hover:bg-slate-100" title="อัปเดต / ทำต่อ (สร้างเฉพาะเนื้อหาที่ยังไม่มี)">
                      <RefreshCw className="w-3.5 h-3.5 text-slate-600" />
                    </button>
                  )}
                  {i.status === 'ready' && !i.active && (
                    <button onClick={() => run(() => api.activateEmbedIndex(i.index_id), `ใช้ index #${i.index_id} แล้ว`)} disabled={busy}
                      className="px-2 py-0.5 rounded border border-brand-300 text-brand-700 hover:bg-brand-50 text-[11px]">ใช้ index นี้</button>
                  )}
                  {!i.active && i.status !== 'building' && (
                    <button onClick={() => run(() => api.deleteEmbedIndex(i.index_id), `ลบ index #${i.index_id} แล้ว`)} disabled={busy}
                      className="p-1 rounded hover:bg-rose-50" title="ลบ index และเวกเตอร์">
                      <Trash2 className="w-3.5 h-3.5 text-rose-600" />
                    </button>
                  )}
                </span>
              </div>
              <Meter value={i.coverage.embedded} total={i.coverage.eligible} />
              <div className="text-[11px] text-slate-500">
                เนื้อหาที่มีเวกเตอร์ {i.coverage.embedded.toLocaleString()} จาก {i.coverage.eligible.toLocaleString()}
                {i.coverage.missing > 0 && i.status !== 'building' && <> · ขาด {i.coverage.missing.toLocaleString()} (กดอัปเดต)</>}
                {i.coverage.withheld > 0 && <> · งดส่ง {i.coverage.withheld.toLocaleString()} รายการที่เป็นข้อมูลภายใน</>}
                {' · '}สร้างโดย {i.created_by} {new Date(i.created_at).toLocaleString('th-TH')}
              </div>
              {i.error && <div className="text-[11px] text-rose-700 break-all">{i.error}</div>}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
