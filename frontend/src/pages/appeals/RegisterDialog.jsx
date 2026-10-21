import React, { useState } from 'react';
import { X } from 'lucide-react';
import { api } from '../../api.js';
import { ErrorBox, todayIso } from '../../components/ui.jsx';
import { btnGhost, btnPrimary, CHANNEL, COMPLAINT_BASIS, L, smallInput, TARGET } from './parts.jsx';

const RECEIVED_HINT = {
  COUNTER: 'วันที่ยื่นต่อสำนักงาน กกพ.',
  POST: 'วันที่ที่ทำการไปรษณีย์ต้นทางประทับตรารับที่ซอง',
  ELECTRONIC: 'วันที่สำนักงาน กกพ. ได้รับเอกสารทางอิเล็กทรอนิกส์'
};

/** ข้อ 5.1 รับเรื่องและลงทะเบียน */
export default function RegisterDialog({ sectors, onClose, onCreated }) {
  const [f, setF] = useState({
    case_type: 'APPEAL', target_type: 'ORDER', complaint_basis: 'PPA', filing_channel: 'ELECTRONIC', esarabun_no: '',
    received_date: todayIso(), notice_date: '', incident_date: '', subject: '', summary: '', party_code: '', party_label: '',
    respondent: 'เลขาธิการสำนักงานคณะกรรมการกำกับกิจการพลังงาน', energy_sector: sectors[0] || 'กิจการไฟฟ้า',
    sla_days: '', classification: 'internal'
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const isAppeal = f.case_type === 'APPEAL';

  const submit = async () => {
    setBusy(true);
    setError(null);
    const body = {
      ...f,
      target_type: isAppeal ? f.target_type : null,
      complaint_basis: isAppeal ? null : f.complaint_basis,
      notice_date: isAppeal && f.notice_date ? f.notice_date : null,
      incident_date: f.incident_date || null,
      esarabun_no: f.esarabun_no || null,
      sla_days: !isAppeal && f.sla_days ? Number(f.sla_days) : null,
      party_label: f.party_label || null,
      summary: f.summary || null
    };
    try {
      const r = await api.registerAppeal(body);
      onCreated(r.appeal_id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/60 flex items-start sm:items-center justify-center p-4 overflow-y-auto">
      <div className="bg-white rounded-2xl max-w-2xl w-full p-6 shadow-xl border border-slate-200 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <span className="font-bold text-slate-900">รับเรื่องและลงทะเบียน (WI-EFL-04 ข้อ 5.1)</span>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex gap-2">
          {[
            ['APPEAL', 'อุทธรณ์ ตาม ม.121'],
            ['COMPLAINT', 'เรื่องร้องเรียน / ข้อพิพาทตามสัญญา']
          ].map(([k, v]) => (
            <button key={k} onClick={() => setF({ ...f, case_type: k })} className={`flex-1 px-3 py-2 rounded-lg text-xs font-semibold border ${f.case_type === k ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-700 border-slate-300'}`}>
              {v}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {isAppeal ? (
            <div>
              <L>อุทธรณ์</L>
              <select value={f.target_type} onChange={set('target_type')} className={smallInput}>
                {Object.entries(TARGET).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div>
              <L>ประเภทเรื่องร้องเรียน</L>
              <select value={f.complaint_basis} onChange={set('complaint_basis')} className={smallInput}>
                {Object.entries(COMPLAINT_BASIS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div>
            <L>ช่องทางที่ยื่น</L>
            <select value={f.filing_channel} onChange={set('filing_channel')} className={smallInput}>
              {Object.entries(CHANNEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <div>
            <L hint={RECEIVED_HINT[f.filing_channel]}>วันที่ยื่น{isAppeal ? 'อุทธรณ์' : 'เรื่องร้องเรียน'}</L>
            <input type="date" value={f.received_date} max={todayIso()} onChange={set('received_date')} className={smallInput} />
          </div>
          {isAppeal ? (
            <div>
              <L hint="ใช้ตรวจกำหนด 30 วัน">{f.target_type === 'RULE' ? 'วันที่กฎประกาศ/ใช้บังคับ' : 'วันที่ได้รับแจ้งคำสั่ง (ใบตอบรับไปรษณีย์)'}</L>
              <input type="date" value={f.notice_date} max={todayIso()} onChange={set('notice_date')} className={smallInput} />
            </div>
          ) : (
            <div>
              <L hint="เว้นว่างถ้าไม่มีกำหนด">กรอบเวลาพิจารณาตามระเบียบ/ประกาศ/สัญญา (วัน)</L>
              <input type="number" min="1" value={f.sla_days} onChange={set('sla_days')} className={smallInput} />
            </div>
          )}
          <div>
            <L>เลขรับ E-Sarabun</L>
            <input value={f.esarabun_no} onChange={set('esarabun_no')} className={smallInput} />
          </div>
          <div>
            <L>วันเกิดเหตุ (ใช้ผูกตัวบทรุ่นที่ถูกต้อง)</L>
            <input type="date" value={f.incident_date} max={todayIso()} onChange={set('incident_date')} className={smallInput} />
          </div>
          <div className="sm:col-span-2">
            <L>เรื่อง</L>
            <input value={f.subject} onChange={set('subject')} className={smallInput} />
          </div>
          <div className="sm:col-span-2">
            <L>สรุปข้อโต้แย้ง</L>
            <textarea rows={2} value={f.summary} onChange={set('summary')} className={smallInput} />
          </div>
          <div>
            <L hint="ชื่อจริงบันทึกแยกใน party-vault">รหัสคู่กรณี</L>
            <input value={f.party_code} onChange={set('party_code')} className={smallInput} placeholder="เช่น EL-SPP-118" />
          </div>
          <div>
            <L>คำอธิบายคู่กรณี (ไม่มีชื่อจริง)</L>
            <input value={f.party_label} onChange={set('party_label')} className={smallInput} placeholder="เช่น ผู้รับใบอนุญาตผลิตไฟฟ้า รหัส [EL-SPP-118]" />
          </div>
          <div>
            <L>ประเภทกิจการ</L>
            <select value={f.energy_sector} onChange={set('energy_sector')} className={smallInput}>
              {sectors.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
          <div>
            <L>ชั้นความลับ</L>
            <select value={f.classification} onChange={set('classification')} className={smallInput}>
              <option value="internal">ใช้ภายในสำนักงาน</option>
              <option value="restricted">ชั้นความลับ (เฉพาะผู้รับผิดชอบ)</option>
            </select>
          </div>
        </div>
        <ErrorBox error={error} />
        <div className="flex justify-end gap-2">
          <button className={btnGhost} onClick={onClose}>
            ยกเลิก
          </button>
          <button className={btnPrimary} disabled={busy || f.subject.trim().length < 5 || f.party_code.trim().length < 2} onClick={submit}>
            ลงทะเบียนและส่ง ผอ.คบ. มอบหมาย
          </button>
        </div>
      </div>
    </div>
  );
}
