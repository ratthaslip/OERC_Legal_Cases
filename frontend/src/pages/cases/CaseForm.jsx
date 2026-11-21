import { useState } from 'react';
import { AlertTriangle, Loader2, Save, X } from 'lucide-react';
import { api } from '../../api.js';
import { Card, ErrorBox, Label, inputCls } from '../../components/ui.jsx';

const EMPTY = {
  black_no: '', red_no: '', court_level: 'ศาลปกครองชั้นต้น', court_name: 'ศาลปกครองกลาง', case_category: 'คดีปกครอง',
  subject: '', summary: '', party_code: '', party_label: '', defendant: 'คณะกรรมการกำกับกิจการพลังงาน (ผู้ถูกฟ้องคดี)',
  energy_sector: '', incident_date: '', filed_date: '', judgment_date: '', status: 'PENDING', stage_text: '', result: '',
  classification: 'internal', origin_appeal_no: '', owners: []
};

/** ฟอร์มเพิ่ม/แก้ไขคดี — initial = ข้อมูลคดีเดิม (แก้ไข) หรือ null (เพิ่มใหม่) */
export default function CaseForm({ opts, initial, onSaved, onCancel }) {
  const [f, setF] = useState(() => (initial ? {
    ...EMPTY, ...Object.fromEntries(Object.keys(EMPTY).map((k) => [k, initial[k] ?? EMPTY[k]])),
    origin_appeal_no: initial.origin_appeal_no || '', owners: initial.owners || []
  } : EMPTY));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const toggleOwner = (u) => setF({ ...f, owners: f.owners.includes(u) ? f.owners.filter((x) => x !== u) : [...f.owners, u] });

  const save = async () => {
    setBusy(true); setError(null);
    const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v === '' ? null : v]));
    body.status = f.status; body.classification = f.classification;
    if (!opts.can_assign) delete body.owners;
    try {
      const r = initial ? await api.updateCase(initial.case_id, { ...body, row_version: initial.row_version })
        : await api.createCase(body);
      onSaved(r.case_id, r.changed);
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  // เรียกเป็นฟังก์ชัน (ไม่ใช่ <T/>) เพื่อไม่ให้ช่องกรอกถูกสร้างใหม่และเสียโฟกัสทุกครั้งที่พิมพ์
  const T = ({ k, label, req, className, ...p }) => (
    <div className={className} key={k}>
      <Label>{label}{req && <span className="text-rose-600"> *</span>}</Label>
      <input className={inputCls} value={f[k] ?? ''} onChange={set(k)} {...p} />
    </div>
  );

  return (
    <Card className="p-5 sm:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold text-slate-900">{initial ? `แก้ไขคดีดำที่ ${initial.black_no}` : 'เพิ่มคดี'}</h2>
        <button onClick={onCancel} className="p-1 rounded hover:bg-slate-100" title="ปิด"><X className="w-4 h-4 text-slate-500" /></button>
      </div>
      {opts.public_data_only && (
        <p className="text-xs p-2 rounded bg-amber-50 border border-amber-300 text-amber-900">
          เครื่องนี้เก็บเฉพาะข้อมูลสาธารณะ — คดีที่เพิ่มจะติดป้าย "ข้อมูลสาธิต" ห้ามใส่ข้อมูลคดีจริง
        </p>
      )}
      <p className="text-[11px] flex items-start gap-1 text-slate-600">
        <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
        ห้ามใส่ชื่อจริง เลขประจำตัวประชาชน หรือที่อยู่ของคู่กรณี — ระบบเก็บเฉพาะรหัสนามแฝง
      </p>

      <div className="grid md:grid-cols-4 gap-3">
        {T({ k: 'black_no', label: 'เลขคดีดำ', req: true, placeholder: 'คบ. 425/2568' })}
        {T({ k: 'red_no', label: 'เลขคดีแดง', placeholder: 'เมื่อมีคำพิพากษา' })}
        <div>
          <Label>ชั้นศาล <span className="text-rose-600">*</span></Label>
          <select className={inputCls} value={f.court_level} onChange={set('court_level')}>
            {opts.court_levels.map((x) => <option key={x}>{x}</option>)}
          </select>
        </div>
        <div>
          <Label>ศาล <span className="text-rose-600">*</span></Label>
          <input className={inputCls} list="court-names" value={f.court_name} onChange={set('court_name')} />
          <datalist id="court-names">{opts.courts.map((x) => <option key={x} value={x} />)}</datalist>
        </div>
      </div>
      <div>
        <Label>เรื่อง (ประเด็นพิพาท) <span className="text-rose-600">*</span></Label>
        <input className={inputCls} value={f.subject} onChange={set('subject')} />
      </div>
      <div>
        <Label>สรุปข้อเท็จจริงและสาระสำคัญ</Label>
        <textarea className={inputCls} rows={4} value={f.summary ?? ''} onChange={set('summary')} />
      </div>
      <div className="grid md:grid-cols-4 gap-3">
        {T({ k: 'party_code', label: 'รหัสคู่กรณี (นามแฝง)', req: true, placeholder: 'EL-VSPP-218' })}
        {T({ k: 'party_label', label: 'คำเรียกคู่กรณี', placeholder: 'ผู้ฟ้องคดี (ผู้ผลิตไฟฟ้า รหัส ...)', className: 'md:col-span-2' })}
        <div>
          <Label>ประเภทกิจการพลังงาน <span className="text-rose-600">*</span></Label>
          <input className={inputCls} list="sectors" value={f.energy_sector} onChange={set('energy_sector')} />
          <datalist id="sectors">{opts.sectors.map((x) => <option key={x} value={x} />)}</datalist>
        </div>
        {T({ k: 'defendant', label: 'ผู้ถูกฟ้องคดี', className: 'md:col-span-2' })}
        {T({ k: 'case_category', label: 'ประเภทคดี' })}
        {T({ k: 'origin_appeal_no', label: 'เลขเรื่องอุทธรณ์ต้นเรื่อง', placeholder: 'อธ. 1/2568' })}
        {T({ k: 'incident_date', label: 'วันเกิดเหตุ', type: 'date' })}
        {T({ k: 'filed_date', label: 'วันยื่นฟ้อง', type: 'date' })}
        {T({ k: 'judgment_date', label: 'วันพิพากษา', type: 'date' })}
        <div>
          <Label>สถานะ</Label>
          <select className={inputCls} value={f.status} onChange={set('status')}>
            {Object.entries(opts.statuses).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        {T({ k: 'stage_text', label: 'ขั้นตอนคดี', placeholder: 'อยู่ระหว่างแสวงหาข้อเท็จจริง', className: 'md:col-span-2' })}
        {T({ k: 'result', label: 'ผลคดี', className: 'md:col-span-2' })}
        <div className="md:col-span-2">
          <Label>ชั้นความลับ</Label>
          <select className={inputCls} value={f.classification} onChange={set('classification')}>
            {Object.entries(opts.classes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="md:col-span-2">
          <Label>ผู้รับผิดชอบ</Label>
          {opts.can_assign ? (
            <div className="flex flex-wrap gap-3 pt-2">
              {opts.officers.map((o) => (
                <label key={o.user_id} className="flex items-center gap-1.5 text-xs text-slate-700">
                  <input type="checkbox" checked={f.owners.includes(o.user_id)} onChange={() => toggleOwner(o.user_id)} /> {o.name}
                </label>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500 pt-2">{initial ? (initial.owners.join(', ') || '—') : 'คุณ (ผอ.คบ. เปลี่ยนผู้รับผิดชอบได้)'}</p>
          )}
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="flex gap-2">
        <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} บันทึก
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded-lg border border-slate-300 text-slate-700 text-sm hover:bg-slate-50">ยกเลิก</button>
      </div>
    </Card>
  );
}
