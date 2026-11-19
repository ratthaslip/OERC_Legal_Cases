import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, KeyRound, Lock, PlugZap, RotateCcw, Save, Settings, XCircle } from 'lucide-react';
import { api } from '../api.js';
import { Card, ErrorBox, Label, Spinner, inputCls } from '../components/ui.jsx';

const PROVIDER_TH = {
  extractive: 'extractive — ไม่เรียกโมเดล (ยกข้อความจากตัวบท)',
  openai_compat: 'OpenAI-compatible — OpenAI, Azure OpenAI, vLLM, Ollama',
  anthropic: 'Anthropic API'
};

const SourceTag = ({ src }) => (
  <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded ${src === 'db' ? 'bg-brand-100 text-brand-800' : 'bg-slate-100 text-slate-500'}`}>
    {src === 'db' ? 'ตั้งจากหน้านี้' : 'จาก .env'}
  </span>
);

/** หน้าตั้งค่าผู้ดูแลระบบ: LLM endpoint / model / API key */
export default function AdminPage({ notify }) {
  const [cfg, setCfg] = useState(null);
  const [form, setForm] = useState({});
  const [key, setKey] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState(null);

  const load = (v) => {
    setCfg(v);
    setForm({ provider: v.provider, base_url: v.base_url, model: v.model, timeout_s: v.timeout_s });
    setKey('');
  };
  useEffect(() => { api.adminLlm().then(load).catch(setError); }, []);

  const run = async (fn, msg) => {
    setBusy(true); setError(null);
    try { const v = await fn(); if (v) load(v); if (msg) notify?.(msg); } catch (e) { setError(e); } finally { setBusy(false); }
  };

  // ส่งเฉพาะช่องที่เปลี่ยน — ช่องที่ไม่แตะยังตามค่า .env ต่อไป
  const changed = () => Object.fromEntries(Object.entries(form).filter(([k, v]) => String(v) !== String(cfg[k])));
  const save = () => {
    const body = { ...changed(), ...(key ? { api_key: key } : {}) };
    if (!Object.keys(body).length) return notify?.('ไม่มีค่าที่เปลี่ยน');
    return run(() => api.saveAdminLlm(body), 'บันทึกค่าตั้ง LLM แล้ว — มีผลภายใน 10 วินาที');
  };
  const clearKey = () => run(() => api.saveAdminLlm({ api_key: '' }), 'ลบ API key ที่ตั้งจากหน้านี้แล้ว');
  const reset = () => run(() => api.resetAdminLlm(), 'กลับไปใช้ค่าจาก .env แล้ว');
  const doTest = async () => {
    setBusy(true); setTest(null);
    try { setTest(await api.testAdminLlm()); } catch (e) { setError(e); } finally { setBusy(false); }
  };

  if (error && !cfg) return <ErrorBox error={error} />;
  if (!cfg) return <Spinner />;
  const external = cfg.external;
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2">
        <Settings className="w-5 h-5 text-brand-600" />
        <h2 className="text-lg font-bold text-slate-900">ตั้งค่าระบบ — โมเดลภาษา (LLM)</h2>
      </div>

      <Card className="p-5 space-y-4">
        <div>
          <Label>ผู้ให้บริการ <SourceTag src={cfg.source.provider} /></Label>
          <select className={inputCls} value={form.provider} onChange={set('provider')}>
            {cfg.providers.map((p) => <option key={p} value={p}>{PROVIDER_TH[p] || p}</option>)}
          </select>
        </div>

        {form.provider !== 'extractive' && (
          <>
            <div>
              <Label>Endpoint (ถึง /v1) <SourceTag src={cfg.source.base_url} /></Label>
              {cfg.allowed_base_urls.length > 0 ? (
                <select className={inputCls} value={form.base_url} onChange={set('base_url')}>
                  {[...new Set([form.base_url, ...cfg.allowed_base_urls])].map((u) => <option key={u} value={u}>{u}</option>)}
                </select>
              ) : (
                <input className={inputCls} value={form.base_url} onChange={set('base_url')} placeholder="https://api.openai.com/v1" />
              )}
              <p className="text-[11px] text-slate-500 mt-1">ระบบต่อท้าย /chat/completions ให้เอง (OpenAI-compatible)</p>
            </div>
            <div className="grid sm:grid-cols-3 gap-3">
              <div className="sm:col-span-2">
                <Label>ชื่อโมเดล <SourceTag src={cfg.source.model} /></Label>
                <input className={inputCls} value={form.model} onChange={set('model')} />
              </div>
              <div>
                <Label>Timeout (วินาที) <SourceTag src={cfg.source.timeout_s} /></Label>
                <input type="number" min="5" max="600" className={inputCls} value={form.timeout_s} onChange={set('timeout_s')} />
              </div>
            </div>
            <div>
              <Label>
                API key <SourceTag src={cfg.source.api_key} />
                {cfg.has_key && <span className="ml-2 text-[11px] text-slate-500">มีคีย์แล้ว {cfg.key_hint || ''}</span>}
              </Label>
              <div className="flex gap-2">
                <input type="password" autoComplete="new-password" className={inputCls} value={key} onChange={(e) => setKey(e.target.value)}
                  placeholder={cfg.has_key ? 'เว้นว่าง = ใช้คีย์เดิม' : 'sk-…'} disabled={!cfg.secret_configured} />
                {cfg.source.api_key === 'db' && (
                  <button onClick={clearKey} disabled={busy} className="px-3 text-xs rounded-lg border border-slate-300 hover:bg-slate-50 whitespace-nowrap">ลบคีย์</button>
                )}
              </div>
              <p className="text-[11px] text-slate-500 mt-1 flex items-center gap-1">
                <KeyRound className="w-3 h-3" />
                {cfg.secret_configured
                  ? 'เข้ารหัสก่อนเก็บ และไม่แสดงกลับบนหน้าจอ'
                  : 'ตั้ง CONFIG_SECRET ใน .env ก่อน จึงจะบันทึก API key จากหน้านี้ได้ (ระหว่างนี้ใช้ LLM_API_KEY ใน .env)'}
              </p>
            </div>
          </>
        )}

        <div className={`p-3 rounded-lg text-xs flex gap-2 ${external ? 'bg-amber-50 border border-amber-300 text-amber-900' : 'bg-emerald-50 border border-emerald-200 text-emerald-900'}`}>
          {external ? <AlertTriangle className="w-4 h-4 shrink-0" /> : <Lock className="w-4 h-4 shrink-0" />}
          {external
            ? 'LLM ภายนอก (ค่าที่บันทึกแล้ว) — ระบบส่งให้ LLM เฉพาะตัวบทกฎหมายสาธารณะ ข้อมูลทะเบียน ตราสารภายใน และสำนวน ตอบด้วยการยกข้อความแทน'
            : 'LLM ภายในเครือข่าย หรือไม่เรียกโมเดล (ค่าที่บันทึกแล้ว) — ส่งแหล่งอ้างอิงทุกประเภทที่ผู้ถามมีสิทธิ์'}
        </div>

        {error && <ErrorBox error={error} />}

        <div className="flex flex-wrap gap-2 pt-1">
          <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
            <Save className="w-4 h-4" /> บันทึก
          </button>
          <button onClick={doTest} disabled={busy} className="px-4 py-2 rounded-lg border border-brand-300 text-brand-700 hover:bg-brand-50 text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
            <PlugZap className="w-4 h-4" /> ทดสอบการเชื่อมต่อ (ค่าที่บันทึกแล้ว)
          </button>
          <button onClick={reset} disabled={busy} className="ml-auto px-3 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 text-xs flex items-center gap-1.5">
            <RotateCcw className="w-3.5 h-3.5" /> กลับไปใช้ค่าจาก .env
          </button>
        </div>

        {test && (
          <div className={`p-3 rounded-lg text-xs ${test.ok ? 'bg-emerald-50 border border-emerald-200' : 'bg-rose-50 border border-rose-200'}`}>
            <div className="font-semibold flex items-center gap-1.5">
              {test.ok ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <XCircle className="w-4 h-4 text-rose-600" />}
              {test.ok ? `เชื่อมต่อได้ — ${test.latency_ms} ms` : 'เชื่อมต่อไม่ได้'}
              <span className="font-normal text-slate-500">({test.provider}{test.model ? ` · ${test.model}` : ''})</span>
            </div>
            {test.sample && <div className="mt-1 text-slate-700">คำตอบ: {test.sample}</div>}
            {test.error && <div className="mt-1 text-rose-800 break-all">{test.error}</div>}
          </div>
        )}

        {cfg.updated_by && (
          <p className="text-[11px] text-slate-400">แก้ไขล่าสุดโดย {cfg.updated_by} · {new Date(cfg.updated_at).toLocaleString('th-TH')} · ทุกการแก้ไขบันทึกใน audit log</p>
        )}
      </Card>
    </div>
  );
}
