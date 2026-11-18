import React, { useEffect, useState } from 'react';
import { AlertTriangle, BookOpen, ChevronRight, FileText, Filter, Layers, Download, Search, X } from 'lucide-react';
import { api, downloadRegisterExport } from '../../api.js';
import { Card, ErrorBox, inputCls, Label, Spinner, thDate, useDebounced } from '../../components/ui.jsx';

export const REG_STATUS = {
  IN_FORCE: { label: 'ใช้บังคับ', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  REPEALED: { label: 'ยกเลิก', cls: 'bg-rose-50 text-rose-700 border-rose-200' },
  UNKNOWN: { label: 'ไม่ระบุสถานะ', cls: 'bg-slate-100 text-slate-500 border-slate-200' }
};
const ATTACH = {
  pdf: 'PDF', word: 'Word', erc_resolution: 'มติ กกพ.', legal_subc_resolution: 'มติ อนุ กม.',
  subc_resolution: 'มติอนุกรรมการ', internal_opinion: 'ตอบข้อหารือภายใน', external_consult: 'หารือหน่วยงานภายนอก',
  other_docs: 'เอกสารอื่น'
};
const PAGE = 50;

export function StatusPill({ status, assumed }) {
  if (status === 'IN_FORCE' && assumed) {
    return (
      <span
        title="ทะเบียนไม่ระบุสถานะ ระบบถือว่าใช้บังคับไว้ก่อน — รอฝ่ายกฎหมายยืนยัน"
        className="inline-flex px-2 py-0.5 rounded border border-dashed text-[10px] font-semibold whitespace-nowrap bg-white text-emerald-700 border-emerald-300"
      >
        ใช้บังคับ*
      </span>
    );
  }
  const s = REG_STATUS[status] || REG_STATUS.UNKNOWN;
  return <span className={`inline-flex px-2 py-0.5 rounded border text-[10px] font-semibold whitespace-nowrap ${s.cls}`}>{s.label}</span>;
}

/** ทะเบียนคุมกฎหมายลำดับรอง (นำเข้าจาก Excel ฝ่ายกฎหมาย) */
export default function RegisterPanel({ me, initialId, go, initialFilter }) {
  const internal = me.role !== 'PUB';
  const [query, setQuery] = useState('');
  const [f, setF] = useState({ doc_type: '', status: '', section: '', unit: '', issues_only: false, has_text: '' });
  const [exporting, setExporting] = useState(false);
  useEffect(() => {
    if (initialFilter?.section || initialFilter?.issues)
      setF((x) => ({ ...x, section: initialFilter.section ? String(initialFilter.section) : x.section, issues_only: !!initialFilter.issues || x.issues_only }));
  }, [initialFilter?.nonce]);
  const doExport = async () => {
    setExporting(true);
    try {
      await downloadRegisterExport({ doc_type: f.doc_type, status: f.status, section: f.section, has_text: f.has_text === '' ? 'false' : f.has_text });
    } catch (e) {
      setError(e);
    } finally {
      setExporting(false);
    }
  };
  const [data, setData] = useState(null);
  const [limit, setLimit] = useState(PAGE);
  const [selected, setSelected] = useState(initialId || null);
  const [error, setError] = useState(null);
  const q = useDebounced(query);

  useEffect(() => setLimit(PAGE), [q, f]);
  useEffect(() => {
    setError(null);
    api
      .register({ q, ...f, issues_only: f.issues_only || undefined, has_text: f.has_text || undefined, limit })
      .then(setData)
      .catch(setError);
  }, [q, f, limit]);
  useEffect(() => {
    if (initialId) setSelected(initialId);
  }, [initialId]);

  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const facets = data?.facets;
  const meta = facets?.meta;

  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  if (!meta?.total) {
    return (
      <Card className="p-8 text-center space-y-2">
        <FileText className="w-8 h-8 text-slate-300 mx-auto" />
        <div className="text-sm font-semibold text-slate-700">ยังไม่ได้นำเข้าทะเบียนกฎหมายลำดับรอง</div>
        <p className="text-xs text-slate-500">
          วางไฟล์ Excel ที่ <code className="bg-slate-100 px-1 rounded">data/register.xlsx</code> แล้วรัน{' '}
          <code className="bg-slate-100 px-1 rounded">python -m app.seed.import_register /data/register.xlsx</code>
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card className="p-4 sm:p-5 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-sm font-bold text-slate-900">ทะเบียนคุมกฎหมายลำดับรองที่ออกโดยอาศัยอำนาจ พ.ร.บ.การประกอบกิจการพลังงาน พ.ศ. 2550</div>
            <div className="text-[11px] text-slate-500">
              {meta.total} ระเบียน · {meta.groups} กลุ่มเรื่อง · นำเข้าจาก {meta.source_file} เมื่อ {thDate(meta.imported_at?.slice(0, 10))} · มีตัวบทเต็มในระบบ {meta.with_text} ระเบียน
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {facets.statuses.map((s) => (
              <button
                key={s.key}
                onClick={() => setF({ ...f, status: f.status === s.key ? '' : s.key })}
                className={`px-2.5 py-1 rounded-full border text-[11px] font-semibold ${
                  f.status === s.key ? 'bg-brand-600 text-white border-brand-600' : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
                }`}
              >
                {(REG_STATUS[s.key] || REG_STATUS.UNKNOWN).label} {s.n}
                {s.assumed > 0 && <span className="font-normal opacity-70"> (* สันนิษฐาน {s.assumed})</span>}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-end">
          <div className="md:col-span-5">
            <Label>ค้นจากชื่อกฎหมาย</Label>
            <div className="relative">
              <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="เช่น โซลาร์รูฟท็อป, VSPP, ค่าธรรมเนียม, การอุทธรณ์"
                className={`${inputCls} pl-10`}
              />
            </div>
          </div>
          <div className="md:col-span-3">
            <Label>ประเภท</Label>
            <select value={f.doc_type} onChange={set('doc_type')} className={inputCls}>
              <option value="">ทุกประเภท</option>
              {facets.doc_types.map((d) => (
                <option key={d.key} value={d.key}>
                  {d.key} ({d.n})
                </option>
              ))}
            </select>
          </div>
          <div className={internal ? 'md:col-span-2' : 'md:col-span-4'}>
            <Label>อาศัยอำนาจมาตรา</Label>
            <select value={f.section} onChange={set('section')} className={inputCls}>
              <option value="">ทุกมาตรา</option>
              {facets.sections.map((s) => (
                <option key={s.key} value={s.key}>
                  มาตรา {s.key} ({s.n})
                </option>
              ))}
            </select>
          </div>
          {internal && (
            <div className="md:col-span-2">
              <Label>ฝ่ายงาน</Label>
              <select value={f.unit} onChange={set('unit')} className={inputCls}>
                <option value="">ทุกฝ่าย</option>
                {(facets.units || []).map((u) => (
                  <option key={u.key} value={u.key}>
                    {u.key} ({u.n})
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-slate-500">ตัวบทเต็มในระบบ:</span>
          {[['', `ทั้งหมด (${meta.total})`], ['true', `มีแล้ว (${meta.with_text})`], ['false', `ยังไม่มี (${meta.total - meta.with_text})`]].map(([v, label]) => (
            <button key={v || 'all'} onClick={() => setF({ ...f, has_text: v })}
              className={`px-2.5 py-1 rounded-full text-[11px] font-medium border ${
                f.has_text === v ? 'bg-brand-600 text-white border-brand-600' : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'}`}>
              {label}
            </button>
          ))}
          {internal && (
            <label className="inline-flex items-center gap-2 text-[11px] text-slate-600 cursor-pointer ml-2">
              <input type="checkbox" checked={f.issues_only} onChange={set('issues_only')} className="accent-amber-600" />
              แสดงเฉพาะระเบียนที่ข้อมูลต้องตรวจ ({meta.with_issues})
            </label>
          )}
          {internal && (
            <button onClick={doExport} disabled={exporting}
              className="ml-auto px-3 py-1.5 rounded-lg text-[11px] font-semibold border border-slate-300 bg-white hover:bg-slate-50 disabled:opacity-50 flex items-center gap-1.5">
              <Download className="w-3.5 h-3.5" />
              {exporting ? 'กำลังสร้างไฟล์…' : f.has_text === 'true' ? 'ส่งออก Excel (ตามตัวกรอง)' : 'ส่งออกรายการที่ยังไม่มีตัวบท (Excel)'}
            </button>
          )}
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <Card className="lg:col-span-7 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-slate-100 flex items-center justify-between text-[11px] text-slate-500">
            <span className="flex items-center gap-1.5">
              <Filter className="w-3.5 h-3.5" /> พบ {data.total} ระเบียน
            </span>
            {q && <span>เรียงตามความเกี่ยวข้อง</span>}
          </div>
          {data.items.length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-500">ไม่พบระเบียนที่ตรงเงื่อนไข</div>
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.items.map((r) => (
                <li key={r.register_id}>
                  <button
                    onClick={() => setSelected(r.register_id)}
                    className={`w-full text-left px-4 py-3 flex gap-3 hover:bg-amber-50/40 ${selected === r.register_id ? 'bg-amber-50' : ''}`}
                  >
                    <span className="w-12 shrink-0 text-[11px] font-mono text-slate-400 pt-0.5">{r.reg_no}</span>
                    <span className="flex-1 min-w-0">
                      <span className="block text-xs font-medium text-slate-800 leading-snug">{r.title_th}</span>
                      <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
                        <StatusPill status={r.status} assumed={r.status_assumed} />
                        <span>{r.doc_type}</span>
                        {r.authority_secs?.length > 0 && <span>· ม.{r.authority_secs.join(', ')}</span>}
                        {r.effective_date && <span>· ใช้บังคับ {thDate(r.effective_date)}</span>}
                        {internal && r.issues?.length > 0 && (
                          <span className="inline-flex items-center gap-0.5 text-amber-700">
                            <AlertTriangle className="w-3 h-3" /> {r.issues.length}
                          </span>
                        )}
                      </span>
                    </span>
                    <ChevronRight className="w-4 h-4 text-slate-300 shrink-0 mt-0.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {data.items.length < data.total && (
            <div className="p-3 border-t border-slate-100 text-center">
              <button onClick={() => setLimit(limit + PAGE)} className="text-xs font-semibold text-amber-700 hover:underline">
                แสดงเพิ่ม ({data.total - data.items.length} รายการ)
              </button>
            </div>
          )}
        </Card>
        <div className="lg:col-span-5">
          {selected ? (
            <RegisterDetail id={selected} internal={internal} go={go} onSelect={setSelected} onClose={() => setSelected(null)} />
          ) : (
            <Card className="p-6 text-center text-xs text-slate-500">
              <Layers className="w-6 h-6 text-slate-300 mx-auto mb-2" />
              เลือกระเบียนทางซ้ายเพื่อดูรายละเอียดและฉบับที่เกี่ยวข้อง
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, children }) {
  if (children == null || children === '' || (Array.isArray(children) && !children.length)) return null;
  return (
    <div className="grid grid-cols-3 gap-2 py-1.5 border-b border-slate-50 last:border-0">
      <dt className="text-[11px] text-slate-500">{k}</dt>
      <dd className="col-span-2 text-xs text-slate-800">{children}</dd>
    </div>
  );
}

function RegisterDetail({ id, internal, go, onSelect, onClose }) {
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    setD(null);
    setError(null);
    api.registerItem(id).then(setD).catch(setError);
  }, [id]);
  if (error) return <ErrorBox error={error} />;
  if (!d) return <Spinner />;
  const r = d.item;
  const attach = Object.entries(r.attachments || {});
  return (
    <Card className="p-5 space-y-4 lg:sticky lg:top-36">
      <div className="flex items-start justify-between gap-2">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <span className="font-mono">ลำดับ {r.reg_no}</span>
            <StatusPill status={r.status} assumed={r.status_assumed} />
          </div>
          <div className="text-sm font-bold text-slate-900 leading-snug">{r.title_th}</div>
          {d.group_title && <div className="text-[11px] text-slate-500">กลุ่ม: {d.group_title}</div>}
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
          <X className="w-4 h-4" />
        </button>
      </div>
      <dl>
        <Row k="ประเภท">{r.doc_type}</Row>
        <Row k="บทอาศัยอำนาจ">
          {r.authority_text ? (
            <span className="space-y-1.5 block">
              <span className="block">พ.ร.บ.การประกอบกิจการพลังงาน พ.ศ. 2550 มาตรา {r.authority_text}</span>
              <span className="flex flex-wrap gap-1.5">
                {r.authority_secs.map((s) => (
                  <button
                    key={s}
                    onClick={() => go('law', { tab: 'provision', eli: `eli/th/act/2550/energy-industry/sec-${s}` })}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-amber-300 bg-amber-50 text-[11px] font-semibold text-amber-900 hover:bg-amber-100"
                  >
                    <BookOpen className="w-3 h-3" /> เปิดตัวบทมาตรา {s}
                  </button>
                ))}
              </span>
            </span>
          ) : null}
        </Row>
        <Row k="วันใช้บังคับ">{r.effective_date ? thDate(r.effective_date) : r.effective_text}</Row>
        <Row k="สถานะในทะเบียน">
          {r.status_text || (r.status_assumed ? 'ไม่ระบุ — ระบบถือว่าใช้บังคับไว้ก่อน (รอฝ่ายกฎหมายยืนยัน)' : r.status === 'UNKNOWN' ? 'ไม่ระบุ' : null)}
        </Row>
        {internal && (
          <>
            <Row k="ฝ่ายงานผู้รับผิดชอบ">{r.owner_unit}</Row>
            <Row k="รอบประเมินผลสัมฤทธิ์">{r.review_text}</Row>
            <Row k="การรับฟังความคิดเห็น">{r.hearing_text}</Row>
            <Row k="หมายเหตุ">{r.note}</Row>
            <Row k="เอกสารในคลัง">{attach.length ? attach.map(([k, n]) => `${ATTACH[k] || k} ${n}`).join(' · ') : null}</Row>
          </>
        )}
      </dl>
      <div className="flex items-start gap-2 p-3 rounded-lg bg-slate-50 border border-slate-200 text-[11px] text-slate-600">
        <BookOpen className="w-4 h-4 shrink-0 text-slate-400" />
        {r.instrument_eli && d.first_provision_eli ? (
          <span className="flex-1 flex flex-wrap items-center justify-between gap-2">
            <span>มีตัวบทเต็มในระบบ (นำเข้าจากราชกิจจานุเบกษา)</span>
            <button
              onClick={() => go('law', { tab: 'provision', eli: d.first_provision_eli })}
              className="px-3 py-1 rounded-lg text-[11px] font-semibold bg-brand-600 text-white hover:bg-brand-700"
            >
              เปิดตัวบทเต็ม
            </button>
          </span>
        ) : (
          'ยังไม่มีตัวบทเต็มในระบบ — ระเบียนนี้มาจากทะเบียนคุม ใช้ตรวจชื่อ สถานะ และบทอาศัยอำนาจ'
        )}
      </div>
      {internal && r.issues?.length > 0 && (
        <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 space-y-1">
          <div className="text-[11px] font-bold text-amber-900 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" /> ข้อมูลที่ควรตรวจในทะเบียน (แถว {r.sheet_row} ของ {r.source_file})
          </div>
          <ul className="list-disc pl-5 text-[11px] text-amber-900 space-y-0.5">
            {r.issues.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        </div>
      )}
      {d.related.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-bold text-slate-700">ระเบียนในกลุ่มเดียวกัน ({d.related.length})</div>
          <ul className="max-h-72 overflow-y-auto divide-y divide-slate-100 border border-slate-100 rounded-lg">
            {d.related.map((x) => (
              <li key={x.register_id}>
                <button onClick={() => onSelect(x.register_id)} className="w-full text-left px-3 py-2 hover:bg-slate-50 flex gap-2">
                  <span className="w-12 shrink-0 text-[10px] font-mono text-slate-400 pt-0.5">{x.reg_no}</span>
                  <span className="flex-1 text-[11px] text-slate-700 leading-snug">
                    {x.title_th}
                    <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-slate-500">
                      <StatusPill status={x.status} assumed={x.status_assumed} />
                      {x.effective_date && thDate(x.effective_date)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
