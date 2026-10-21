import React, { useCallback, useEffect, useState } from 'react';
import { BookOpen, CheckCircle2, Gavel, Lock, Scale, Shield, Sparkles, Timer, UserCheck } from 'lucide-react';
import { api, setApiUser } from './api.js';
import LawPage from './pages/LawPage.jsx';
import AppealsPage from './pages/AppealsPage.jsx';
import CasesPage from './pages/CasesPage.jsx';
import AssistantPage from './pages/AssistantPage.jsx';
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
  { key: 'assistant', label: 'ระบบช่วยค้นคว้าอัจฉริยะ', icon: Sparkles, dot: true }
];

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
  const [route, setRoute] = useState({ page: loadSaved('erc.page', 'law') });
  const [toast, setToast] = useState(null);
  const [assistantOn, setAssistantOn] = useState(true);
  const [llm, setLlm] = useState(null);

  setApiUser(userId);

  useEffect(() => {
    setApiUser(userId);
    try {
      localStorage.setItem('erc.user', userId);
    } catch {}
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
    } catch {}
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
  const pageProps = { me, go, notify, route, switchUser };
  const k = `${userId}-${route.page}`;

  return (
    <div className="min-h-screen bg-slate-100 text-slate-800 flex flex-col font-sans">
      <div className="bg-amber-100 text-amber-900 text-[11px] text-center py-1.5 px-4 border-b border-amber-200">
        POC — ตัวบท เลขสำนวน และคู่กรณีทั้งหมดเป็นข้อมูลสาธิต ไม่ใช่ข้อมูลจริงของสำนักงาน กกพ.
      </div>
      <header className="bg-slate-900 text-white shadow-md border-b border-slate-800 sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3.5">
            <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-amber-400 to-amber-600 flex items-center justify-center text-slate-950 shadow-sm">
              <Scale className="w-6 h-6" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base sm:text-lg font-bold tracking-tight">ระบบสารสนเทศกฎหมาย งานอุทธรณ์ และงานคดีพลังงาน</h1>
                <span className="hidden md:inline-flex px-2 py-0.5 rounded text-[11px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-400/30">
                  สำนักงาน กกพ.
                </span>
              </div>
              <p className="text-xs text-slate-400">สำนักงานคณะกรรมการกำกับกิจการพลังงาน (Office of the Energy Regulatory Commission)</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {me && (
              <div className="hidden lg:flex flex-col items-end text-right mr-1">
                <div className="flex items-center gap-1.5 text-xs font-medium text-slate-200">
                  <UserCheck className="w-3.5 h-3.5 text-emerald-400" />
                  <span>{me.name}</span>
                </div>
                <span className="text-[11px] text-slate-400">{me.title}</span>
              </div>
            )}
            <div className="flex items-center bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5">
              <span className="text-[11px] text-slate-400 mr-2">บัญชีผู้ใช้งาน (สาธิต):</span>
              <select
                value={userId}
                onChange={(e) => switchUser(e.target.value)}
                className="bg-transparent text-xs font-semibold text-amber-300 focus:outline-none cursor-pointer"
              >
                {users.map((u) => (
                  <option key={u.user_id} value={u.user_id} className="bg-slate-900 text-white">
                    {u.name} — {u.title}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="bg-slate-800/90 border-t border-slate-700/80">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between overflow-x-auto">
            <nav className="flex space-x-1">
              {NAV.map(({ key, label, icon: Icon, badge, internal: needInternal, dot }) => (
                <button
                  key={key}
                  onClick={() => go(key)}
                  className={`flex items-center gap-2 px-3.5 py-3 text-xs sm:text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                    route.page === key
                      ? 'border-amber-400 text-amber-300 bg-slate-900/50'
                      : 'border-transparent text-slate-300 hover:text-white hover:bg-slate-700/40'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  <span>{label}</span>
                  {badge && <span className="px-1.5 py-0.5 text-[10px] rounded-full bg-amber-500/20 text-amber-300 font-semibold">{badge}</span>}
                  {dot && <span className={`w-2 h-2 rounded-full ${assistantOn ? 'bg-emerald-400' : 'bg-rose-500'}`} />}
                  {needInternal && me && !internal && <Lock className="w-3.5 h-3.5 text-slate-400" />}
                </button>
              ))}
            </nav>
            {me && (
              <div className="hidden xl:flex items-center gap-2 text-xs text-slate-300 py-2">
                <span className="px-2.5 py-1 rounded-full bg-slate-900 border border-slate-700 flex items-center gap-1.5">
                  <Shield className="w-3.5 h-3.5 text-amber-400" />
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
          {me && route.page === 'law' && <LawPage key={k} {...pageProps} />}
          {me && route.page === 'appeal' && <AppealsPage key={k} {...pageProps} />}
          {me && route.page === 'court' && <CasesPage key={k} {...pageProps} />}
          {me && route.page === 'assistant' && <AssistantPage key={k} {...pageProps} enabled={assistantOn} llm={llm} />}
        </ErrorBoundary>
      </main>

      <footer className="text-center text-[11px] text-slate-500 py-4">
        POC v0.1 · ทุกการเปิดดูและการแก้ไขถูกบันทึกใน audit log
      </footer>
    </div>
  );
}
