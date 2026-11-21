import { useCallback, useEffect, useState } from 'react';
import { BookOpen, CheckCircle2, Gavel, Scale, Settings, Shield, Sparkles, Timer, UserCheck } from 'lucide-react';
import { api, setApiUser } from './api.js';
import LawPage from './pages/LawPage.jsx';
import AppealsPage from './pages/AppealsPage.jsx';
import CasesPage from './pages/CasesPage.jsx';
import AssistantPage from './pages/AssistantPage.jsx';
import AdminPage from './pages/AdminPage.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';

const CLEARANCE = {
  PUB: 'ข้อมูลเผยแพร่สาธารณะ',
  STF: 'ภายในสำนักงาน กกพ.',
  LGL: 'ภายใน + สำนวนชั้นความลับที่รับผิดชอบ',
  DIR: 'ผอ.คบ. · ทุกเรื่องของฝ่ายคดี',
  ADM: 'ภายในสำนักงาน กกพ. (ผู้ดูแลระบบ)'
};

const NAV = [
  { key: 'law', label: 'สืบค้นกฎหมายพลังงาน', icon: BookOpen },
  { key: 'appeal', label: 'สืบค้นและติดตามงานอุทธรณ์', icon: Timer, badge: 'SLA', internal: true },
  { key: 'court', label: 'สืบค้นงานคดี', icon: Gavel, internal: true },
  { key: 'assistant', label: 'ระบบช่วยค้นคว้าอัจฉริยะ', icon: Sparkles, dot: true },
  { key: 'admin', label: 'ตั้งค่าระบบ', icon: Settings, roles: ['ADM'] }
];

// โลโก้: ค่าเริ่มต้นใช้สัญลักษณ์ตาชั่ง (ไม่โหลดรูป)
// เมื่อได้ไฟล์โลโก้ทางการ: วางที่ frontend/public/brand/ แล้วตั้ง VITE_BRAND_LOGO=/brand/logo.png
// ถ้าโหลดรูปไม่ได้ จะกลับมาแสดงตาชั่ง — ไม่วาดหรือจำลองโลโก้เอง
const LOGO_SRC = import.meta.env.VITE_BRAND_LOGO || '';

function BrandLogo() {
  const [ok, setOk] = useState(Boolean(LOGO_SRC));
  return (
    <div className="h-12 min-w-12 px-1.5 rounded-xl bg-white flex items-center justify-center shadow-sm">
      {ok ? (
        <img src={LOGO_SRC} alt="ตราสำนักงานคณะกรรมการกำกับกิจการพลังงาน" className="h-10 w-auto object-contain" onError={() => setOk(false)} />
      ) : (
        <Scale className="w-7 h-7 text-brand-700" strokeWidth={1.75} aria-label="ตาชั่ง" />
      )}
    </div>
  );
}

function loadSaved(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

export default function App() {
  const [userId, setUserId] = useState(() => loadSaved('erc.user', 'lgl1'));
  const [users, setUsers] = useState([]);
  const [me, setMe] = useState(null);
  // หน้า "นำเข้าเอกสาร" ย้ายไปเป็นแท็บในสืบค้นกฎหมาย — หน้าที่บันทึกไว้แบบเดิมเปิดแท็บนั้นแทน
  const [route, setRoute] = useState(() => {
    const saved = loadSaved('erc.page', 'law');
    return saved === 'import' ? { page: 'law', tab: 'import' } : { page: saved };
  });
  const [toast, setToast] = useState(null);
  const [assistantOn, setAssistantOn] = useState(true);
  const [llm, setLlm] = useState(null);

  setApiUser(userId);

  useEffect(() => {
    setApiUser(userId);
    try {
      localStorage.setItem('erc.user', userId);
    } catch { /* เบราว์เซอร์ปิด storage — ไม่จำผู้ใช้ */ }
    api.me().then((m) => {
      setMe(m);
      setUsers(m.demo_users);
    });
    api.assistantStatus().then((s) => {
      setAssistantOn(s.enabled);
      setLlm(s);
    }).catch(() => setAssistantOn(false));
  }, [userId]);

  useEffect(() => {
    try {
      localStorage.setItem('erc.page', route.page);
    } catch { /* เบราว์เซอร์ปิด storage — ไม่จำหน้า */ }
  }, [route.page]);

  const notify = useCallback((msg) => {
    setToast(msg);
    setTimeout(() => setToast((t) => (t === msg ? null : t)), 3500);
  }, []);

  /** นำทางข้ามโมดูล: go('law', {eli, asOf}) / go('appeal', {id}) / go('court', {id}) */
  const go = useCallback((page, params = {}) => {
    setRoute({ page, ...params, nonce: Date.now() });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  const switchUser = (u) => {
    setUserId(u);
    const next = users.find((x) => x.user_id === u);
    if (next) notify(`สลับบัญชีผู้ใช้งานเป็น: ${next.name} (${next.title})`);
  };

  const internal = me && me.role !== 'PUB';
  // เมนูที่ผู้ใช้เห็น: งานอุทธรณ์/งานคดีเฉพาะเจ้าหน้าที่ (ประชาชนทั่วไปไม่เห็นเมนู) และเมนูที่จำกัดบทบาท
  const canSee = (n) => (!n.roles || n.roles.includes(me?.role)) && (!n.internal || internal);
  const nav = NAV.filter(canSee);
  // หน้าที่บันทึกไว้หรือถูกลิงก์มาแต่ผู้ใช้ปัจจุบันไม่มีสิทธิ์ (เช่น สลับเป็นประชาชนทั่วไป) → กลับไปหน้าสืบค้นกฎหมาย
  const page = !me || nav.some((n) => n.key === route.page) ? route.page : 'law';
  const pageProps = { me, go, notify, route, switchUser };
  const k = `${userId}-${page}`;

  return (
    <div className="min-h-screen bg-canvas text-slate-800 flex flex-col font-sans">
      <div className="bg-amber-50 text-amber-900 text-[11px] text-center py-1.5 px-4 border-b border-amber-200">
        POC — ตัวบท พ.ร.บ.การประกอบกิจการพลังงาน พ.ศ. 2550 จากฉบับรวมของสำนักงานคณะกรรมการกฤษฎีกา (รอนิติกรตรวจทาน) · ทะเบียนกฎหมายลำดับรองจากทะเบียนของสำนักงาน · สำนวนอุทธรณ์ คดี และคู่กรณีเป็นข้อมูลสาธิต
      </div>
      <header className="sticky top-0 z-30 shadow-card">
        <div className="text-white bg-[linear-gradient(100deg,#0a4a9c_0%,#0b5fbf_60%,#1278d6_100%)]">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3.5">
              <BrandLogo />
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-base sm:text-lg font-bold tracking-tight">ระบบสารสนเทศกฎหมาย งานอุทธรณ์ และงานคดีพลังงาน</h1>
                  <span className="hidden md:inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500 text-white">
                    สำนักงาน กกพ.
                  </span>
                </div>
                <p className="text-xs text-brand-100">สำนักงานคณะกรรมการกำกับกิจการพลังงาน (Office of the Energy Regulatory Commission)</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {me && (
                <div className="hidden lg:flex flex-col items-end text-right mr-1">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-white">
                    <UserCheck className="w-3.5 h-3.5 text-amber-300" />
                    <span>{me.name}</span>
                  </div>
                  <span className="text-[11px] text-brand-100">{me.title}</span>
                </div>
              )}
              <div className="flex items-center bg-white/10 border border-white/25 rounded-lg px-2.5 py-1.5">
                <span className="text-[11px] text-brand-100 mr-2">บัญชีผู้ใช้งาน (สาธิต):</span>
                <select
                  value={userId}
                  onChange={(e) => switchUser(e.target.value)}
                  className="bg-transparent text-xs font-semibold text-white focus:outline-none cursor-pointer"
                >
                  {users.map((u) => (
                    <option key={u.user_id} value={u.user_id} className="bg-white text-slate-900">
                      {u.name} — {u.title}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white border-b border-slate-200">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between overflow-x-auto">
            <nav className="flex space-x-1">
              {nav.map(({ key, label, icon: Icon, badge, dot }) => (
                <button
                  key={key}
                  onClick={() => go(key)}
                  className={`flex items-center gap-2 px-3.5 py-3 text-xs sm:text-sm font-medium border-b-[3px] transition-colors whitespace-nowrap ${
                    page === key
                      ? 'border-brand-600 text-brand-700 font-semibold'
                      : 'border-transparent text-slate-600 hover:text-brand-700 hover:bg-brand-50'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  <span>{label}</span>
                  {badge && <span className="px-1.5 py-0.5 text-[10px] rounded-full bg-amber-100 text-amber-700 font-semibold">{badge}</span>}
                  {dot && <span className={`w-2 h-2 rounded-full ${assistantOn ? 'bg-emerald-500' : 'bg-rose-500'}`} />}
                </button>
              ))}
            </nav>
            {me && (
              <div className="hidden xl:flex items-center gap-2 text-xs text-slate-600 py-2">
                <span className="px-2.5 py-1 rounded-full bg-brand-50 border border-brand-100 text-brand-800 flex items-center gap-1.5">
                  <Shield className="w-3.5 h-3.5 text-brand-600" />
                  <span>ระดับสิทธิ์: {CLEARANCE[me.role]}</span>
                </span>
              </div>
            )}
          </div>
        </div>
      </header>

      {toast && (
        <div className="fixed bottom-5 right-5 z-50 bg-slate-900 text-white px-4 py-3 rounded-xl shadow-lg border border-slate-700 flex items-center gap-2.5 text-xs sm:text-sm">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>{toast}</span>
        </div>
      )}

      <main className="max-w-7xl w-full mx-auto px-4 sm:px-6 py-6 flex-1">
        <ErrorBoundary key={k}>
          {me && page === 'law' && <LawPage key={k} {...pageProps} />}
          {me && page === 'appeal' && <AppealsPage key={k} {...pageProps} />}
          {me && page === 'court' && <CasesPage key={k} {...pageProps} />}
          {me && page === 'assistant' && <AssistantPage key={k} {...pageProps} enabled={assistantOn} llm={llm} />}
          {me && page === 'admin' && me.role === 'ADM' && <AdminPage key={k} {...pageProps} />}
        </ErrorBoundary>
      </main>

      <footer className="text-center text-[11px] text-slate-500 py-4">
        POC v0.1 · ทุกการเปิดดูและการแก้ไขถูกบันทึกใน audit log
      </footer>
    </div>
  );
}
