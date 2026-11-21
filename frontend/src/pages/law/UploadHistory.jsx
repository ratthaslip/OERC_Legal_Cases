import { useEffect, useState } from 'react';
import { Check, GitCompare, History, Loader2 } from 'lucide-react';
import { api } from '../../api.js';
import { Card, ErrorBox } from '../../components/ui.jsx';

const when = (iso) => (iso ? new Date(iso).toLocaleString('th-TH', { dateStyle: 'medium', timeStyle: 'short' }) : '');

/** ขั้นตอนนำเข้า: ถอดข้อความ → ร่าง → ส่งตรวจ → เผยแพร่ */
const STEPS = [
  ['processing', 'ถอดข้อความ'],
  ['draft', 'ตรวจทาน/แก้ร่าง'],
  ['submitted', 'ส่งตรวจ'],
  ['approved', 'อนุมัติและเผยแพร่']
];
export function WorkflowSteps({ status }) {
  const at = { processing: 0, failed: 0, draft: 1, rejected: 1, submitted: 2, approved: 3, superseded: 3 }[status] ?? 0;
  return (
    <ol className="flex flex-wrap items-center gap-1 text-[11px]">
      {STEPS.map(([k, label], i) => (
        <li key={k} className="flex items-center gap-1">
          <span className={`flex items-center gap-1 px-2 py-0.5 rounded-full border ${
            i < at ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
              : i === at ? 'border-brand-500 bg-brand-600 text-white font-semibold'
                : 'border-slate-200 text-slate-400'}`}>
            {i < at ? <Check className="w-3 h-3" /> : <span className="w-3 text-center">{i + 1}</span>}{label}
          </span>
          {i < STEPS.length - 1 && <span className="text-slate-300">›</span>}
        </li>
      ))}
    </ol>
  );
}

const KIND = { extract: 'ผลถอดข้อความ', reprocess: 'ถอดข้อความใหม่', correction: 'ตั้งต้นจากฉบับที่เผยแพร่', edit: 'แก้ร่าง' };
const FIELD = {
  title: 'ชื่อเต็ม', code: 'ชื่อย่อ', category: 'หมวดหมู่', classification: 'ชั้นข้อมูล',
  effectiveDate: 'วันใช้บังคับ', publishedDate: 'วันประกาศ', source: 'แหล่งที่มา'
};
const CHANGE = {
  added: ['เพิ่ม', 'bg-emerald-100 text-emerald-800'],
  removed: ['ลบ', 'bg-rose-100 text-rose-800'],
  changed: ['แก้', 'bg-amber-100 text-amber-900']
};

function summaryText(s) {
  if (!s) return '';
  const parts = [];
  if (s.fields?.length) parts.push(s.fields.map((f) => FIELD[f] || f).join(', '));
  if (s.changed?.length) parts.push(`แก้ ${s.changed.join(', ')}`);
  if (s.added?.length) parts.push(`เพิ่ม ${s.added.join(', ')}`);
  if (s.removed?.length) parts.push(`ลบ ${s.removed.join(', ')}`);
  if (s.method) parts.push(s.method);
  if (s.from_upload) parts.push(`จากรายการ #${s.from_upload}`);
  return parts.join(' · ');
}

/** ประวัติร่างทุกรุ่น + เทียบความต่าง (ค่าเริ่มต้น: ผลถอดข้อความ/ฉบับเผยแพร่ → ร่างล่าสุด) */
export function UploadHistory({ id, version }) {
  const [revs, setRevs] = useState(null);
  const [pick, setPick] = useState(null);   // {base, head} หรือ null = ค่าเริ่มต้น
  const [diff, setDiff] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => { api.uploadRevisions(id).then((r) => setRevs(r.items)).catch(setError); }, [id, version]);
  useEffect(() => {
    setDiff(null);
    api.uploadDiff(id, pick?.base, pick?.head).then(setDiff).catch((e) => (e.status === 404 ? setDiff(false) : setError(e)));
  }, [id, version, pick]);

  return (
    <div className="grid lg:grid-cols-3 gap-4">
      <Card className="p-4 lg:col-span-2 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <GitCompare className="w-4 h-4 text-brand-600" />
          <h3 className="font-semibold text-sm text-slate-800">สิ่งที่แก้</h3>
          {diff && (
            <span className="text-[11px] text-slate-500">
              รุ่น {diff.base.rev_no} ({KIND[diff.base.kind]}) → รุ่น {diff.head.rev_no} ({KIND[diff.head.kind]} · {diff.head.actor})
            </span>
          )}
          {pick && <button onClick={() => setPick(null)} className="ml-auto text-[11px] text-brand-700 hover:underline">เทียบกับผลถอดข้อความ</button>}
        </div>
        {error && <ErrorBox error={error} />}
        {diff === null && !error && <Loader2 className="w-4 h-4 animate-spin text-slate-400" />}
        {diff === false && <p className="text-xs text-slate-500">ยังไม่มีประวัติร่าง</p>}
        {diff && <DiffView diff={diff} />}
      </Card>
      <Card className="p-4 space-y-2">
        <div className="flex items-center gap-2">
          <History className="w-4 h-4 text-slate-500" />
          <h3 className="font-semibold text-sm text-slate-800">ประวัติร่าง ({revs?.length ?? 0} รุ่น)</h3>
        </div>
        <p className="text-[11px] text-slate-500">บันทึกทุกครั้งที่ถอดข้อความหรือบันทึกร่าง — แก้หรือลบย้อนหลังไม่ได้ คลิกเพื่อดูว่ารุ่นนั้นแก้อะไร</p>
        <ol className="space-y-1 max-h-96 overflow-auto">
          {(revs || []).slice().reverse().map((r) => {
            const active = pick?.head === r.rev_no;
            return (
              <li key={r.rev_no}>
                <button disabled={r.rev_no === 1} onClick={() => setPick({ base: r.rev_no - 1, head: r.rev_no })}
                  className={`w-full text-left p-2 rounded border text-xs ${active ? 'border-brand-400 bg-brand-50' : 'border-slate-200 hover:bg-slate-50'} disabled:hover:bg-white disabled:cursor-default`}>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-slate-700">รุ่น {r.rev_no}</span>
                    <span className="text-slate-600">{KIND[r.kind] || r.kind}</span>
                    <span className="ml-auto text-slate-400">{r.actor === 'system' ? 'ระบบ' : r.actor}</span>
                  </div>
                  <div className="text-[11px] text-slate-400">{when(r.created_at)}</div>
                  {summaryText(r.summary) && <div className="text-[11px] text-slate-600 mt-0.5">{summaryText(r.summary)}</div>}
                </button>
              </li>
            );
          })}
        </ol>
      </Card>
    </div>
  );
}

const show = (v) => (v === null || v === undefined || v === '' ? '—' : String(v));

export function DiffView({ diff }) {
  if (!diff.fields.length && !diff.provisions.length) {
    return <p className="text-xs text-slate-500">ไม่มีความต่างระหว่างสองรุ่นนี้</p>;
  }
  return (
    <div className="space-y-3">
      {diff.fields.length > 0 && (
        <table className="w-full text-xs border border-slate-200 rounded">
          <thead className="bg-slate-50 text-slate-600">
            <tr><th className="text-left p-2 w-28">ข้อมูล</th><th className="text-left p-2">เดิม</th><th className="text-left p-2">ใหม่</th></tr>
          </thead>
          <tbody>
            {diff.fields.map((f) => (
              <tr key={f.field} className="border-t border-slate-100 align-top">
                <td className="p-2 text-slate-600">{FIELD[f.field] || f.field}</td>
                <td className="p-2"><del className="bg-rose-50 text-rose-800">{show(f.old)}</del></td>
                <td className="p-2"><ins className="no-underline bg-emerald-50 text-emerald-800">{show(f.new)}</ins></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {diff.provisions.map((p) => (
        <div key={`${p.change}-${p.key}`} className="border border-slate-200 rounded-lg p-3 space-y-1.5">
          <div className="flex items-center gap-2 text-xs">
            <span className={`px-1.5 py-0.5 rounded font-medium ${CHANGE[p.change][1]}`}>{CHANGE[p.change][0]}</span>
            <span className="font-semibold text-slate-800">{p.key}</span>
            {p.part_changed && <span className="text-slate-500">หมวด: {show(p.old.part)} → {show(p.new.part)}</span>}
          </div>
          <p className="text-sm leading-relaxed whitespace-pre-wrap text-slate-800">
            {p.segments.map((s, i) => (s.op === 'eq' ? <span key={i}>{s.text}</span>
              : s.op === 'del' ? <del key={i} className="bg-rose-100 text-rose-800">{s.text}</del>
                : <ins key={i} className="no-underline bg-emerald-100 text-emerald-900">{s.text}</ins>))}
          </p>
        </div>
      ))}
    </div>
  );
}
