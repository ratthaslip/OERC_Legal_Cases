import React from 'react';
import { Lock, AlertTriangle, Loader2 } from 'lucide-react';

const TH_MONTHS = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];

/** '2019-05-15' → '15 พ.ค. 2562' (เก็บ ค.ศ. แสดง พ.ศ.) */
export function thDate(iso) {
  if (!iso) return '-';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return `${d} ${TH_MONTHS[m - 1]} ${y + 543}`;
}

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

export const SLA_TEXT = {
  ON_TRACK: (s) => `อยู่ในกรอบเวลา (เหลือ ${s.days_left} วัน)`,
  NEAR_DUE: (s) => `ใกล้ครบกำหนด (เหลือ ${s.days_left} วัน)`,
  OVERDUE: (s) => `เกินกำหนด ${Math.abs(s.days_left)} วัน`,
  CLOSED: (s) => (s.days_used != null ? `ดำเนินการเสร็จสิ้น (ใช้เวลา ${s.days_used} วัน)` : 'ดำเนินการเสร็จสิ้น'),
  INCOMPLETE_DATA: () => 'ข้อมูลไม่ครบ (ไม่มีวันรับเรื่อง)',
  NOT_APPLICABLE: () => 'ไม่นับ SLA'
};

const SLA_STYLE = {
  ON_TRACK: 'bg-sky-50 text-sky-800 border-sky-200',
  NEAR_DUE: 'bg-amber-100 text-amber-800 border-amber-300',
  OVERDUE: 'bg-rose-50 text-rose-700 border-rose-300',
  CLOSED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  INCOMPLETE_DATA: 'bg-slate-100 text-slate-600 border-slate-300',
  NOT_APPLICABLE: 'bg-slate-100 text-slate-600 border-slate-300'
};

export function SlaBadge({ sla, size = 'sm' }) {
  if (!sla) return null;
  const cls = size === 'lg' ? 'px-2.5 py-0.5 rounded-full text-xs font-bold whitespace-nowrap' : 'px-2 py-0.5 rounded text-[10px] font-semibold whitespace-nowrap';
  return <span className={`${cls} border ${SLA_STYLE[sla.state]}`}>{SLA_TEXT[sla.state](sla)}</span>;
}

export function ClassBadge({ classification }) {
  if (classification !== 'restricted') return null;
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-rose-50 text-rose-700 border border-rose-200 whitespace-nowrap">
      <Lock className="w-3 h-3" /> ชั้นความลับ
    </span>
  );
}

export function Card({ className = '', children }) {
  return <div className={`bg-white rounded-xl shadow-xs border border-slate-200 ${className}`}>{children}</div>;
}

export function Label({ children }) {
  return <label className="block text-xs font-semibold text-slate-700 mb-1.5">{children}</label>;
}

export const inputCls =
  'w-full px-3 py-2 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-amber-500';

export function Spinner({ text = 'กำลังโหลด…' }) {
  return (
    <div className="flex items-center justify-center gap-2 text-xs text-slate-500 py-10">
      <Loader2 className="w-4 h-4 animate-spin" /> {text}
    </div>
  );
}

export function ErrorBox({ error }) {
  if (!error) return null;
  return (
    <div className="flex items-start gap-2 text-xs text-rose-800 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2.5">
      <AlertTriangle className="w-4 h-4 shrink-0" />
      <span>{error.message || String(error)}</span>
    </div>
  );
}

export function OmittedNote({ count, what = 'เรื่อง' }) {
  if (!count) return null;
  return (
    <div className="flex items-center gap-2 text-[11px] text-slate-600 bg-slate-50 border border-dashed border-slate-300 rounded-lg px-3 py-2">
      <Lock className="w-3.5 h-3.5 text-slate-400" />
      <span>
        มีอีก {count} {what} ที่ตรงกับคำค้นแต่อยู่นอกสิทธิ์การเข้าถึงของคุณ (ระบบไม่แสดงรายละเอียด)
      </span>
    </div>
  );
}

export function LockedPanel({ title, onLogin, onBack, color = 'amber' }) {
  return (
    <div className="bg-white rounded-2xl shadow-xs border border-slate-200 p-10 text-center max-w-xl mx-auto my-8">
      <div
        className={`w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4 border ${
          color === 'indigo' ? 'bg-indigo-50 text-indigo-600 border-indigo-200' : 'bg-amber-50 text-amber-600 border-amber-200'
        }`}
      >
        <Lock className="w-7 h-7" />
      </div>
      <h2 className="text-lg font-bold text-slate-900 mb-2">{title}</h2>
      <p className="text-xs sm:text-sm text-slate-600 leading-relaxed mb-6">
        บัญชีผู้ใช้งานปัจจุบันของคุณคือ <strong>บุคคลภายนอก</strong> จึงใช้งานได้เฉพาะระบบสืบค้นกฎหมายพลังงานสาธารณะ
      </p>
      <div className="flex flex-wrap justify-center gap-3">
        <button onClick={onLogin} className="px-4 py-2.5 rounded-xl text-xs font-semibold bg-slate-900 text-white hover:bg-slate-800">
          สลับเป็นบัญชีนิติกร
        </button>
        <button onClick={onBack} className="px-4 py-2.5 rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200">
          กลับไปหน้าสืบค้นกฎหมายพลังงาน
        </button>
      </div>
    </div>
  );
}

export function useDebounced(value, ms = 350) {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function DocView({ doc }) {
  return (
    <div className="p-4 rounded-xl bg-slate-900 text-slate-100 space-y-2">
      <div className="flex items-center justify-between text-xs text-slate-400 border-b border-slate-800 pb-2">
        <span>
          {doc.doc_type} • ลงวันที่ {thDate(doc.doc_date)}
        </span>
        {doc.pages && <span>จำนวน {doc.pages} หน้า</span>}
      </div>
      <p className="text-xs sm:text-sm leading-relaxed text-slate-200 pt-1">"{doc.excerpt}"</p>
    </div>
  );
}

export function Field({ k, v, strong }) {
  return (
    <div>
      <span className="text-[11px] font-semibold text-slate-500">{k}: </span>
      <span className={`text-xs ${strong ? 'font-bold text-emerald-700' : 'font-semibold text-slate-800'}`}>{v}</span>
    </div>
  );
}
