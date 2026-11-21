import { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, ArrowLeft, CheckCircle2, CornerUpLeft, ExternalLink, FileText, FileUp, Loader2, Lock, PencilLine, Plus,
  RefreshCw, Save, ScanText, Search, Send, Trash2, Undo2, XCircle
} from 'lucide-react';
import { api, openRawFile, uploadLawFile } from '../api.js';
import { Card, ErrorBox, Label, Spinner, inputCls, thDate, useDebounced } from '../components/ui.jsx';
import { UploadHistory, WorkflowSteps } from './law/UploadHistory.jsx';

const STATUS = {
  processing: ['กำลังถอดข้อความ', 'bg-sky-100 text-sky-800'],
  draft: ['ร่าง — กำลังตรวจทาน', 'bg-amber-100 text-amber-900'],
  submitted: ['ส่งตรวจแล้ว — รออนุมัติ', 'bg-violet-100 text-violet-800'],
  approved: ['อนุมัติแล้ว — เผยแพร่', 'bg-emerald-100 text-emerald-800'],
  superseded: ['ถูกแทนที่ด้วยฉบับแก้ไข', 'bg-slate-100 text-slate-500'],
  rejected: ['ไม่รับ', 'bg-slate-200 text-slate-700'],
  failed: ['ถอดข้อความไม่สำเร็จ', 'bg-rose-100 text-rose-800']
};
const METHOD = { docx: 'Word', doc: 'Word (.doc)', pdf: 'PDF ข้อความ', ocr: 'OCR จากภาพสแกน' };
const StatusBadge = ({ s }) => (
  <span className={`px-2 py-0.5 rounded text-[11px] font-medium whitespace-nowrap ${STATUS[s]?.[1] || ''}`}>{STATUS[s]?.[0] || s}</span>
);
const when = (iso) => (iso ? new Date(iso).toLocaleString('th-TH', { dateStyle: 'medium', timeStyle: 'short' }) : '');

/** หน้านำเข้าเอกสารกฎหมาย (PDF/Word): อัปโหลด → ระบบแยกข้อ → ตรวจทาน → อนุมัติโดยผู้อื่น → เผยแพร่ */
export default function ImportPage({ me, go, notify }) {
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const timer = useRef(null);

  const refresh = () => api.uploads().then(setList).catch(setError);
  useEffect(() => { refresh(); return () => clearTimeout(timer.current); }, []);
  useEffect(() => {
    clearTimeout(timer.current);
    if (list?.items?.some((i) => i.status === 'processing')) timer.current = setTimeout(refresh, 3000);
  }, [list]);

  if (open) return <Review key={open} id={open} me={me} go={go} notify={notify} onOpen={setOpen} onBack={() => { setOpen(null); refresh(); }} />;
  if (!list) return error ? <ErrorBox error={error} /> : <Spinner />;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <FileUp className="w-5 h-5 text-brand-600" />
        <h2 className="text-lg font-bold text-slate-900">นำเข้าเอกสารกฎหมาย</h2>
      </div>
      <p className="text-xs text-slate-600 -mt-3">
        อัปโหลด PDF หรือ Word → ระบบถอดข้อความและแยกเป็นข้อ/มาตรา (ไฟล์สแกนใช้ OCR ภายในเซิร์ฟเวอร์) → ตรวจทานและแก้ร่าง
        (ระบบเก็บทุกรุ่น) → ส่งตรวจ → นิติกรหรือผู้ดูแลที่ไม่ได้ร่วมทำร่างอนุมัติ จึงขึ้นในระบบค้นและผู้ช่วย AI
      </p>
      <UploadForm docTypes={list.doc_types} notify={notify} onDone={(u) => { refresh(); notify?.(`รับไฟล์ ${u.original_name} แล้ว — กำลังถอดข้อความ`); }} />

      <Card className="overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
          <h3 className="font-semibold text-sm text-slate-800">รายการนำเข้า</h3>
          <button onClick={refresh} className="p-1 rounded hover:bg-slate-100" title="โหลดใหม่"><RefreshCw className="w-4 h-4 text-slate-500" /></button>
        </div>
        {list.items.length === 0 ? (
          <p className="p-6 text-sm text-slate-400 text-center">ยังไม่มีรายการ</p>
        ) : (
          <div className="divide-y divide-slate-100">
            {list.items.map((u) => (
              <button key={u.upload_id} onClick={() => setOpen(u.upload_id)} className="w-full text-left px-4 py-3 hover:bg-slate-50 flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="text-xs text-slate-400 w-10">#{u.upload_id}</span>
                <StatusBadge s={u.status} />
                {u.status === 'processing' && <Loader2 className="w-3.5 h-3.5 animate-spin text-sky-600" />}
                {u.classification === 'internal' && <Lock className="w-3.5 h-3.5 text-slate-500" title="เฉพาะเจ้าหน้าที่" />}
                <span className="text-sm text-slate-800 font-medium flex-1 min-w-[16rem] truncate">{u.title || u.original_name}</span>
                <span className="text-[11px] text-slate-500">
                  {u.method ? `${METHOD[u.method]} · ` : ''}{u.provision_count ? `${u.provision_count} บทบัญญัติ · ` : ''}
                  {u.uploaded_by} · {when(u.uploaded_at)}
                </span>
                {u.amends_eli && <span className="text-[11px] px-1.5 py-0.5 rounded bg-sky-50 text-sky-800 border border-sky-200">คำขอแก้ไข</span>}
                {u.can_approve && (
                  <span className="text-[11px] px-1.5 py-0.5 rounded bg-brand-600 text-white">รอคุณอนุมัติได้</span>
                )}
              </button>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function UploadForm({ docTypes, onDone }) {
  const [file, setFile] = useState(null);
  const [docType, setDocType] = useState('ประกาศ');
  const [cls, setCls] = useState('public');
  const [eff, setEff] = useState('');
  const [ocr, setOcr] = useState(false);
  const [reg, setReg] = useState(null);
  const [q, setQ] = useState('');
  const [opts, setOpts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const dq = useDebounced(q, 350);
  const fileRef = useRef(null);

  useEffect(() => {
    if (!dq.trim() || reg) { setOpts([]); return; }
    api.uploadRegisterOptions(dq).then((r) => setOpts(r.items)).catch(() => setOpts([]));
  }, [dq, reg]);

  const submit = async () => {
    if (!file) return setError(new Error('เลือกไฟล์ก่อน'));
    setBusy(true); setError(null);
    try {
      const u = await uploadLawFile(file, { doc_type: docType, classification: cls, register_id: reg?.register_id,
        effective_date: eff, force_ocr: ocr ? 'true' : '' });
      setFile(null); setReg(null); setQ(''); setEff(''); setOcr(false);
      if (fileRef.current) fileRef.current.value = '';
      onDone(u);
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  return (
    <Card className="p-5 space-y-4">
      <div className="grid md:grid-cols-2 gap-4">
        <div>
          <Label>ไฟล์ (.pdf .docx .doc ไม่เกิน 50 MB)</Label>
          <input ref={fileRef} type="file" accept=".pdf,.docx,.doc" onChange={(e) => setFile(e.target.files?.[0] || null)}
            className="block w-full text-sm text-slate-700 file:mr-3 file:px-3 file:py-2 file:rounded-lg file:border-0 file:bg-brand-50 file:text-brand-700 file:font-semibold hover:file:bg-brand-100" />
          <label className="mt-2 flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={ocr} onChange={(e) => setOcr(e.target.checked)} />
            เป็นไฟล์สแกน — บังคับ OCR (ปกติระบบตรวจให้อัตโนมัติ)
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>ประเภทเอกสาร</Label>
            <select className={inputCls} value={docType} onChange={(e) => setDocType(e.target.value)}>
              {docTypes.map((t) => <option key={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <Label>ชั้นข้อมูล</Label>
            <select className={inputCls} value={cls} onChange={(e) => setCls(e.target.value)}>
              <option value="public">สาธารณะ</option>
              <option value="internal">เฉพาะเจ้าหน้าที่</option>
            </select>
          </div>
        </div>
      </div>
      <div className="grid md:grid-cols-3 gap-4">
        <div className="md:col-span-2 relative">
          <Label>ผูกกับแถวทะเบียนกฎหมายลำดับรอง (ไม่บังคับ)</Label>
          {reg ? (
            <div className="flex items-center gap-2 p-2 rounded-lg border border-brand-200 bg-brand-50 text-xs">
              <span className="font-semibold text-brand-800">{reg.reg_no}</span>
              <span className="flex-1 text-slate-700 line-clamp-2">{reg.title_th}</span>
              <button onClick={() => setReg(null)} className="text-slate-500 hover:text-rose-600"><XCircle className="w-4 h-4" /></button>
            </div>
          ) : (
            <div className="relative">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input className={`${inputCls} pl-9`} value={q} onChange={(e) => setQ(e.target.value)} placeholder="ค้นชื่อเรื่องหรือเลขทะเบียน (เฉพาะแถวที่ยังไม่มีตัวบท)" />
            </div>
          )}
          {opts.length > 0 && (
            <div className="absolute z-10 mt-1 w-full max-h-64 overflow-auto bg-white border border-slate-200 rounded-lg shadow-lg">
              {opts.map((o) => (
                <button key={o.register_id} onClick={() => { setReg(o); setOpts([]); if (o.effective_date) setEff(''); }}
                  className="w-full text-left px-3 py-2 text-xs hover:bg-brand-50 border-b border-slate-100 last:border-0">
                  <span className="font-semibold text-slate-800 mr-2">{o.reg_no}</span>
                  <span className="text-slate-700">{o.title_th}</span>
                  <span className="ml-2 text-slate-400">{o.status === 'IN_FORCE' ? 'ใช้บังคับ' : 'ยกเลิกแล้ว'}{o.effective_date ? ` · ${thDate(o.effective_date)}` : ''}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div>
          <Label>วันใช้บังคับ {reg?.effective_date ? '(ใช้ตามทะเบียน)' : '(ไม่ระบุ = หาจากเอกสาร)'}</Label>
          <input type="date" className={inputCls} value={eff} onChange={(e) => setEff(e.target.value)} disabled={!!reg?.effective_date} />
        </div>
      </div>
      {error && <ErrorBox error={error} />}
      <button onClick={submit} disabled={busy || !file}
        className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileUp className="w-4 h-4" />} นำเข้า
      </button>
    </Card>
  );
}

function Review({ id, me, go, notify, onBack, onOpen }) {
  const [u, setU] = useState(null);
  const [form, setForm] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [dirty, setDirty] = useState(false);
  const [version, setVersion] = useState(0);   // ให้ประวัติ/ความต่างโหลดใหม่หลังบันทึกหรือเปลี่ยนสถานะ
  const timer = useRef(null);

  const load = (v) => {
    setU(v); setDirty(false); setVersion((n) => n + 1);
    const d = v.draft;
    setForm(d && d.provisions ? {
      title: d.title, code: d.code, category: d.category, classification: d.classification || 'public',
      effectiveDate: d.effectiveDate || '', publishedDate: d.publishedDate || '', source: d.source || '',
      provisions: d.provisions.map((p) => ({ number: p.number, part: p.part || '', text: p.versions[0].text }))
    } : null);
  };
  const refresh = () => api.upload(id).then(load).catch(setError);
  useEffect(() => { refresh(); return () => clearTimeout(timer.current); }, [id]);
  useEffect(() => {
    clearTimeout(timer.current);
    if (u?.status === 'processing') timer.current = setTimeout(refresh, 3000);
  }, [u]);

  const run = async (fn, msg) => {
    setBusy(true); setError(null);
    try { const v = await fn(); if (v?.upload_id) { if (v.draft !== undefined) load(v); else refresh(); } if (msg) notify?.(msg); return v; }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  const set = (k) => (e) => { setForm({ ...form, [k]: e.target.value }); setDirty(true); };
  const setProv = (i, k) => (e) => {
    const ps = form.provisions.slice(); ps[i] = { ...ps[i], [k]: e.target.value };
    setForm({ ...form, provisions: ps }); setDirty(true);
  };
  const removeProv = (i) => { setForm({ ...form, provisions: form.provisions.filter((_, j) => j !== i) }); setDirty(true); };
  const addProv = (i) => {
    const ps = form.provisions.slice(); ps.splice(i + 1, 0, { number: '', part: ps[i]?.part || '', text: '' });
    setForm({ ...form, provisions: ps }); setDirty(true);
  };
  const save = () => run(() => api.saveUpload(id, {
    ...form, effectiveDate: form.effectiveDate || null, publishedDate: form.publishedDate || null
  }), 'บันทึกร่างแล้ว');

  if (!u) return error ? <ErrorBox error={error} /> : <Spinner />;
  const editable = u.status === 'draft';
  const mine = u.uploaded_by === me.user_id;
  const submitter = u.submitted_by === me.user_id || me.role === 'ADM';
  const why = u.can_approve ? '' : [u.uploaded_by, u.submitted_by, ...(u.editors || [])].includes(me.user_id)
    ? 'คุณร่วมทำร่างนี้ (นำเข้า แก้ หรือส่งตรวจ) — ให้นิติกรหรือผู้ดูแลคนอื่นอนุมัติ (หลักสี่ตา)' : '';
  const src = u.draft?.sourceFiles?.[0]?.sha256 || u.sha256;

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-sm text-brand-700 hover:underline flex items-center gap-1"><ArrowLeft className="w-4 h-4" /> รายการนำเข้า</button>
      <Card className="p-5 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-400">#{u.upload_id}</span>
          <StatusBadge s={u.status} />
          {u.method && <span className="text-[11px] px-2 py-0.5 rounded bg-slate-100 text-slate-700 flex items-center gap-1">
            {u.method === 'ocr' ? <ScanText className="w-3 h-3" /> : <FileText className="w-3 h-3" />}{METHOD[u.method]}</span>}
          <button onClick={() => openRawFile(src).catch(setError)} className="text-[11px] text-brand-700 hover:underline flex items-center gap-1">
            <ExternalLink className="w-3 h-3" /> {u.original_name}
          </button>
          <span className="ml-auto text-[11px] text-slate-500">
            นำเข้าโดย {u.uploaded_by} · {when(u.uploaded_at)}{u.edited_by ? ` · แก้ล่าสุด ${u.edited_by} ${when(u.edited_at)}` : ''}
            {u.reviewed_by ? ` · ${u.status === 'approved' || u.status === 'superseded' ? 'อนุมัติ' : u.status === 'rejected' ? 'ไม่รับ' : 'ส่งกลับ'}โดย ${u.reviewed_by} ${when(u.reviewed_at)}` : ''}
          </span>
        </div>
        <WorkflowSteps status={u.status} />
        {u.amends_eli && (
          <p className="text-xs p-2 rounded bg-sky-50 border border-sky-200 text-sky-900">
            คำขอแก้ไขตัวบทที่เผยแพร่แล้ว (จากรายการ #{u.amends_upload}) — เมื่ออนุมัติจะแทนที่ฉบับเดิมในระบบค้นและผู้ช่วย AI
          </p>
        )}
        {u.register_title && <p className="text-xs text-slate-600">ทะเบียน: {u.register_title}</p>}
        {u.submitted_by && ['submitted', 'approved'].includes(u.status) && (
          <p className="text-[11px] text-slate-500">ส่งตรวจโดย {u.submitted_by} · {when(u.submitted_at)}{u.editors?.length ? ` · ผู้แก้ร่าง: ${u.editors.join(', ')}` : ''}</p>
        )}
        {u.review_note && (
          <p className={`text-xs p-2 rounded border ${u.status === 'draft' ? 'bg-amber-50 border-amber-300 text-amber-900' : 'bg-slate-50 border-slate-200'}`}>
            {u.status === 'draft' ? `ส่งกลับให้แก้โดย ${u.reviewed_by}: ` : 'หมายเหตุผู้ตรวจ: '}{u.review_note}
          </p>
        )}
        {u.status === 'processing' && <div className="flex items-center gap-2 text-sm text-sky-800"><Loader2 className="w-4 h-4 animate-spin" /> กำลังถอดข้อความ — ไฟล์สแกนใช้เวลาราว 3 วินาทีต่อหน้า</div>}
        {u.error && <div className="p-3 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">{u.error}</div>}
        {u.notes?.length > 0 && (
          <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900 space-y-1">
            <div className="font-semibold flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" /> ข้อสังเกตจากการแปลง (จะเป็นประเด็นรอตรวจหลังเผยแพร่)</div>
            <ul className="list-disc pl-5">{u.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          </div>
        )}
        {error && <ErrorBox error={error} />}
        <div className="flex flex-wrap gap-2 pt-1">
          {u.status === 'approved' && (
            <button onClick={() => go('law', { tab: 'provision', eli: u.draft.provisions[0].id, ...(u.draft.effectiveDate > new Date().toISOString().slice(0, 10) ? { asOf: u.draft.effectiveDate } : {}) })} className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-semibold flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" /> เปิดตัวบทในระบบ
            </button>
          )}
          {u.status === 'approved' && (
            <button onClick={async () => { const v = await run(() => api.correctLaw(u.eli_id), 'สร้างคำขอแก้ไขแล้ว'); if (v?.upload_id) onOpen(v.upload_id); }} disabled={busy}
              className="px-3 py-1.5 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 text-xs flex items-center gap-1">
              <PencilLine className="w-3.5 h-3.5" /> ขอแก้ไขฉบับนี้
            </button>
          )}
          {['draft', 'failed', 'rejected'].includes(u.status) && !u.amends_eli && (
            <button onClick={() => run(() => api.reprocessUpload(id, true), 'เริ่มถอดใหม่ด้วย OCR')} disabled={busy}
              className="px-3 py-1.5 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 text-xs flex items-center gap-1">
              <ScanText className="w-3.5 h-3.5" /> ถอดใหม่ด้วย OCR
            </button>
          )}
          {!['approved', 'superseded', 'processing'].includes(u.status) && (mine || me.role === 'ADM') && (
            <button onClick={async () => { if (await run(() => api.deleteUpload(id), 'ลบรายการแล้ว')) onBack(); }} disabled={busy}
              className="px-3 py-1.5 rounded-lg border border-rose-200 text-rose-700 hover:bg-rose-50 text-xs flex items-center gap-1">
              <Trash2 className="w-3.5 h-3.5" /> ลบรายการ
            </button>
          )}
        </div>
      </Card>

      {u.status !== 'processing' && u.status !== 'failed' && <UploadHistory id={id} version={version} />}

      {form && (
        <>
          <Card className="p-5 space-y-3">
            <h3 className="font-semibold text-sm text-slate-800">ข้อมูลตราสาร</h3>
            <div><Label>ชื่อเต็ม</Label><input className={inputCls} value={form.title} onChange={set('title')} disabled={!editable} /></div>
            <div className="grid md:grid-cols-3 gap-3">
              <div><Label>ชื่อย่อ (ใช้ในการอ้างอิง)</Label><input className={inputCls} value={form.code} onChange={set('code')} disabled={!editable} /></div>
              <div><Label>หมวดหมู่</Label><input className={inputCls} value={form.category} onChange={set('category')} disabled={!editable} /></div>
              <div>
                <Label>ชั้นข้อมูล</Label>
                <select className={inputCls} value={form.classification} onChange={set('classification')} disabled={!editable}>
                  <option value="public">สาธารณะ</option><option value="internal">เฉพาะเจ้าหน้าที่</option>
                </select>
              </div>
              <div>
                <Label>วันใช้บังคับ {!form.effectiveDate && <span className="text-rose-600">— ต้องระบุก่อนอนุมัติ</span>}</Label>
                <input type="date" className={`${inputCls} ${!form.effectiveDate ? 'border-rose-400 bg-rose-50' : ''}`} value={form.effectiveDate} onChange={set('effectiveDate')} disabled={!editable} />
              </div>
              <div><Label>วันประกาศ</Label><input type="date" className={inputCls} value={form.publishedDate} onChange={set('publishedDate')} disabled={!editable} /></div>
              <div><Label>แหล่งที่มา</Label><input className={inputCls} value={form.source} onChange={set('source')} disabled={!editable} /></div>
            </div>
            <p className="text-[11px] text-slate-400">รหัสอ้างอิง: <span className="font-mono">{u.draft.id}</span></p>
          </Card>

          <Card className="p-5 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-sm text-slate-800">บทบัญญัติ ({form.provisions.length})</h3>
              {editable && <span className="text-[11px] text-slate-500">ตรวจเทียบกับไฟล์ต้นฉบับ — แก้เลขข้อ ข้อความ หรือแยก/รวมข้อได้</span>}
            </div>
            {form.provisions.map((p, i) => (
              <div key={i} className="border border-slate-200 rounded-lg p-3 space-y-2">
                <div className="flex gap-2 items-center">
                  <div className="w-32 shrink-0">
                    <input className={inputCls} value={p.number} onChange={setProv(i, 'number')} disabled={!editable} placeholder="ข้อ 1" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <input className={inputCls} value={p.part} onChange={setProv(i, 'part')} disabled={!editable} placeholder="หมวด/ส่วน (ถ้ามี)" />
                  </div>
                  {editable && (
                    <>
                      <button onClick={() => addProv(i)} className="p-1.5 rounded hover:bg-slate-100" title="เพิ่มบทบัญญัติถัดจากนี้"><Plus className="w-4 h-4 text-slate-600" /></button>
                      <button onClick={() => removeProv(i)} className="p-1.5 rounded hover:bg-rose-50" title="ลบบทบัญญัตินี้"><Trash2 className="w-4 h-4 text-rose-600" /></button>
                    </>
                  )}
                </div>
                <textarea className={`${inputCls} font-[inherit] leading-relaxed`} rows={Math.min(14, Math.max(3, Math.ceil(p.text.length / 90) + p.text.split('\n').length))}
                  value={p.text} onChange={setProv(i, 'text')} disabled={!editable} />
              </div>
            ))}
          </Card>

          {editable && (
            <Card className="p-4 flex flex-wrap items-center gap-2 sticky bottom-3 shadow-lg">
              <button onClick={save} disabled={busy || !dirty} className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
                <Save className="w-4 h-4" /> บันทึกร่าง
              </button>
              <button onClick={() => run(() => api.submitUpload(id), 'ส่งตรวจแล้ว')} disabled={busy || dirty || !u.draft.effectiveDate}
                title={dirty ? 'บันทึกร่างก่อน' : !u.draft.effectiveDate ? 'ระบุวันใช้บังคับก่อน' : ''}
                className="px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
                <Send className="w-4 h-4" /> ส่งตรวจ
              </button>
              <span className="text-[11px] text-slate-500">ทุกครั้งที่บันทึก ระบบเก็บเป็นรุ่นใหม่ ผู้อนุมัติจะเห็นว่าแก้อะไรจากผลถอดข้อความ</span>
            </Card>
          )}
          {u.status === 'submitted' && (
            <Card className="p-4 flex flex-wrap items-center gap-2 sticky bottom-3 shadow-lg">
              {submitter && (
                <button onClick={() => run(() => api.withdrawUpload(id), 'ถอนกลับมาแก้แล้ว')} disabled={busy}
                  className="px-3 py-2 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 text-sm flex items-center gap-1.5">
                  <CornerUpLeft className="w-4 h-4" /> ถอนกลับมาแก้
                </button>
              )}
              <input className={`${inputCls} flex-1 min-w-[12rem]`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="หมายเหตุการตรวจ (ต้องระบุเมื่อส่งกลับแก้)" />
              <button onClick={() => run(() => api.returnUpload(id, note), 'ส่งกลับให้แก้แล้ว')} disabled={busy || !note.trim()}
                className="px-3 py-2 rounded-lg border border-amber-300 text-amber-800 hover:bg-amber-50 text-sm flex items-center gap-1.5 disabled:opacity-50">
                <Undo2 className="w-4 h-4" /> ส่งกลับแก้
              </button>
              <button onClick={() => run(() => api.rejectUpload(id, note), 'บันทึกว่าไม่รับแล้ว')} disabled={busy}
                className="px-3 py-2 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 text-sm flex items-center gap-1.5">
                <XCircle className="w-4 h-4" /> ไม่รับ
              </button>
              <button onClick={() => run(() => api.approveUpload(id, note), u.amends_eli ? 'อนุมัติและแทนที่ฉบับเดิมแล้ว' : 'อนุมัติและเผยแพร่แล้ว')} disabled={busy || !u.can_approve}
                title={why}
                className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
                <CheckCircle2 className="w-4 h-4" /> {u.amends_eli ? 'อนุมัติและแทนที่ฉบับเดิม' : 'อนุมัติและเผยแพร่'}
              </button>
              {why && <span className="w-full text-[11px] text-slate-500">{why}</span>}
            </Card>
          )}
        </>
      )}
    </div>
  );
}
