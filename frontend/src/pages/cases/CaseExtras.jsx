import { useEffect, useRef, useState } from 'react';
import { ExternalLink, FileUp, History, Loader2, Plus, RefreshCw, ScanText, Trash2 } from 'lucide-react';
import { api, openCaseDoc, postForm } from '../../api.js';
import { DocView, ErrorBox, Label, inputCls, thDate } from '../../components/ui.jsx';

const when = (iso) => (iso ? new Date(iso).toLocaleString('th-TH', { dateStyle: 'medium', timeStyle: 'short' }) : '');

/** ลำดับกระบวนพิจารณา — ผู้รับผิดชอบ/ผอ.คบ. เพิ่มหรือลบรายการได้ (รวมวันนัดในอนาคต) */
export function Proceedings({ caseId, items, canEdit, onChange }) {
  const [f, setF] = useState({ proceeding_date: '', title: '', detail: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const add = async () => {
    setBusy(true); setError(null);
    try { await api.addProceeding(caseId, { ...f, detail: f.detail || null }); setF({ proceeding_date: '', title: '', detail: '' }); onChange(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  const del = async (pid) => {
    try { await api.deleteProceeding(caseId, pid); onChange(); } catch (e) { setError(e); }
  };
  if (!items.length && !canEdit) return null;
  return (
    <div>
      <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-2">ลำดับการดำเนินกระบวนพิจารณาในชั้นศาล</h4>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
        {items.map((p) => (
          <div key={p.proceeding_id} className="p-3 rounded-xl border border-slate-200 bg-white group">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-bold text-slate-900">{p.title}</span>
              <span className="flex items-center gap-1 shrink-0">
                <span className="text-[11px] font-semibold text-indigo-700">{thDate(p.proceeding_date)}</span>
                {canEdit && (
                  <button onClick={() => del(p.proceeding_id)} title="ลบรายการ" className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-rose-50">
                    <Trash2 className="w-3.5 h-3.5 text-rose-600" />
                  </button>
                )}
              </span>
            </div>
            {p.detail && <p className="text-[11px] text-slate-500 mt-1">{p.detail}</p>}
          </div>
        ))}
      </div>
      {canEdit && (
        <div className="mt-2.5 p-3 rounded-xl border border-dashed border-slate-300 grid md:grid-cols-12 gap-2 items-end">
          <div className="md:col-span-3"><Label htmlFor="proc-proceeding_date">วันที่</Label><input id="proc-proceeding_date" type="date" className={inputCls} value={f.proceeding_date} onChange={(e) => setF({ ...f, proceeding_date: e.target.value })} /></div>
          <div className="md:col-span-4"><Label htmlFor="proc-title">รายการ</Label><input id="proc-title" className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="ยื่นคำให้การ / นัดนั่งพิจารณา" /></div>
          <div className="md:col-span-4"><Label htmlFor="proc-detail">รายละเอียด</Label><input id="proc-detail" className={inputCls} value={f.detail} onChange={(e) => setF({ ...f, detail: e.target.value })} /></div>
          <button onClick={add} disabled={busy || !f.proceeding_date || f.title.trim().length < 2}
            className="md:col-span-1 h-10 rounded-lg bg-indigo-600 text-white text-xs font-semibold flex items-center justify-center gap-1 disabled:opacity-50">
            <Plus className="w-4 h-4" /> เพิ่ม
          </button>
          {error && <div className="md:col-span-12"><ErrorBox error={error} /></div>}
        </div>
      )}
    </div>
  );
}

const DOC_STATUS = {
  processing: ['กำลังถอดข้อความ', 'bg-sky-100 text-sky-800'],
  failed: ['ถอดข้อความไม่สำเร็จ', 'bg-rose-100 text-rose-800']
};

/** เอกสารในสำนวน: ดูข้อความที่ถอดได้ เปิดไฟล์ต้นฉบับ แนบเพิ่ม (Word/PDF, ไฟล์สแกนใช้ OCR ในเครื่อง) */
export function Documents({ caseId, items, canEdit, docTypes, onChange }) {
  const [idx, setIdx] = useState(0);
  const [error, setError] = useState(null);
  const timer = useRef(null);
  useEffect(() => {
    clearTimeout(timer.current);
    if (items.some((d) => d.status === 'processing')) timer.current = setTimeout(onChange, 3000);
    return () => clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ตั้งเวลาโหลดซ้ำจากสถานะเอกสารล่าสุดเท่านั้น
  }, [items]);
  const doc = items[Math.min(idx, items.length - 1)];
  const run = async (fn) => { setError(null); try { await fn(); onChange(); } catch (e) { setError(e); } };
  if (!items.length && !canEdit) return null;
  return (
    <div className="pt-2 border-t border-slate-200 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-bold text-slate-700 tracking-wider">เอกสารในสำนวนคดี</h4>
        <div className="flex flex-wrap gap-1.5">
          {items.map((x, i) => (
            <button key={x.document_id} onClick={() => setIdx(i)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 ${doc?.document_id === x.document_id ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}>
              {x.status === 'processing' && <Loader2 className="w-3 h-3 animate-spin" />}
              {x.doc_type.length > 40 ? x.doc_type.slice(0, 40) + '…' : x.doc_type}{x.doc_no ? ` ${x.doc_no}` : ''}
            </button>
          ))}
        </div>
      </div>
      {doc && (
        <div className="space-y-2">
          {(doc.has_file || canEdit) && (
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
              {DOC_STATUS[doc.status] && <span className={`px-2 py-0.5 rounded font-medium ${DOC_STATUS[doc.status][1]}`}>{DOC_STATUS[doc.status][0]}</span>}
              {doc.method === 'ocr' && <span className="flex items-center gap-1"><ScanText className="w-3 h-3" /> ข้อความจาก OCR — ตรวจกับต้นฉบับ</span>}
              {doc.has_file && (
                <button onClick={() => openCaseDoc(caseId, doc.document_id).catch(setError)} className="text-brand-700 hover:underline flex items-center gap-1">
                  <ExternalLink className="w-3 h-3" /> {doc.original_name}
                </button>
              )}
              {doc.added_by && <span>แนบโดย {doc.added_by}</span>}
              {canEdit && doc.has_file && doc.status === 'failed' && (
                <button onClick={() => run(() => api.reprocessCaseDoc(caseId, doc.document_id))} className="text-brand-700 hover:underline flex items-center gap-1">
                  <RefreshCw className="w-3 h-3" /> ถอดใหม่
                </button>
              )}
              {canEdit && doc.status !== 'processing' && (
                <button onClick={() => run(() => api.deleteCaseDoc(caseId, doc.document_id))} className="ml-auto text-rose-700 hover:underline flex items-center gap-1">
                  <Trash2 className="w-3 h-3" /> เอาออกจากสำนวน
                </button>
              )}
            </div>
          )}
          {doc.error && <p className="text-xs p-2 rounded bg-rose-50 border border-rose-200 text-rose-800">{doc.error}</p>}
          {doc.status === 'ready' && doc.excerpt && <DocView doc={{ ...doc, excerpt: doc.excerpt.length > 3000 ? doc.excerpt.slice(0, 3000) + '…' : doc.excerpt }} />}
        </div>
      )}
      <ErrorBox error={error} />
      {canEdit && <AttachForm caseId={caseId} docTypes={docTypes} onDone={onChange} />}
    </div>
  );
}

function AttachForm({ caseId, docTypes, onDone }) {
  const [file, setFile] = useState(null);
  const [f, setF] = useState({ doc_type: 'คำพิพากษา', doc_no: '', doc_date: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const ref = useRef(null);
  const send = async () => {
    setBusy(true); setError(null);
    try {
      await postForm(`/court-cases/${caseId}/documents`, file, f);
      setFile(null); setF({ ...f, doc_no: '', doc_date: '' }); if (ref.current) ref.current.value = '';
      onDone();
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <div className="p-3 rounded-xl border border-dashed border-slate-300 space-y-2">
      <div className="grid md:grid-cols-12 gap-2 items-end">
        <div className="md:col-span-4">
          <Label>ไฟล์ (.pdf .docx .doc ไม่เกิน 50 MB)</Label>
          <input ref={ref} type="file" accept=".pdf,.docx,.doc" onChange={(e) => setFile(e.target.files?.[0] || null)} className="block w-full text-xs text-slate-700" />
        </div>
        <div className="md:col-span-3">
          <Label>ประเภทเอกสาร</Label>
          <select className={inputCls} value={f.doc_type} onChange={(e) => setF({ ...f, doc_type: e.target.value })}>
            {docTypes.map((t) => <option key={t}>{t}</option>)}
          </select>
        </div>
        <div className="md:col-span-2"><Label>เลขที่</Label><input className={inputCls} value={f.doc_no} onChange={(e) => setF({ ...f, doc_no: e.target.value })} /></div>
        <div className="md:col-span-2"><Label>ลงวันที่</Label><input type="date" className={inputCls} value={f.doc_date} onChange={(e) => setF({ ...f, doc_date: e.target.value })} /></div>
        <button onClick={send} disabled={busy || !file} className="md:col-span-1 h-10 rounded-lg bg-indigo-600 text-white text-xs font-semibold flex items-center justify-center gap-1 disabled:opacity-50">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileUp className="w-4 h-4" />} แนบ
        </button>
      </div>
      <ErrorBox error={error} />
    </div>
  );
}

const FIELD = {
  black_no: 'เลขคดีดำ', red_no: 'เลขคดีแดง', court_level: 'ชั้นศาล', court_name: 'ศาล', case_category: 'ประเภทคดี',
  subject: 'เรื่อง', summary: 'สรุป', party_code: 'รหัสคู่กรณี', party_label: 'คำเรียกคู่กรณี', defendant: 'ผู้ถูกฟ้องคดี',
  energy_sector: 'ประเภทกิจการ', incident_date: 'วันเกิดเหตุ', filed_date: 'วันยื่นฟ้อง', judgment_date: 'วันพิพากษา',
  status: 'สถานะ', stage_text: 'ขั้นตอนคดี', result: 'ผลคดี', classification: 'ชั้นความลับ', appeal_id: 'เรื่องอุทธรณ์ต้นเรื่อง',
  owners: 'ผู้รับผิดชอบ'
};
const KIND = { create: 'เพิ่มคดี', edit: 'แก้ไข', proceeding: 'กระบวนพิจารณา', import: 'นำเข้าจาก Excel' };
const show = (v) => (v === null || v === undefined || v === '' ? '—' : Array.isArray(v) ? v.join(', ') || '—' : String(v));

/** ประวัติการแก้ไขคดี (แก้/ลบย้อนหลังไม่ได้) */
export function CaseHistory({ caseId, version }) {
  const [items, setItems] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { if (open) api.caseHistory(caseId).then((r) => setItems(r.items)).catch(() => setItems([])); }, [caseId, version, open]);
  return (
    <div className="pt-2 border-t border-slate-200">
      <button onClick={() => setOpen(!open)} className="text-xs font-bold text-slate-700 flex items-center gap-1.5">
        <History className="w-3.5 h-3.5" /> ประวัติการแก้ไข {open ? '▴' : '▾'}
      </button>
      {open && (
        <ol className="mt-2 space-y-1.5">
          {!items && <Loader2 className="w-4 h-4 animate-spin text-slate-400" />}
          {items?.map((r) => (
            <li key={r.rev_no} className="text-xs p-2 rounded border border-slate-200">
              <div className="flex gap-2 text-slate-600">
                <span className="font-semibold">รุ่น {r.rev_no}</span><span>{KIND[r.kind] || r.kind}</span>
                <span className="ml-auto text-slate-400">{r.actor} · {when(r.created_at)}</span>
              </div>
              {(r.summary.fields || []).map((c) => (
                <div key={c.field} className="mt-1 text-[11px]">
                  <span className="text-slate-500">{FIELD[c.field] || c.field}: </span>
                  <del className="bg-rose-50 text-rose-800">{show(c.old)}</del> → <ins className="no-underline bg-emerald-50 text-emerald-800">{show(c.new)}</ins>
                </div>
              ))}
              {(r.summary.proceedings_added || []).map((x) => <div key={`a${x}`} className="mt-1 text-[11px] text-emerald-800">+ {x}</div>)}
              {(r.summary.proceedings_removed || []).map((x) => <div key={`r${x}`} className="mt-1 text-[11px] text-rose-800">− {x}</div>)}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
