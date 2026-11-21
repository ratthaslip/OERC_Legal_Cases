import { useMemo, useState } from 'react';
import { ArrowRight, FileText, GitBranch, Layers, Paperclip, Search } from 'lucide-react';
import { openRawFile } from '../../api.js';
import { StatusPill } from './RegisterPanel.jsx';
import { thDate } from '../../components/ui.jsx';

const SHOW = 8;

/** กฎหมายลำดับรองที่ออกโดยอาศัยอำนาจตามมาตรานี้ (จากบทอาศัยอำนาจในทะเบียนกฎหมายลำดับรอง) */
export function SubordinatePanel({ provisionNo, items, asOf, onOpenText, onOpenRegister }) {
  const [q, setQ] = useState('');
  const [type, setType] = useState('');
  const [showRepealed, setShowRepealed] = useState(false);
  const [all, setAll] = useState(false);

  const repealed = items.filter((i) => i.status === 'REPEALED').length;
  const types = useMemo(() => {
    const m = {};
    items.forEach((i) => (m[i.doc_type || 'อื่น ๆ'] = (m[i.doc_type || 'อื่น ๆ'] || 0) + 1));
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  }, [items]);
  const list = items.filter(
    (i) =>
      (showRepealed || i.status !== 'REPEALED') &&
      (!type || (i.doc_type || 'อื่น ๆ') === type) &&
      (!q.trim() || `${i.reg_no} ${i.title_th}`.includes(q.trim()))
  );
  const shown = all ? list : list.slice(0, SHOW);

  return (
    <div className="mt-6 pt-5 border-t border-slate-200">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h3 className="text-xs sm:text-sm font-bold text-slate-900 flex items-center gap-2">
          <Layers className="w-4 h-4 text-amber-600" />
          <span>
            กฎหมายลำดับรองที่ออกโดยอาศัยอำนาจตาม {provisionNo} ({items.length} ฉบับ
            {repealed > 0 && ` · ยกเลิกแล้ว ${repealed}`})
          </span>
        </h3>
        <span className="text-[11px] text-slate-500">ที่มา: บทอาศัยอำนาจในทะเบียนกฎหมายลำดับรอง</span>
      </div>

      {items.length > SHOW && (
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="กรองตามชื่อหรือเลขทะเบียน"
              className="w-full pl-8 pr-3 py-1.5 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-500"
            />
          </div>
          {types.length > 1 &&
            [['', `ทั้งหมด`], ...types.map(([t, n]) => [t, `${t} ${n}`])].map(([t, label]) => (
              <button
                key={t || 'all'}
                onClick={() => setType(t)}
                className={`px-2.5 py-1 rounded-full text-[11px] font-medium border ${
                  type === t ? 'bg-brand-600 text-white border-brand-600' : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
              >
                {label}
              </button>
            ))}
          {repealed > 0 && (
            <label className="flex items-center gap-1.5 text-[11px] text-slate-600 ml-1">
              <input type="checkbox" checked={showRepealed} onChange={(e) => setShowRepealed(e.target.checked)} />
              แสดงฉบับที่ยกเลิกแล้ว
            </label>
          )}
        </div>
      )}

      <div className="divide-y divide-slate-100 border border-slate-200 rounded-xl overflow-hidden">
        {shown.length === 0 && <p className="text-xs text-slate-500 p-4 text-center">ไม่มีรายการที่ตรงกับตัวกรอง</p>}
        {shown.map((i) => {
          const notYet = i.effective_date && asOf && i.effective_date > asOf;
          return (
            <div key={i.register_id} className="px-4 py-3 flex items-start gap-3 hover:bg-slate-50/60">
              <span className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-mono">#{i.reg_no}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
                  {i.doc_type && <span className="text-[10px] font-semibold text-amber-800 bg-amber-50 px-1.5 py-0.5 rounded">{i.doc_type}</span>}
                  <StatusPill status={i.status} assumed={i.status_assumed} />
                  {notYet && (
                    <span className="text-[10px] font-semibold text-sky-700 bg-sky-50 border border-sky-200 px-1.5 py-0.5 rounded">
                      ยังไม่มีผล ณ วันที่เลือก
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-900 leading-snug line-clamp-2">{i.title_th}</p>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  ใช้บังคับ {i.effective_date ? thDate(i.effective_date) : i.effective_text || '—'} · อาศัยอำนาจ ม.{i.authority_text}
                </p>
              </div>
              <div className="shrink-0 flex flex-col items-end gap-1">
                {i.first_provision_eli ? (
                  <button
                    onClick={() => onOpenText(i.first_provision_eli)}
                    className="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-brand-600 text-white hover:bg-brand-700 flex items-center gap-1"
                  >
                    <FileText className="w-3 h-3" /> ตัวบทเต็ม
                  </button>
                ) : (
                  <span className="text-[10px] text-slate-400">ยังไม่มีตัวบทเต็ม</span>
                )}
                <button onClick={() => onOpenRegister(i.register_id)} className="text-[11px] font-semibold text-amber-700 hover:underline flex items-center gap-0.5">
                  ดูในทะเบียน <ArrowRight className="w-3 h-3" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
      {list.length > SHOW && (
        <button onClick={() => setAll(!all)} className="mt-2 text-[11px] font-semibold text-amber-700 hover:underline">
          {all ? 'แสดงน้อยลง' : `แสดงทั้งหมด ${list.length} ฉบับ`}
        </button>
      )}
    </div>
  );
}

/** ตราสารลำดับรอง: บอกว่าออกโดยอาศัยอำนาจตามมาตราใดของ พ.ร.บ.แม่บท */
export function ParentAuthority({ pa, onOpenText, onOpenRegister }) {
  if (!pa) return null;
  return (
    <div className="mt-3 rounded-lg border border-indigo-200 bg-indigo-50/60 px-4 py-2.5 flex flex-wrap items-center gap-2 text-xs text-indigo-950">
      <Layers className="w-4 h-4 text-indigo-600 shrink-0" />
      <span>ออกโดยอาศัยอำนาจตาม พ.ร.บ.การประกอบกิจการพลังงาน พ.ศ. 2550</span>
      {pa.sections.map((s) =>
        s.eli ? (
          <button key={s.no} onClick={() => onOpenText(s.eli)} className="px-2 py-0.5 rounded bg-white border border-indigo-300 font-semibold hover:bg-indigo-100">
            มาตรา {s.no}
          </button>
        ) : (
          <span key={s.no} className="px-2 py-0.5 rounded bg-white border border-indigo-200">มาตรา {s.no}</span>
        )
      )}
      {pa.authority_text && pa.sections.length === 0 && <span>{pa.authority_text}</span>}
      <button onClick={() => onOpenRegister(pa.register_id)} className="ml-auto text-[11px] font-semibold text-indigo-700 hover:underline">
        ทะเบียน #{pa.reg_no} →
      </button>
    </div>
  );
}

/** ไฟล์ต้นฉบับที่ใช้ทำตัวบทฉบับนี้ (PDF ราชกิจจานุเบกษา / ฉบับรวม) */
export function SourceFiles({ files }) {
  if (!files?.length) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
      <Paperclip className="w-3.5 h-3.5 text-slate-400" />
      <span>ต้นฉบับ:</span>
      {files.map((f) =>
        f.available ? (
          <button
            key={f.sha256}
            onClick={() => openRawFile(f.sha256).catch(() => alert('เปิดไฟล์ไม่สำเร็จ'))}
            title={`SHA-256 ${f.sha256}`}
            className="px-2 py-0.5 rounded border border-slate-300 bg-white hover:bg-slate-50 font-medium text-slate-800"
          >
            {f.role === 'amendment' ? 'ฉบับแก้ไข: ' : ''}
            {f.name}
            <span className="text-slate-500 font-normal">
              {' '}· {f.source}{f.page_count ? ` · ${f.page_count} หน้า` : ''}{f.pages ? ` (${f.pages})` : ''}
            </span>
          </button>
        ) : (
          <span key={f.sha256} title={`SHA-256 ${f.sha256}`} className="px-2 py-0.5 rounded border border-dashed border-slate-300 text-slate-500">
            {f.name} — ยังไม่มีไฟล์ในที่เก็บ
          </span>
        )
      )}
    </div>
  );
}

const REL = {
  out: { refers_to: 'อ้างถึง', amends: 'แก้ไข/เพิ่มเติม', repeals: 'ยกเลิก', remedy: 'ช่องทางต่อไป' },
  in: { refers_to: 'ถูกอ้างโดย', amends: 'ถูกแก้ไข/เพิ่มโดย', repeals: 'ถูกยกเลิกโดย', remedy: 'มาจากขั้นตอน' }
};
const ORDER = ['amends', 'repeals', 'remedy', 'refers_to'];

/** เส้นความสัมพันธ์ของมาตรา/ข้อนี้จากตาราง law_relation (อ้างถึง / แก้ไข / ยกเลิก / ช่องทางเยียวยา) */
export function RelationsPanel({ rel, onOpenText }) {
  if (!rel) return null;
  const groups = [];
  for (const dir of ['in', 'out']) {
    const items = dir === 'in' ? rel.incoming : rel.outgoing;
    for (const t of ORDER) {
      const xs = items.filter((x) => x.rel_type === t);
      if (xs.length) groups.push({ key: `${dir}-${t}`, label: REL[dir][t], auto: xs.every((x) => x.status === 'auto'), xs, dir });
    }
  }
  if (!groups.length) return null;
  return (
    <div className="mt-4 pt-4 border-t border-slate-100">
      <h4 className="text-xs font-bold text-slate-600 tracking-wider mb-2 flex items-center gap-1.5">
        <GitBranch className="w-3.5 h-3.5 text-brand-600" /> ความสัมพันธ์กับบทบัญญัติอื่น
      </h4>
      <div className="space-y-1.5">
        {groups.map((g) => (
          <div key={g.key} className="flex flex-wrap items-center gap-1.5 text-xs text-slate-700 bg-slate-50 px-3 py-2 rounded-lg border border-slate-200/60">
            <span className="px-2 py-0.5 rounded bg-brand-100 text-brand-800 font-semibold text-[11px] shrink-0">{g.label}</span>
            {g.xs.map((x, i) => {
              const target = g.dir === 'in' ? x.src_eli : x.dst_eli;
              const isProv = target && /\/(sec|clause|item)-/.test(target);
              return isProv ? (
                <button key={i} onClick={() => onOpenText(target)} title={x.note || ''}
                  className="px-2 py-0.5 rounded bg-white border border-slate-300 hover:border-brand-400 hover:text-brand-700">
                  {x.label}
                </button>
              ) : (
                <span key={i} className="px-2 py-0.5 rounded bg-white border border-slate-200 text-slate-600" title={x.note || ''}>{x.label}</span>
              );
            })}
            {g.auto && <span className="text-[10px] text-slate-400 ml-auto">สกัดจากถ้อยคำอัตโนมัติ</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
