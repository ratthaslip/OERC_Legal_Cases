import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, FileText, Gavel, Mail, Plus, Search, Timer, UserCheck } from 'lucide-react';
import { api } from '../api.js';
import { Card, ClassBadge, ErrorBox, inputCls, Label, LockedPanel, OmittedNote, Spinner, thDate, useDebounced } from '../components/ui.jsx';
import AppealDetail from './appeals/AppealDetail.jsx';
import RegisterDialog from './appeals/RegisterDialog.jsx';
import { CASE_TYPE_SHORT, clockText, Pill, stepTimingText } from './appeals/parts.jsx';

const STEP_SHORT = ['รับเรื่อง/มอบหมาย', 'ตรวจสิทธิ/เอกสาร', 'แจ้งแก้ไข', 'ข้อเท็จจริง', 'คณะอนุฯ', 'วาระ กกพ.', 'กกพ. วินิจฉัย', 'แจ้งผล', 'ปิดเรื่อง'];

export default function AppealsPage({ me, go, notify, route, switchUser }) {
  const [filters, setFilters] = useState({ q: '', sector: '', status: 'IN_PROGRESS', case_type: '', attention: '', step: '', mine: false });
  const [list, setList] = useState(null);
  const [selected, setSelected] = useState(route.id || null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [registering, setRegistering] = useState(false);
  const q = useDebounced(filters.q);

  useEffect(() => {
    if (route.id) {
      setSelected(route.id);
      setFilters((f) => ({ ...f, status: '' }));
    }
  }, [route.nonce]);

  const loadList = () =>
    api
      .appeals({ ...filters, q, mine: filters.mine || undefined })
      .then((r) => {
        setList(r);
        if (!selected && r.items.length) setSelected(r.items[0].appeal_id);
      })
      .catch(setError);

  useEffect(() => {
    if (me.role !== 'PUB') loadList();
  }, [q, filters.sector, filters.status, filters.case_type, filters.attention, filters.step, filters.mine]);

  useEffect(() => {
    if (!selected || me.role === 'PUB') return;
    setDetail(null);
    setActionError(null);
    api.appeal(selected).then(setDetail).catch((e) => setError(e.status === 404 ? new Error('ไม่พบเรื่องนี้ หรือคุณไม่มีสิทธิ์เข้าถึง') : e));
  }, [selected]);

  if (me.role === 'PUB')
    return <LockedPanel title="ระบบสืบค้นและติดตามงานอุทธรณ์สำหรับเจ้าหน้าที่สำนักงาน กกพ." onLogin={() => switchUser('lgl1')} onBack={() => go('law')} />;

  /** เรียก action ด้วย row_version ปัจจุบัน แล้วรับรายละเอียดฉบับใหม่ */
  const run = async (msg, call) => {
    setActionError(null);
    try {
      const next = await call(detail.appeal.row_version);
      setDetail(next);
      notify(msg);
      loadList();
    } catch (e) {
      setActionError(e);
      if (e.status === 409) api.appeal(selected).then(setDetail);
    }
  };

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const st = list?.stats;
  const canRegister = ['STF', 'LGL', 'DIR'].includes(me.role);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Kpi label={`ทั้งหมดตามสิทธิ์ · ร้องเรียน ${st?.complaints ?? '-'}`} value={st?.total} icon={<FileText className="w-5 h-5" />} tone="slate"
          onClick={() => setFilters((f) => ({ ...f, status: '', attention: '', step: '' }))} />
        <Kpi label="อยู่ระหว่างดำเนินการ" value={st?.in_progress} icon={<Timer className="w-5 h-5" />} tone="amber"
          onClick={() => setFilters((f) => ({ ...f, status: 'IN_PROGRESS', attention: '', step: '' }))} />
        <Kpi label="เกินกรอบเวลา" value={st?.overdue} icon={<AlertTriangle className="w-5 h-5" />} tone="rose"
          onClick={() => setFilters((f) => ({ ...f, status: 'IN_PROGRESS', attention: 'OVERDUE', step: '' }))} />
        <Kpi label="ใกล้ครบกำหนด" value={st?.near_due} icon={<Timer className="w-5 h-5" />} tone="amber"
          onClick={() => setFilters((f) => ({ ...f, status: 'IN_PROGRESS', attention: 'NEAR_DUE', step: '' }))} />
        <Kpi label={`รอมอบหมาย · หนังสือรอตอบ ${st?.open_requests ?? '-'}`} value={st?.unassigned} icon={<UserCheck className="w-5 h-5" />} tone="indigo"
          onClick={() => setFilters((f) => ({ ...f, status: 'IN_PROGRESS', attention: '', step: '1' }))} />
      </div>

      {/* pipeline ตามขั้นตอน WI-EFL-04 */}
      <Card className="p-3">
        <div className="flex items-center justify-between mb-2 px-1">
          <span className="text-[11px] font-bold text-slate-700">เรื่องที่อยู่ระหว่างดำเนินการแยกตามขั้นตอน (คลิกเพื่อกรอง)</span>
          {filters.step && (
            <button className="text-[11px] text-amber-700 font-semibold" onClick={() => setFilters((f) => ({ ...f, step: '' }))}>
              ล้างตัวกรองขั้นตอน
            </button>
          )}
        </div>
        <div className="grid grid-cols-3 sm:grid-cols-9 gap-1.5">
          {STEP_SHORT.map((name, i) => {
            const n = i + 1;
            const c = st?.by_step?.[n] ?? 0;
            const on = String(n) === filters.step;
            return (
              <button
                key={n}
                onClick={() => setFilters((f) => ({ ...f, status: 'IN_PROGRESS', step: on ? '' : String(n) }))}
                className={`text-left px-2 py-1.5 rounded-lg border text-[11px] ${on ? 'bg-amber-50 border-amber-400' : 'bg-white border-slate-200 hover:border-slate-300'}`}
              >
                <div className="text-slate-500">ขั้น {n}</div>
                <div className="font-semibold text-slate-900 truncate">{name}</div>
                <div className={`text-base font-bold ${c ? 'text-slate-900' : 'text-slate-300'}`}>{c}</div>
              </button>
            );
          })}
        </div>
      </Card>

      <Card className="p-4 sm:p-5">
        <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-end">
          <div className="md:col-span-4">
            <Label>ค้นหา (เลขรับ อธ./รร., ประเด็น, มาตราที่อ้างอิง)</Label>
            <div className="relative">
              <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input value={filters.q} onChange={set('q')} placeholder="เช่น อธ. 18/2567, จุดเชื่อมโยง, ท่อส่งก๊าซ..." className={`${inputCls} pl-10`} />
            </div>
          </div>
          <div className="md:col-span-2">
            <Label>ประเภทเรื่อง</Label>
            <select value={filters.case_type} onChange={set('case_type')} className={inputCls}>
              <option value="">ทั้งหมด</option>
              <option value="APPEAL">อุทธรณ์ (ม.121)</option>
              <option value="COMPLAINT">เรื่องร้องเรียน</option>
            </select>
          </div>
          <div className="md:col-span-2">
            <Label>ประเภทกิจการ</Label>
            <select value={filters.sector} onChange={set('sector')} className={inputCls}>
              <option value="">ทุกประเภท</option>
              {list?.sectors.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="md:col-span-2">
            <Label>สถานะ</Label>
            <select value={filters.status} onChange={set('status')} className={inputCls}>
              <option value="">ทุกสถานะ</option>
              <option value="IN_PROGRESS">อยู่ระหว่างดำเนินการ</option>
              <option value="CLOSED">ปิดเรื่องแล้ว</option>
            </select>
          </div>
          <div className="md:col-span-2 flex flex-col gap-2">
            <label className="flex items-center gap-2 text-xs font-semibold text-slate-700">
              <input type="checkbox" checked={filters.mine} onChange={(e) => setFilters((f) => ({ ...f, mine: e.target.checked }))} /> เฉพาะงานของฉัน
            </label>
            {canRegister && (
              <button onClick={() => setRegistering(true)} className="px-3 py-2 rounded-lg text-xs font-semibold bg-amber-600 text-white hover:bg-amber-700 flex items-center justify-center gap-1">
                <Plus className="w-3.5 h-3.5" /> รับเรื่องใหม่
              </button>
            )}
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        <div className="lg:col-span-4 space-y-3 lg:max-h-[calc(100vh-160px)] lg:overflow-y-auto lg:pr-1">
          <OmittedNote count={list?.restricted_omitted} />
          {!list && <Spinner />}
          {list?.items.length === 0 && <p className="text-xs text-slate-500 text-center py-6">ไม่พบเรื่องที่ตรงกับเงื่อนไข</p>}
          {list?.items.map((a) => (
            <ListItem key={a.appeal_id} a={a} selected={a.appeal_id === selected} onClick={() => setSelected(a.appeal_id)} />
          ))}
        </div>
        <div className="lg:col-span-8">
          <ErrorBox error={error} />
          {selected && !detail && !error && <Spinner />}
          {detail && <AppealDetail d={detail} go={go} run={run} error={actionError} />}
        </div>
      </div>

      {registering && (
        <RegisterDialog
          sectors={list?.sectors || []}
          onClose={() => setRegistering(false)}
          onCreated={(id) => {
            setRegistering(false);
            notify('ลงทะเบียนเรื่องแล้ว รอ ผอ.คบ. มอบหมาย');
            setFilters((f) => ({ ...f, status: 'IN_PROGRESS', step: '', attention: '' }));
            setSelected(id);
            loadList();
          }}
        />
      )}
    </div>
  );
}

function ListItem({ a, selected, onClick }) {
  const cur = a.current_step;
  const tc = a.total_clock;
  return (
    <div
      onClick={onClick}
      className={`appeal-item p-4 rounded-xl border cursor-pointer transition-all bg-white ${selected ? 'border-amber-500 ring-2 ring-amber-500/20 shadow-xs' : 'border-slate-200 hover:border-slate-300'}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-1.5 mb-1.5">
        <span className="flex items-center gap-1.5 whitespace-nowrap">
          <span className="px-2.5 py-0.5 rounded text-xs font-bold bg-slate-900 text-white">{a.appeal_no}</span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${a.case_type === 'APPEAL' ? 'bg-amber-50 text-amber-800' : 'bg-violet-50 text-violet-800'}`}>
            {CASE_TYPE_SHORT[a.case_type]}
          </span>
          <ClassBadge classification={a.classification} />
        </span>
        {a.attention === 'OVERDUE' && <Pill tone="OVERDUE">ต้องเร่งดำเนินการ</Pill>}
        {a.attention === 'NEAR_DUE' && <Pill tone="NEAR_DUE">ใกล้ครบกำหนด</Pill>}
        {a.status === 'CLOSED' && <Pill tone="DONE_ON_TIME">ปิดเรื่องแล้ว</Pill>}
      </div>
      <h3 className="text-xs sm:text-sm font-bold text-slate-900 line-clamp-2 mb-2">{a.subject}</h3>
      <div className="text-[11px] text-slate-500 space-y-1">
        {cur ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <span>
              ขั้น {cur.step_no}/9: {cur.name}
            </span>
            {stepTimingText({ ...cur, state: 'current' }) && <Pill tone={cur.timing}>{stepTimingText({ ...cur, state: 'current' })}</Pill>}
          </div>
        ) : (
          <div className="line-clamp-1">ผล: {a.decision_result || '-'}</div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-1 pt-1 border-t border-slate-100">
          <span>
            รับเรื่อง {thDate(a.received_date)}
            {tc && tc.state !== 'NOT_APPLICABLE' && ` · ${tc.label.replace('กรอบเวลารวม ', '')}: ${clockText(tc)}`}
          </span>
          <span className="flex items-center gap-2">
            {a.open_requests > 0 && (
              <span className={`flex items-center gap-0.5 ${a.overdue_requests ? 'text-rose-700 font-semibold' : ''}`}>
                <Mail className="w-3 h-3" /> {a.open_requests}
              </span>
            )}
            {a.court_case_count > 0 && (
              <span className="text-indigo-700 font-semibold flex items-center gap-0.5">
                <Gavel className="w-3 h-3" /> คดี
              </span>
            )}
          </span>
        </div>
        {a.owner_names?.length > 0 && <div>ผู้รับผิดชอบ: {a.owner_names.join(', ')}</div>}
      </div>
    </div>
  );
}

function Kpi({ label, value, icon, tone, onClick }) {
  const t = {
    slate: ['border-slate-200', 'text-slate-500', 'text-slate-900', 'bg-slate-100 text-slate-700'],
    amber: ['border-amber-200', 'text-amber-800 font-medium', 'text-amber-600', 'bg-amber-50 text-amber-600'],
    rose: ['border-rose-200', 'text-rose-800 font-medium', 'text-rose-600', 'bg-rose-50 text-rose-600'],
    emerald: ['border-emerald-200', 'text-emerald-800 font-medium', 'text-emerald-600', 'bg-emerald-50 text-emerald-600'],
    indigo: ['border-indigo-200', 'text-indigo-800 font-medium', 'text-indigo-600', 'bg-indigo-50 text-indigo-600']
  }[tone];
  return (
    <div onClick={onClick} className={`bg-white rounded-xl border ${t[0]} p-4 flex items-center justify-between ${onClick ? 'cursor-pointer hover:shadow-xs' : ''}`}>
      <div className="min-w-0">
        <p className={`text-[11px] ${t[1]} leading-snug`}>{label}</p>
        <p className={`text-2xl font-bold mt-1 ${t[2]}`}>{value ?? '-'}</p>
      </div>
      <div className={`w-10 h-10 shrink-0 rounded-xl flex items-center justify-center ${t[3]}`}>{icon}</div>
    </div>
  );
}
