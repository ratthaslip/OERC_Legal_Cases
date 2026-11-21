import { thDate } from '../../components/ui.jsx';

export const CASE_TYPE = { APPEAL: 'อุทธรณ์', COMPLAINT: 'เรื่องร้องเรียน' };
export const CASE_TYPE_SHORT = { APPEAL: 'อุทธรณ์', COMPLAINT: 'ร้องเรียน' };
export const COMPLAINT_BASIS = {
  PROCUREMENT: 'ระเบียบจัดหาไฟฟ้า/ประกาศรับซื้อไฟฟ้า',
  PPA: 'ข้อพิพาทตามสัญญาซื้อขายไฟฟ้า',
  CONSUMER: 'เรื่องร้องเรียนของผู้ใช้พลังงาน (ระเบียบ 2553)'
};
export const TARGET = { ORDER: 'อุทธรณ์คำสั่ง', RULE: 'อุทธรณ์ระเบียบ/ประกาศ/ข้อกำหนด' };
export const CHANNEL = { COUNTER: 'ยื่นด้วยตนเอง', POST: 'ไปรษณีย์ลงทะเบียน', ELECTRONIC: 'ช่องทางอิเล็กทรอนิกส์' };
export const ROUTE = { SUBCOMMITTEE: 'เสนอคณะอนุกรรมการฯ', DIRECT: 'เสนอ กกพ. โดยตรง (ไม่ผ่านอนุฯ)' };
export const TARGET_TYPE = { APPELLANT: 'ผู้อุทธรณ์/ผู้ร้องเรียน', INTERNAL: 'ฝ่ายงานภายใน', EXTERNAL: 'หน่วยงาน/บุคคลภายนอก' };

const TONE = {
  OVERDUE: 'bg-rose-50 text-rose-700 border-rose-300',
  DONE_LATE: 'bg-rose-50 text-rose-700 border-rose-300',
  LATE: 'bg-rose-50 text-rose-700 border-rose-300',
  NEAR_DUE: 'bg-amber-100 text-amber-800 border-amber-300',
  ON_TRACK: 'bg-sky-50 text-sky-800 border-sky-200',
  OPEN: 'bg-sky-50 text-sky-800 border-sky-200',
  DONE_ON_TIME: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  ON_TIME: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  ANSWERED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  WAITING: 'bg-slate-100 text-slate-600 border-slate-300',
  NOT_STARTED: 'bg-slate-100 text-slate-500 border-slate-200',
  NOT_APPLICABLE: 'bg-slate-100 text-slate-500 border-slate-200'
};

export function Pill({ tone, children }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded border text-[10px] font-semibold whitespace-nowrap ${TONE[tone] || TONE.NOT_STARTED}`}>
      {children}
    </span>
  );
}

export function clockText(c) {
  switch (c.state) {
    case 'ON_TRACK':
      return `เหลือ ${c.days_left} วัน`;
    case 'NEAR_DUE':
      return `ใกล้ครบ · เหลือ ${c.days_left} วัน`;
    case 'OVERDUE':
      return `เกินกำหนด ${Math.abs(c.days_left)} วัน`;
    case 'DONE_ON_TIME':
      return c.key === 'filing' ? `ทันกำหนด (วันที่ ${c.days_used})` : `เสร็จในกรอบ (${c.days_used} วัน)`;
    case 'DONE_LATE':
      return c.key === 'filing' ? `พ้นกำหนด (วันที่ ${c.days_used})` : `เสร็จเกินกรอบ (${c.days_used} วัน)`;
    case 'NOT_APPLICABLE':
      return 'ไม่กำหนด';
    default:
      return 'ยังไม่เริ่มนับ';
  }
}

export function stepTimingText(s) {
  if (s.state === 'current') {
    if (s.timing === 'WAITING') return 'รอวันเริ่มนับ';
    if (s.timing === 'OVERDUE') return `เกินกรอบ ${Math.abs(s.days_left)} วัน`;
    if (s.timing === 'NEAR_DUE') return s.days_left === 0 ? 'ครบกำหนดวันนี้' : `เหลือ ${s.days_left} วัน`;
    return `เหลือ ${s.days_left} วัน`;
  }
  if (s.state === 'completed') return s.timing === 'LATE' ? 'เสร็จเกินกรอบ' : s.timing === 'ON_TIME' ? 'ทันกรอบ' : 'เสร็จแล้ว';
  if (s.state === 'skipped') return 'ข้ามขั้นนี้';
  return '';
}

/** ลิงก์ไปตัวบทที่เป็นฐานทางกฎหมาย (เปิดหน้าสืบค้นกฎหมาย ณ วันที่ที่กำหนด) */
export function LawRefs({ refs, go, asOf, className = '' }) {
  if (!refs?.length) return null;
  return (
    <span className={`flex flex-wrap gap-1 ${className}`}>
      {refs.map((r) => (
        <button
          key={r.eli}
          type="button"
          onClick={() => go && go('law', { tab: 'provision', eli: r.eli, ...(asOf ? { asOf } : {}) })}
          className="inline-flex items-center px-1.5 py-0.5 rounded border border-amber-300 bg-amber-50 text-[10px] font-medium text-amber-900 hover:bg-amber-100"
          title="เปิดตัวบท"
        >
          {r.label}
        </button>
      ))}
    </span>
  );
}

export function ClockCard({ c, go, asOf }) {
  return (
    <div className="p-3 rounded-xl border border-slate-200 bg-white flex flex-col gap-1.5">
      <span className="text-[11px] font-bold text-slate-800 leading-snug">{c.label}</span>
      <span>
        <Pill tone={c.state}>{clockText(c)}</Pill>
      </span>
      <div className="text-[11px] text-slate-500">
        {c.start ? `เริ่ม ${thDate(c.start)}` : '—'}
        {c.due ? ` · ครบ ${thDate(c.due)}` : ''}
      </div>
      {c.refs?.length ? <LawRefs refs={c.refs} go={go} asOf={asOf} /> : <div className="text-[10px] text-slate-400 leading-snug">{c.basis}</div>}
    </div>
  );
}

export const smallInput =
  'w-full px-2.5 py-1.5 text-xs border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-amber-500';
export const btnPrimary =
  'px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-40';
export const btnGhost =
  'px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-40';

export function L({ children, hint }) {
  return (
    <label className="block text-[11px] font-semibold text-slate-700 mb-1">
      {children}
      {hint && <span className="font-normal text-slate-500"> — {hint}</span>}
    </label>
  );
}
