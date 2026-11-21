import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, CheckCircle2, Download, FileSpreadsheet, Loader2, Send, XCircle } from 'lucide-react';
import { api, downloadFile, postForm } from '../../api.js';
import { Card, ErrorBox, inputCls } from '../../components/ui.jsx';

const when = (iso) => (iso ? new Date(iso).toLocaleString('th-TH', { dateStyle: 'medium', timeStyle: 'short' }) : '');
const STATUS = {
  draft: ['ตรวจแล้ว — รอส่งตรวจ', 'bg-amber-100 text-amber-900'],
  submitted: ['ส่งตรวจแล้ว — รออนุมัติ', 'bg-violet-100 text-violet-800'],
  applied: ['อนุมัติและบันทึกแล้ว', 'bg-emerald-100 text-emerald-800'],
  rejected: ['ไม่รับ / ยกเลิก', 'bg-slate-200 text-slate-700']
};
const ACTION = {
  create: ['เพิ่มใหม่', 'bg-emerald-100 text-emerald-800'],
  update: ['ปรับปรุง', 'bg-amber-100 text-amber-900'],
  unchanged: ['ไม่เปลี่ยน', 'bg-slate-100 text-slate-500']
};
const Badge = ({ m, k }) => <span className={`px-2 py-0.5 rounded text-[11px] font-medium whitespace-nowrap ${m[k]?.[1] || ''}`}>{m[k]?.[0] || k}</span>;

/** นำเข้าทะเบียนคดีจาก Excel: แม่แบบ → อัปโหลด → ตรวจทีละแถว → ส่งตรวจ → ผู้อื่นอนุมัติ */
export default function CaseImport({ me, opts, onClose, notify }) {
  const [list, setList] = useState(null);
  const [open, setOpen] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);
  const refresh = () => api.caseImports().then((r) => setList(r.items)).catch(setError);
  useEffect(() => { refresh(); }, []);

  const upload = async (file) => {
    setBusy(true); setError(null);
    try { const v = await postForm('/court-cases/imports', file); setOpen(v.import_id); refresh(); }
    catch (e) { setError(e); } finally { setBusy(false); if (ref.current) ref.current.value = ''; }
  };

  if (open) return <ImportDetail id={open} me={me} notify={notify} onBack={() => { setOpen(null); refresh(); }} />;
  return (
    <Card className="p-5 sm:p-6 space-y-4">
      <div className="flex items-center gap-2">
        <button onClick={onClose} className="text-sm text-brand-700 hover:underline flex items-center gap-1"><ArrowLeft className="w-4 h-4" /> สืบค้นงานคดี</button>
      </div>
      <div className="flex items-center gap-2">
        <FileSpreadsheet className="w-5 h-5 text-indigo-600" />
        <h2 className="text-base font-bold text-slate-900">นำเข้าทะเบียนคดีจาก Excel</h2>
      </div>
      <p className="text-xs text-slate-600">
        ดาวน์โหลดแม่แบบ → กรอกชีต "คดี" (และ "กระบวนพิจารณา" ถ้ามี) → อัปโหลด → ระบบตรวจทีละแถวและบอกว่าแถวไหนจะเพิ่มใหม่หรือปรับปรุงคดีเดิม
        → ส่งตรวจ → นิติกรหรือ ผอ.คบ. อีกคนอนุมัติ จึงบันทึกเข้าระบบ · จับคู่คดีด้วยศาล + เลขคดีดำ นำเข้าไฟล์เดิมซ้ำไม่สร้างคดีซ้ำ
      </p>
      {opts.public_data_only && (
        <p className="text-xs p-2 rounded bg-amber-50 border border-amber-300 text-amber-900">เครื่องนี้เก็บเฉพาะข้อมูลสาธารณะ — นำเข้าทะเบียนคดีบนเครื่องของสำนักงาน</p>
      )}
      <div className="flex flex-wrap gap-2">
        <button onClick={() => downloadFile('/court-cases/imports/template', 'แม่แบบนำเข้าทะเบียนคดี.xlsx').catch(setError)}
          className="px-3 py-2 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 text-sm flex items-center gap-1.5">
          <Download className="w-4 h-4" /> ดาวน์โหลดแม่แบบ
        </button>
        <label className={`px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold flex items-center gap-1.5 cursor-pointer ${busy || opts.public_data_only ? 'opacity-50 pointer-events-none' : ''}`}>
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileSpreadsheet className="w-4 h-4" />} อัปโหลดไฟล์ .xlsx
          <input ref={ref} type="file" accept=".xlsx" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
      </div>
      <ErrorBox error={error} />
      <div className="divide-y divide-slate-100 border border-slate-200 rounded-lg">
        {!list && <div className="p-4"><Loader2 className="w-4 h-4 animate-spin text-slate-400" /></div>}
        {list?.length === 0 && <p className="p-4 text-sm text-slate-400 text-center">ยังไม่มีรายการนำเข้า</p>}
        {list?.map((r) => (
          <button key={r.import_id} onClick={() => setOpen(r.import_id)} className="w-full text-left px-4 py-3 hover:bg-slate-50 flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-xs text-slate-400 w-10">#{r.import_id}</span>
            <Badge m={STATUS} k={r.status} />
            <span className="text-sm text-slate-800 font-medium flex-1 min-w-[12rem] truncate">{r.original_name}</span>
            <span className="text-[11px] text-slate-500">
              เพิ่ม {r.counts.create} · ปรับปรุง {r.counts.update} · กระบวนพิจารณา {r.counts.proceedings}
              {r.error_count ? ` · ผิด ${r.error_count} แถว` : ''} · {r.created_by} · {when(r.created_at)}
            </span>
            {r.can_approve && <span className="text-[11px] px-1.5 py-0.5 rounded bg-brand-600 text-white">รอคุณอนุมัติได้</span>}
          </button>
        ))}
      </div>
    </Card>
  );
}

function ImportDetail({ id, me, notify, onBack }) {
  const [r, setR] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [onlyIssues, setOnlyIssues] = useState(false);
  useEffect(() => { api.caseImport(id).then(setR).catch(setError); }, [id]);
  const run = async (fn, msg) => {
    setBusy(true); setError(null);
    try { setR(await fn()); notify?.(msg); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  if (!r) return error ? <ErrorBox error={error} /> : <Loader2 className="w-5 h-5 animate-spin text-slate-400" />;
  const cases = onlyIssues ? r.cases.filter((c) => c.errors.length || c.warnings.length) : r.cases;
  const procs = onlyIssues ? r.proceedings.filter((p) => p.errors.length) : r.proceedings;
  const why = r.status === 'submitted' && !r.can_approve ? 'คุณเป็นผู้นำเข้าหรือผู้ส่งตรวจ — ให้นิติกรหรือ ผอ.คบ. คนอื่นอนุมัติ (หลักสี่ตา)' : '';
  return (
    <Card className="p-5 sm:p-6 space-y-4">
      <button onClick={onBack} className="text-sm text-brand-700 hover:underline flex items-center gap-1"><ArrowLeft className="w-4 h-4" /> รายการนำเข้า</button>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-400">#{r.import_id}</span>
        <Badge m={STATUS} k={r.status} />
        <span className="text-sm font-semibold text-slate-800">{r.original_name}</span>
        <span className="ml-auto text-[11px] text-slate-500">
          นำเข้าโดย {r.created_by} · {when(r.created_at)}
          {r.submitted_by ? ` · ส่งตรวจโดย ${r.submitted_by}` : ''}{r.reviewed_by ? ` · ${r.status === 'applied' ? 'อนุมัติ' : 'ไม่รับ'}โดย ${r.reviewed_by}` : ''}
        </span>
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="px-2 py-1 rounded bg-emerald-50 text-emerald-800">เพิ่มใหม่ {r.counts.create}</span>
        <span className="px-2 py-1 rounded bg-amber-50 text-amber-900">ปรับปรุง {r.counts.update}</span>
        <span className="px-2 py-1 rounded bg-slate-100 text-slate-600">ไม่เปลี่ยน {r.counts.unchanged}</span>
        <span className="px-2 py-1 rounded bg-indigo-50 text-indigo-800">กระบวนพิจารณา {r.counts.proceedings}</span>
        {r.error_count > 0 && <span className="px-2 py-1 rounded bg-rose-100 text-rose-800 font-semibold">ผิด {r.error_count} แถว — แก้ในไฟล์แล้วอัปโหลดใหม่</span>}
        <label className="ml-auto flex items-center gap-1 text-slate-600"><input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} /> เฉพาะแถวที่มีปัญหา</label>
      </div>
      {r.result?.warnings?.length > 0 && <ul className="text-[11px] text-amber-800 list-disc pl-5">{r.result.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
      {r.review_note && <p className="text-xs p-2 rounded bg-slate-50 border border-slate-200">หมายเหตุผู้ตรวจ: {r.review_note}</p>}

      <div className="overflow-x-auto border border-slate-200 rounded-lg">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-slate-600">
            <tr><th className="p-2 text-left w-12">แถว</th><th className="p-2 text-left">ผล</th><th className="p-2 text-left">เลขคดีดำ</th>
              <th className="p-2 text-left">ศาล</th><th className="p-2 text-left">เรื่อง</th><th className="p-2 text-left">ข้อผิดพลาด / ข้อสังเกต</th></tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.row} className={`border-t border-slate-100 align-top ${c.errors.length ? 'bg-rose-50/60' : ''}`}>
                <td className="p-2 text-slate-400">{c.row}</td>
                <td className="p-2">{c.errors.length ? <span className="text-rose-700 font-semibold">ผิด</span> : <Badge m={ACTION} k={c.action} />}</td>
                <td className="p-2 font-semibold whitespace-nowrap">{c.values.black_no}</td>
                <td className="p-2 whitespace-nowrap">{c.values.court_name}</td>
                <td className="p-2 min-w-[16rem]">{c.values.subject}</td>
                <td className="p-2 min-w-[14rem]">
                  {c.errors.map((e) => <div key={e} className="text-rose-700">{e}</div>)}
                  {c.warnings.map((w) => <div key={w} className="text-amber-700">{w}</div>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {cases.length === 0 && <p className="p-3 text-xs text-slate-400 text-center">ไม่มีแถว</p>}
      </div>
      {procs.length > 0 && (
        <div className="overflow-x-auto border border-slate-200 rounded-lg">
          <table className="w-full text-xs">
            <thead className="bg-slate-50 text-slate-600">
              <tr><th className="p-2 text-left w-12">แถว</th><th className="p-2 text-left">เลขคดีดำ</th><th className="p-2 text-left">วันที่</th>
                <th className="p-2 text-left">รายการ</th><th className="p-2 text-left">ข้อผิดพลาด</th></tr>
            </thead>
            <tbody>
              {procs.map((p) => (
                <tr key={p.row} className={`border-t border-slate-100 ${p.errors.length ? 'bg-rose-50/60' : ''}`}>
                  <td className="p-2 text-slate-400">{p.row}</td><td className="p-2">{p.black_no}</td><td className="p-2">{p.proceeding_date}</td>
                  <td className="p-2">{p.title}</td><td className="p-2 text-rose-700">{p.errors.join(' · ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {r.status === 'applied' && r.result && (
        <p className="text-xs p-2 rounded bg-emerald-50 border border-emerald-200 text-emerald-900">
          บันทึกแล้ว: เพิ่ม {r.result.created?.length || 0} คดี · ปรับปรุง {r.result.updated?.length || 0} คดี · เพิ่มกระบวนพิจารณา {r.result.proceedings_added || 0} รายการ
        </p>
      )}
      <ErrorBox error={error} />
      {['draft', 'submitted'].includes(r.status) && (
        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-200">
          {r.status === 'draft' && (
            <button onClick={() => run(() => api.submitCaseImport(id), 'ส่งตรวจแล้ว')} disabled={busy || r.error_count > 0}
              className="px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
              <Send className="w-4 h-4" /> ส่งตรวจ
            </button>
          )}
          <input className={`${inputCls} flex-1 min-w-[12rem]`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="หมายเหตุ (ไม่บังคับ)" />
          <button onClick={() => run(() => api.rejectCaseImport(id, note), r.status === 'draft' ? 'ยกเลิกแล้ว' : 'บันทึกว่าไม่รับแล้ว')} disabled={busy}
            className="px-3 py-2 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 text-sm flex items-center gap-1.5">
            <XCircle className="w-4 h-4" /> {r.status === 'draft' ? 'ยกเลิก' : 'ไม่รับ'}
          </button>
          {r.status === 'submitted' && (
            <button onClick={() => run(() => api.approveCaseImport(id, note), 'อนุมัติและบันทึกแล้ว')} disabled={busy || !r.can_approve} title={why}
              className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
              <CheckCircle2 className="w-4 h-4" /> อนุมัติและบันทึก
            </button>
          )}
          {why && <span className="w-full text-[11px] text-slate-500">{why}</span>}
        </div>
      )}
    </Card>
  );
}
