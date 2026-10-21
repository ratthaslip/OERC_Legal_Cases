import React, { useState } from 'react';
import { ArrowRight, CheckCircle2, Circle, Clock, FileText, Gavel, Lock, Mail, MinusCircle } from 'lucide-react';
import { api } from '../../api.js';
import { Card, ClassBadge, DocView, ErrorBox, Field, thDate, todayIso } from '../../components/ui.jsx';
import StepActions, { RevertButton } from './StepActions.jsx';
import {
  CASE_TYPE, CHANNEL, ClockCard, COMPLAINT_BASIS, Pill, ROUTE, smallInput, stepTimingText, TARGET, TARGET_TYPE
} from './parts.jsx';

export default function AppealDetail({ d, go, run, error }) {
  const a = d.appeal;
  const [docIdx, setDocIdx] = useState(0);
  const sc = a.screening;
  const doc = d.documents[docIdx];

  return (
    <Card className="p-5 sm:p-6 space-y-5">
      {/* หัวเรื่อง */}
      <div className="border-b border-slate-200 pb-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="px-3 py-1 rounded-lg text-xs font-bold bg-slate-900 text-white">เลขรับ {a.appeal_no}</span>
            <span className={`px-2.5 py-1 rounded-lg text-xs font-semibold border ${a.case_type === 'APPEAL' ? 'bg-amber-50 text-amber-900 border-amber-200' : 'bg-violet-50 text-violet-900 border-violet-200'}`}>
              {CASE_TYPE[a.case_type]}
            </span>
            <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-slate-100 text-slate-700">{a.energy_sector}</span>
            <ClassBadge classification={a.classification} />
            {a.status === 'CLOSED' && <Pill tone="DONE_ON_TIME">ปิดเรื่องแล้ว</Pill>}
          </div>
          {d.court_cases.map((k) => (
            <button
              key={k.case_id}
              onClick={() => go('court', { id: k.case_id })}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-50 text-indigo-800 border border-indigo-200 hover:bg-indigo-100 flex items-center gap-1.5"
            >
              <Gavel className="w-3.5 h-3.5" /> คดีที่ฟ้องต่อยอด ({k.red_no || k.black_no}) <ArrowRight className="w-3.5 h-3.5" />
            </button>
          ))}
        </div>
        <h2 className="text-base sm:text-lg font-bold text-slate-900 leading-snug">{a.subject}</h2>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
          <span>E-Sarabun: {a.esarabun_no || '-'}</span>
          <span>ทะเบียนคุมงาน FM-EFL-01: {a.register_no || 'ยังไม่ลงทะเบียน'}</span>
          <span>ช่องทาง: {CHANNEL[a.filing_channel] || '-'}</span>
          {a.case_type === 'APPEAL' ? <span>{TARGET[a.target_type] || ''}</span> : <span>{COMPLAINT_BASIS[a.complaint_basis] || ''}</span>}
        </div>
      </div>

      {/* กรอบเวลาตามกฎหมาย */}
      <div>
        <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-2">กรอบเวลาตามกฎหมาย</h4>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2.5">
          {d.clocks.map((c) => (
            <ClockCard key={c.key} c={c} />
          ))}
        </div>
      </div>

      {/* ขั้นตอน 9 ขั้น */}
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-bold text-slate-800 tracking-wider">ขั้นตอนการดำเนินงานตามคู่มือ WI-EFL-04 และกรอบควบคุมภายใน</h4>
          <span className="text-[11px] text-slate-500">ผู้รับผิดชอบ: {a.owner_names?.join(', ') || 'ยังไม่มอบหมาย'}</span>
        </div>
        <ErrorBox error={error} />
        <ol className="space-y-1.5 mt-2">
          {d.steps.map((s) => (
            <li
              key={s.step_no}
              className={`rounded-lg border ${s.state === 'current' ? 'bg-white border-amber-400 ring-1 ring-amber-400' : s.state === 'completed' ? 'bg-white border-slate-200' : 'bg-slate-100/60 border-slate-200'}`}
            >
              <div className="flex items-start gap-3 px-3 py-2">
                <span className="mt-0.5 shrink-0">
                  {s.state === 'completed' && <CheckCircle2 className="w-4 h-4 text-emerald-600" />}
                  {s.state === 'current' && <Clock className="w-4 h-4 text-amber-600" />}
                  {s.state === 'skipped' && <MinusCircle className="w-4 h-4 text-slate-400" />}
                  {s.state === 'pending' && <Circle className="w-4 h-4 text-slate-300" />}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className={`text-xs font-semibold ${s.state === 'pending' || s.state === 'skipped' ? 'text-slate-500' : 'text-slate-900'}`}>
                      {s.step_no}. {s.name}
                      {s.optional && <span className="ml-1 text-[10px] font-normal text-slate-400">(ถ้าจำเป็น)</span>}
                    </span>
                    <span className="flex items-center gap-1.5">
                      {s.state === 'completed' && <span className="text-[10px] text-slate-500">{thDate(s.completed_at)}</span>}
                      {s.state === 'current' && s.due_date && <span className="text-[10px] text-slate-500">ครบกำหนด {thDate(s.due_date)}</span>}
                      {stepTimingText(s) && <Pill tone={s.state === 'current' ? s.timing : s.state === 'completed' ? s.timing || 'NOT_STARTED' : 'NOT_STARTED'}>{stepTimingText(s)}</Pill>}
                    </span>
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5">
                    {s.limit_text}
                    {s.state === 'skipped' && s.step_no === 5 && a.route === 'DIRECT' && ` · เหตุ: ${a.route_reason || 'เสนอ กกพ. โดยตรง'}`}
                    {s.state === 'skipped' && (s.step_no === 3 || s.step_no === 4) && a.admissibility === 'ACCEPTED' && s.step_no === 3 && ' · เอกสารครบถ้วนตั้งแต่ตรวจครั้งแรก'}
                    {s.state === 'skipped' && a.admissibility === 'REJECTED' && s.step_no !== 5 && ' · เสนอความเห็นไม่รับไว้พิจารณา'}
                  </div>
                  {s.note && s.state === 'completed' && <div className="text-[11px] text-slate-600 mt-0.5">บันทึก: {s.note}</div>}
                  {s.state === 'current' && (
                    <div className="mt-3">
                      <StepActions d={d} run={run} />
                    </div>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
        <div className="mt-2">
          <RevertButton d={d} run={run} />
        </div>
      </div>

      {/* ผลตรวจรับเรื่อง */}
      {sc && (
        <div className="p-4 rounded-xl border border-slate-200">
          <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-2">ผลตรวจสิทธิ เขตอำนาจ ระยะเวลา และเอกสาร (ขั้นที่ 2)</h4>
          <div className="flex flex-wrap gap-1.5">
            <Check ok={sc.standing} text="มีสิทธิ" />
            <Check ok={sc.jurisdiction} text="อยู่ในอำนาจ กกพ." />
            <Check ok={sc.timely} text={sc.timely === false ? 'ยื่นพ้นกำหนด' : 'ยื่นทันกำหนด'} />
            {sc.prior_steps_done !== null && sc.prior_steps_done !== undefined && <Check ok={sc.prior_steps_done} text="ทำตามขั้นตอนก่อนยื่นครบ" />}
            <Check ok={sc.docs_complete} text={sc.docs_complete ? 'เอกสารครบถ้วน' : 'เอกสารไม่ครบ'} />
            <Pill tone={a.admissibility === 'REJECTED' ? 'OVERDUE' : a.admissibility === 'ACCEPTED' ? 'DONE_ON_TIME' : 'NEAR_DUE'}>
              {a.admissibility === 'REJECTED' ? 'เสนอไม่รับไว้พิจารณา' : a.admissibility === 'ACCEPTED' ? 'รับไว้พิจารณา' : 'รอแก้ไขเอกสาร'}
            </Pill>
            {a.route && <Pill tone="NOT_STARTED">{ROUTE[a.route]}</Pill>}
          </div>
          {sc.note && <p className="text-[11px] text-slate-600 mt-2">เหตุผล: {sc.note}</p>}
        </div>
      )}

      {/* หนังสือขอข้อมูล/แจ้งแก้ไข */}
      {d.requests.length > 0 && <Requests d={d} run={run} />}

      {/* คู่กรณี */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4 rounded-xl bg-slate-50 border border-slate-200">
        <div>
          <div className="text-[11px] font-semibold text-slate-500 mb-1">{a.case_type === 'APPEAL' ? 'ผู้อุทธรณ์' : 'ผู้ร้องเรียน'}</div>
          <div className="text-xs sm:text-sm font-bold text-slate-900">{a.party_label}</div>
          <span className="inline-flex items-center gap-1 text-[11px] text-slate-500 mt-2">
            <Lock className="w-3 h-3" /> แสดงเป็นรหัสนามแฝง — ชื่อจริงเก็บแยกในระบบ party-vault
          </span>
        </div>
        <div className="space-y-1.5 border-t md:border-t-0 md:border-l border-slate-200 pt-3 md:pt-0 md:pl-4">
          <Field k={a.case_type === 'APPEAL' ? 'ผู้ออกคำสั่งที่ถูกอุทธรณ์' : 'คู่กรณี'} v={a.respondent || '-'} />
          {a.case_type === 'APPEAL' && <Field k={a.target_type === 'RULE' ? 'วันที่กฎประกาศใช้บังคับ' : 'วันได้รับแจ้งคำสั่ง'} v={thDate(a.notice_date)} />}
          <Field k="วันยื่น (ข้อ 5.2(3))" v={thDate(a.received_date)} />
          <Field k="ผลคำวินิจฉัย กกพ." v={a.decision_result || 'อยู่ระหว่างการพิจารณา'} strong />
          {a.erc_meeting_ref && <Field k="การประชุม กกพ." v={a.erc_meeting_ref} />}
        </div>
      </div>

      <div>
        <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-1.5">สรุปข้อเท็จจริงและประเด็น</h4>
        <p className="text-xs sm:text-sm text-slate-700 leading-relaxed bg-slate-50/70 p-4 rounded-xl border border-slate-200/80">{a.summary}</p>
      </div>

      {d.citations.length > 0 && (
        <div>
          <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-2">บทบัญญัติที่ใช้ประกอบการพิจารณา (ผูกตามรุ่น ณ วันเกิดเหตุ {thDate(a.incident_date)})</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {d.citations.map((c, i) => (
              <div key={i} className="p-3 rounded-xl border border-slate-200 bg-white flex items-center justify-between gap-2 hover:border-amber-400">
                <div>
                  <div className="text-xs font-bold text-slate-900">
                    {c.label} (รุ่นที่ {c.cited_version_no})
                  </div>
                  <p className="text-[11px] text-slate-500 mt-0.5">{c.note}</p>
                </div>
                <button
                  onClick={() => go('law', { eli: c.cited_eli_id, asOf: a.incident_date })}
                  className="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold bg-amber-50 text-amber-800 hover:bg-amber-100 border border-amber-200 shrink-0"
                >
                  ดูตัวบท ณ วันเกิดเหตุ
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {d.documents.length > 0 && (
        <div className="pt-2 border-t border-slate-200">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
            <h4 className="text-xs font-bold text-slate-700 tracking-wider">เอกสารในสำนวน</h4>
            <div className="flex flex-wrap gap-1.5">
              {d.documents.map((x, i) => (
                <button
                  key={x.document_id}
                  onClick={() => setDocIdx(i)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${docIdx === i ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}
                >
                  {x.doc_type.length > 36 ? x.doc_type.slice(0, 36) + '…' : x.doc_type}
                </button>
              ))}
            </div>
          </div>
          {doc && <DocView doc={doc} />}
        </div>
      )}

      <Timeline d={d} />
    </Card>
  );
}

function Check({ ok, text }) {
  return <Pill tone={ok === false ? 'OVERDUE' : 'DONE_ON_TIME'}>{(ok === false ? '✕ ' : '✓ ') + text}</Pill>;
}

function Requests({ d, run }) {
  const [respond, setRespond] = useState(null);
  const [date, setDate] = useState(todayIso());
  const label = { OPEN: 'รอคำตอบ', NEAR_DUE: 'ใกล้ครบกำหนด', OVERDUE: 'เกินกำหนด', ANSWERED: 'ได้รับแล้ว' };
  return (
    <div>
      <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-2 flex items-center gap-1.5">
        <Mail className="w-3.5 h-3.5" /> หนังสือแจ้งแก้ไข / ขอข้อเท็จจริง (ติดตามเมื่อใกล้ครบกำหนด)
      </h4>
      <div className="overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full text-xs min-w-[640px]">
          <thead className="bg-slate-50 text-[11px] text-slate-500">
            <tr>
              <th className="text-left px-3 py-2">ประเภท</th>
              <th className="text-left px-3 py-2">ผู้รับ</th>
              <th className="text-left px-3 py-2">ประเด็น</th>
              <th className="text-left px-3 py-2">ออก / กำหนดตอบ</th>
              <th className="text-left px-3 py-2">สถานะ</th>
            </tr>
          </thead>
          <tbody>
            {d.requests.map((r) => (
              <tr key={r.request_id} className="border-t border-slate-100 align-top">
                <td className="px-3 py-2">{r.kind === 'CORRECTION' ? 'แจ้งแก้ไข' : 'ขอข้อเท็จจริง'}</td>
                <td className="px-3 py-2">
                  <div className="font-semibold">{r.target_name}</div>
                  <div className="text-[10px] text-slate-500">{TARGET_TYPE[r.target_type]} · {r.doc_no || '-'}</div>
                </td>
                <td className="px-3 py-2">{r.subject}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  {thDate(r.issued_date)}
                  <div className="text-[10px] text-slate-500">ตอบภายใน {thDate(r.due_date)}</div>
                </td>
                <td className="px-3 py-2">
                  <Pill tone={r.state}>{label[r.state]}</Pill>
                  {r.responded_date && <div className="text-[10px] text-slate-500 mt-0.5">{thDate(r.responded_date)}</div>}
                  {!r.responded_date && d.can_edit && (
                    respond === r.request_id ? (
                      <div className="mt-1 flex gap-1">
                        <input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} className={`${smallInput} w-32`} />
                        <button
                          className="px-2 rounded bg-slate-900 text-white text-[11px]"
                          onClick={() => run('บันทึกการได้รับคำตอบแล้ว', (v) => api.respondRequest(d.appeal.appeal_id, v, r.request_id, { date })).then(() => setRespond(null))}
                        >
                          บันทึก
                        </button>
                      </div>
                    ) : (
                      <button className="block mt-1 text-[11px] font-semibold text-amber-700 hover:underline" onClick={() => setRespond(r.request_id)}>
                        บันทึกว่าได้รับคำตอบ
                      </button>
                    )
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const MS_LABEL = {
  RECEIVED: 'รับเรื่อง (E-Sarabun)', ASSIGNED: 'ผอ.คบ. มอบหมาย', SCREEN_DONE: 'ตรวจสิทธิ/ระยะเวลา/เอกสารเสร็จ',
  CORRECTION_DONE: 'เอกสารครบถ้วนหลังแจ้งแก้ไข', FACTS_DONE: 'แสวงหาข้อเท็จจริงเสร็จ', SUBC_FIRST_MEETING: 'ประชุมอนุฯ ครั้งแรก',
  SUBC_EXT_NOTICE: 'แจ้งขยายระยะเวลาอนุฯ', SUBC_EXTENDED_DUE: 'ครบกำหนดอนุฯ (ขยาย)', SUBC_REPORT: 'ได้รับรายงาน/ความเห็นอนุฯ',
  ERC_AGENDA_SENT: 'ส่งวาระ กกพ.', ERC_RECEIVED: 'กกพ. รับเรื่อง', ERC_RESOLUTION: 'กกพ. มีมติ', NOTIFY_SENT: 'ส่งหนังสือแจ้งผล',
  NOTIFY_RECEIVED: 'ผู้รับได้รับหนังสือแจ้งผล', CLOSED: 'ปิดเรื่อง'
};

function Timeline({ d }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="pt-2 border-t border-slate-200">
      <button onClick={() => setOpen(!open)} className="text-xs font-bold text-slate-700 flex items-center gap-1.5">
        <FileText className="w-3.5 h-3.5" /> บันทึกเหตุการณ์ของเรื่อง ({d.milestones.length}) {open ? '▲' : '▼'}
      </button>
      {open && (
        <ul className="mt-2 space-y-1">
          {d.milestones.map((m) => (
            <li key={m.code} className="text-[11px] text-slate-700 flex gap-3">
              <span className="w-24 shrink-0 text-slate-500">{thDate(m.at_date)}</span>
              <span className="font-semibold">{MS_LABEL[m.code] || m.code}</span>
              {m.note && <span className="text-slate-500">— {m.note}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
