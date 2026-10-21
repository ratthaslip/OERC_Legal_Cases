import React, { useMemo, useState } from 'react';
import { CheckCircle2, Send, UserCheck, XCircle } from 'lucide-react';
import { api } from '../../api.js';
import { thDate, todayIso } from '../../components/ui.jsx';
import { btnGhost, btnPrimary, L, ROUTE, smallInput, TARGET_TYPE } from './parts.jsx';

/** แผงดำเนินการของขั้นปัจจุบัน — ฟอร์มต่างกันตามขั้นใน WI-EFL-04 */
export default function StepActions({ d, run }) {
  const a = d.appeal;
  const cur = d.steps.find((s) => s.state === 'current');
  if (!cur) return null;
  const id = a.appeal_id;
  const ms = Object.fromEntries(d.milestones.map((m) => [m.code, m.at_date]));

  if (cur.step_no === 1) {
    return d.can_assign ? <Assign d={d} run={run} /> : <Waiting text="รอ ผอ.คบ. พิจารณามอบหมายเจ้าหน้าที่ผู้รับผิดชอบ" />;
  }
  if (!d.can_edit) return <Waiting text="ดำเนินการได้เฉพาะเจ้าหน้าที่ฝ่ายคดีผู้รับผิดชอบเรื่อง หรือ ผอ.คบ." />;

  switch (cur.step_no) {
    case 2:
      return <Screening d={d} run={run} />;
    case 3:
      return (
        <div className="space-y-3">
          <RequestForm d={d} run={run} kind="CORRECTION" />
          <CompleteBar
            label="เอกสารครบถ้วนแล้ว ดำเนินการต่อ"
            onDone={(date, extra) => run('บันทึกว่าแก้ไขเอกสารครบถ้วน', (v) => api.completeStep(id, v, 3, { date, ...extra }))}
            withForce={d.requests.some((r) => r.kind === 'CORRECTION' && !r.responded_date)}
          />
        </div>
      );
    case 4:
      return <Facts d={d} run={run} />;
    case 5:
      return <Subcommittee d={d} run={run} ms={ms} />;
    case 6:
      return (
        <CompleteBar
          label="ส่งวาระเสนอ กกพ. แล้ว"
          dateLabel="วันที่ส่งวาระ/ฝ่ายอำนวยการฯ รับเรื่อง"
          onDone={(date, extra) =>
            run('บันทึกการเสนอวาระ กกพ.', (v) => api.completeStep(id, v, 6, { date, agenda_sent_date: date, ...extra }))}
        />
      );
    case 7:
      return <Decision d={d} run={run} />;
    case 8:
      return <Notify d={d} run={run} />;
    case 9:
      return ms.NOTIFY_RECEIVED ? (
        <CompleteBar
          label="ปิดเรื่องและจัดเก็บหลักฐานครบถ้วน"
          hint={`ผู้รับได้รับหนังสือแจ้งผลเมื่อ ${thDate(ms.NOTIFY_RECEIVED)}`}
          onDone={(date, extra) => run('ปิดเรื่องแล้ว', (v) => api.completeStep(id, v, 9, { date, ...extra }))}
        />
      ) : (
        <MilestoneForm
          label="บันทึกวันที่ผู้รับได้รับหนังสือแจ้งผล (ใบตอบรับไปรษณีย์)"
          onSave={(date, note) => run('บันทึกใบตอบรับแล้ว', (v) => api.milestone(id, v, { code: 'NOTIFY_RECEIVED', date, note }))}
        />
      );
    default:
      return null;
  }
}

function Waiting({ text }) {
  return <p className="text-[11px] text-slate-500 italic">{text}</p>;
}

function Assign({ d, run }) {
  const [officer, setOfficer] = useState(d.officers[0]?.user_id || '');
  const [es, setEs] = useState(d.appeal.esarabun_no || '');
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 items-end">
      <div>
        <L>มอบหมายเจ้าหน้าที่ฝ่ายคดี</L>
        <select value={officer} onChange={(e) => setOfficer(e.target.value)} className={smallInput}>
          {d.officers.map((o) => (
            <option key={o.user_id} value={o.user_id}>
              {o.name}
            </option>
          ))}
        </select>
      </div>
      <div>
        <L>เลขรับ E-Sarabun</L>
        <input value={es} onChange={(e) => setEs(e.target.value)} className={smallInput} />
      </div>
      <button
        className={`${btnPrimary} flex items-center gap-1 justify-center`}
        onClick={() => run('มอบหมายผู้รับผิดชอบแล้ว', (v) => api.assign(d.appeal.appeal_id, v, { officer, esarabun_no: es || null }))}
      >
        <UserCheck className="w-3.5 h-3.5" /> มอบหมาย
      </button>
    </div>
  );
}

function Screening({ d, run }) {
  const a = d.appeal;
  const isAppeal = a.case_type === 'APPEAL';
  const filing = d.clocks.find((c) => c.key === 'filing');
  const [chk, setChk] = useState({ standing: true, jurisdiction: true, timely: true, prior_steps_done: true });
  const [docs, setDocs] = useState(Object.fromEntries(d.required_docs.map((x) => [x.key, true])));
  const [route, setRoute] = useState('SUBCOMMITTEE');
  const [note, setNote] = useState('');
  const [reg, setReg] = useState(a.register_no || '');
  const [date, setDate] = useState(todayIso());

  const timely = isAppeal ? (filing?.state === 'DONE_LATE' ? false : filing?.state === 'DONE_ON_TIME' ? true : null) : chk.timely;
  const eligible = chk.standing && chk.jurisdiction && timely !== false && (isAppeal || chk.prior_steps_done);
  const complete = Object.values(docs).every(Boolean);
  const result = !eligible ? 'REJECTED' : complete ? 'ACCEPTED' : 'NEED_CORRECTION';
  const label = {
    ACCEPTED: 'รับเรื่องไว้พิจารณา → ลงทะเบียนคุมงาน (FM-EFL-01) และแสวงหาข้อเท็จจริง',
    NEED_CORRECTION: 'มีสิทธิและทันกำหนด แต่เอกสารไม่ครบ → แจ้งให้แก้ไขในคราวเดียว',
    REJECTED: 'ไม่มีสิทธิ / พ้นกำหนด / ไม่อยู่ในอำนาจ → เสนอความเห็นไม่รับไว้พิจารณา'
  }[result];
  const box = (k, text) => (
    <label key={k} className="flex items-start gap-2 text-xs text-slate-700">
      <input type="checkbox" checked={chk[k]} onChange={(e) => setChk({ ...chk, [k]: e.target.checked })} className="mt-0.5" />
      {text}
    </label>
  );

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <div className="text-[11px] font-bold text-slate-700">สิทธิ เขตอำนาจ และระยะเวลา (ข้อ 5.3)</div>
          {box('standing', isAppeal ? 'ผู้ยื่นเป็นผู้ใช้พลังงาน ผู้รับใบอนุญาต หรือผู้มีส่วนได้เสียที่ได้รับผลกระทบ' : 'ผู้ร้องเรียนมีสิทธิร้องเรียนตามระเบียบ/ประกาศ/สัญญา')}
          {box('jurisdiction', 'เรื่องอยู่ในอำนาจการพิจารณาของ กกพ.')}
          {isAppeal ? (
            <div className={`text-xs px-2 py-1.5 rounded border ${timely === false ? 'bg-rose-50 border-rose-200 text-rose-800' : timely ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-slate-50 border-slate-200 text-slate-600'}`}>
              {timely === null
                ? 'ไม่มีวันได้รับแจ้งคำสั่ง — ตรวจหลักฐานใบตอบรับไปรษณีย์/ราชกิจจานุเบกษาก่อน'
                : `ระบบคำนวณ: ยื่นวันที่ ${filing.days_used} นับแต่ได้รับแจ้ง (${thDate(filing.start)}) → ${timely ? 'ทันกำหนด 30 วัน' : 'พ้นกำหนด 30 วัน'}`}
            </div>
          ) : (
            <>
              {box('timely', 'ยื่นภายในกำหนดเวลาตามระเบียบ/ประกาศ/สัญญา')}
              {box('prior_steps_done', 'ปฏิบัติตามขั้นตอนที่กำหนดในระเบียบ/ประกาศ/สัญญาครบแล้วก่อนยื่น')}
            </>
          )}
        </div>
        <div className="space-y-1.5">
          <div className="text-[11px] font-bold text-slate-700">ความครบถ้วนของหนังสือ (ข้อ 5.2)</div>
          {d.required_docs.map((x) => (
            <label key={x.key} className="flex items-start gap-2 text-xs text-slate-700">
              <input type="checkbox" checked={docs[x.key]} onChange={(e) => setDocs({ ...docs, [x.key]: e.target.checked })} className="mt-0.5" />
              {x.label}
            </label>
          ))}
        </div>
      </div>
      <div className={`text-xs font-semibold px-3 py-2 rounded-lg border ${result === 'REJECTED' ? 'bg-rose-50 border-rose-200 text-rose-800' : result === 'NEED_CORRECTION' ? 'bg-amber-50 border-amber-200 text-amber-900' : 'bg-emerald-50 border-emerald-200 text-emerald-800'}`}>
        ผลตรวจ: {label}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 items-end">
        {result === 'ACCEPTED' && (
          <div>
            <L>เลขทะเบียนคุมงาน FM-EFL-01</L>
            <input value={reg} onChange={(e) => setReg(e.target.value)} className={smallInput} />
          </div>
        )}
        {result === 'REJECTED' && (
          <>
            <div>
              <L>เสนอผู้มีอำนาจ</L>
              <select value={route} onChange={(e) => setRoute(e.target.value)} className={smallInput}>
                {Object.entries(ROUTE).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-2">
              <L hint="จำเป็น">เหตุผลประกอบความเห็นไม่รับ</L>
              <input value={note} onChange={(e) => setNote(e.target.value)} className={smallInput} placeholder="ข้อเท็จจริงและข้อกฎหมายที่ใช้" />
            </div>
          </>
        )}
        <div>
          <L>วันที่ตรวจเสร็จ</L>
          <input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} className={smallInput} />
        </div>
      </div>
      <div className="flex justify-end">
        <button
          disabled={result === 'REJECTED' && !note.trim()}
          className={btnPrimary}
          onClick={() =>
            run('บันทึกผลการตรวจแล้ว', (v) =>
              api.completeStep(a.appeal_id, v, 2, {
                date,
                result,
                route: result === 'REJECTED' ? route : undefined,
                note: note || undefined,
                register_no: reg || undefined,
                checks: { ...chk, docs }
              })
            )
          }
        >
          บันทึกผลตรวจและดำเนินการต่อ
        </button>
      </div>
    </div>
  );
}

function Facts({ d, run }) {
  const [route, setRoute] = useState('SUBCOMMITTEE');
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');
  const open = d.requests.some((r) => r.kind === 'INFO' && !r.responded_date);
  return (
    <div className="space-y-3">
      <RequestForm d={d} run={run} kind="INFO" />
      <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
        <div className="text-[11px] font-bold text-slate-700">ข้อเท็จจริงเพียงพอ → สรุปข้อเท็จจริง ข้อกฎหมาย ความเห็นเบื้องต้น แล้วเสนอ</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {Object.entries(ROUTE).map(([k, v]) => (
            <label key={k} className="flex items-center gap-2 text-xs">
              <input type="radio" checked={route === k} onChange={() => setRoute(k)} /> {v}
            </label>
          ))}
        </div>
        {route === 'DIRECT' && (
          <div>
            <L hint="ข้อ 5.3(3.2)">เหตุที่ไม่ผ่านอนุฯ (มีแนวคำวินิจฉัยแล้ว/เรื่องเร่งด่วน ตามความเห็นประธานอนุฯ)</L>
            <input value={reason} onChange={(e) => setReason(e.target.value)} className={smallInput} />
          </div>
        )}
        <div>
          <L>สรุปความเห็นเบื้องต้น</L>
          <input value={note} onChange={(e) => setNote(e.target.value)} className={smallInput} />
        </div>
      </div>
      <CompleteBar
        label="ส่งเรื่องเข้าสู่การพิจารณา"
        disabled={route === 'DIRECT' && !reason.trim()}
        withForce={open}
        onDone={(date, extra) =>
          run('ส่งเรื่องเข้าสู่การพิจารณาแล้ว', (v) =>
            api.completeStep(d.appeal.appeal_id, v, 4, { date, route, route_reason: reason || undefined, note: note || undefined, ...extra })
          )
        }
      />
    </div>
  );
}

function Subcommittee({ d, run, ms }) {
  const id = d.appeal.appeal_id;
  const sub = d.clocks.find((c) => c.key === 'subcommittee');
  const [ext, setExt] = useState(false);
  const [notice, setNotice] = useState(todayIso());
  const [newDue, setNewDue] = useState('');
  if (!ms.SUBC_FIRST_MEETING)
    return (
      <div className="space-y-3">
        <MilestoneForm
          label="บันทึกวันที่เรื่องเข้าที่ประชุมคณะอนุกรรมการฯ ครั้งแรก (เริ่มนับ 30 วัน)"
          onSave={(date, note) => run('บันทึกวันประชุมอนุฯ แล้ว', (v) => api.milestone(id, v, { code: 'SUBC_FIRST_MEETING', date, note }))}
        />
        <RequestForm d={d} run={run} kind="INFO" compact />
      </div>
    );
  const maxDue = sub?.start ? addDays(sub.start, 60) : '';
  return (
    <div className="space-y-3">
      <div className="text-xs text-slate-700">
        ประชุมครั้งแรก {thDate(ms.SUBC_FIRST_MEETING)} · ครบกำหนด {thDate(sub?.due)}
        {ms.SUBC_EXTENDED_DUE && <span className="ml-1 text-amber-700 font-semibold">(ขยายแล้ว)</span>}
      </div>
      {!ms.SUBC_EXTENDED_DUE &&
        (ext ? (
          <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 grid grid-cols-1 sm:grid-cols-3 gap-2 items-end">
            <div>
              <L hint="ต้องก่อนครบกำหนดเดิม">วันที่มีหนังสือแจ้งผู้อุทธรณ์</L>
              <input type="date" value={notice} max={todayIso()} onChange={(e) => setNotice(e.target.value)} className={smallInput} />
            </div>
            <div>
              <L hint="ไม่เกิน 30 วันจากกำหนดเดิม">ครบกำหนดใหม่</L>
              <input type="date" value={newDue} max={maxDue} onChange={(e) => setNewDue(e.target.value)} className={smallInput} />
            </div>
            <button
              className={btnPrimary}
              onClick={() =>
                run('บันทึกการขยายระยะเวลาแล้ว', (v) =>
                  api.milestone(id, v, { code: 'SUBC_EXT_NOTICE', date: notice, extended_due: newDue || undefined, note: 'แจ้งขยายระยะเวลา (ม.45 วิ.ปกครอง)' })
                )
              }
            >
              บันทึกการขยายเวลา
            </button>
          </div>
        ) : (
          <button className={btnGhost} onClick={() => setExt(true)}>
            ขยายระยะเวลาพิจารณา (ครั้งเดียว ≤ 30 วัน)
          </button>
        ))}
      <CompleteBar
        label="คณะอนุกรรมการฯ มีความเห็นแล้ว (ได้รับรายงานการประชุม)"
        dateLabel="วันที่ได้รับรายงานการประชุม"
        withNote
        onDone={(date, extra) => run('บันทึกความเห็นคณะอนุกรรมการฯ แล้ว', (v) => api.completeStep(id, v, 5, { date, ...extra }))}
      />
    </div>
  );
}

function Decision({ d, run }) {
  const isAppeal = d.appeal.case_type === 'APPEAL';
  const options = isAppeal
    ? ['ยกอุทธรณ์ (ยืนตามคำสั่ง)', 'แก้คำสั่ง', 'เพิกถอนคำสั่ง', 'ไม่รับอุทธรณ์ไว้พิจารณา']
    : ['ยกเรื่องร้องเรียน', 'ให้คู่สัญญาปฏิบัติตามสัญญา/ระเบียบ', 'ไม่รับเรื่องร้องเรียนไว้พิจารณา'];
  const [result, setResult] = useState(d.appeal.admissibility === 'REJECTED' ? options[options.length - 1] : options[0]);
  const [detail, setDetail] = useState('');
  const [ref, setRef] = useState('');
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <div>
          <L>ผลคำวินิจฉัยตามมติ กกพ.</L>
          <select value={result} onChange={(e) => setResult(e.target.value)} className={smallInput}>
            {options.map((o) => (
              <option key={o}>{o}</option>
            ))}
          </select>
        </div>
        <div>
          <L>รายละเอียดเพิ่มเติม</L>
          <input value={detail} onChange={(e) => setDetail(e.target.value)} className={smallInput} placeholder="เช่น ขยายเวลาปรับปรุง 45 วัน" />
        </div>
        <div>
          <L>การประชุม กกพ.</L>
          <input value={ref} onChange={(e) => setRef(e.target.value)} className={smallInput} placeholder="ครั้งที่ …/…" />
        </div>
      </div>
      <CompleteBar
        label="บันทึกมติ กกพ."
        dateLabel="วันที่ กกพ. มีมติ"
        onDone={(date, extra) =>
          run('บันทึกมติ กกพ. แล้ว', (v) =>
            api.completeStep(d.appeal.appeal_id, v, 7, { date, result: detail ? `${result} (${detail})` : result, meeting_ref: ref || undefined, ...extra })
          )
        }
      />
    </div>
  );
}

function Notify({ d, run }) {
  const isAppeal = d.appeal.case_type === 'APPEAL';
  const [rights, setRights] = useState(false);
  return (
    <div className="space-y-2">
      <div className="text-[11px] text-slate-600">
        หนังสือแจ้งผลต้องมีข้อเท็จจริงอันเป็นสาระสำคัญ ข้อกฎหมาย และเหตุผลในการใช้ดุลพินิจ ส่งไปรษณีย์ลงทะเบียนตอบรับ และสำเนาแจ้งฝ่ายงานที่เกี่ยวข้อง (ข้อ 5.7)
      </div>
      {isAppeal && (
        <label className="flex items-center gap-2 text-xs font-semibold text-slate-800">
          <input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} /> แจ้งสิทธิ วิธีการ และระยะเวลาฟ้องคดีต่อศาลปกครองแล้ว
        </label>
      )}
      <CompleteBar
        label="ส่งหนังสือแจ้งผลแล้ว"
        dateLabel="วันที่ส่งหนังสือ"
        disabled={isAppeal && !rights}
        onDone={(date, extra) =>
          run('บันทึกการส่งหนังสือแจ้งผลแล้ว', (v) => api.completeStep(d.appeal.appeal_id, v, 8, { date, rights_notice: rights, ...extra }))
        }
      />
    </div>
  );
}

function RequestForm({ d, run, kind, compact }) {
  const [open, setOpen] = useState(!compact && !d.requests.some((r) => r.kind === kind));
  const [f, setF] = useState({
    target_type: kind === 'CORRECTION' ? 'APPELLANT' : 'INTERNAL',
    target_name: kind === 'CORRECTION' ? d.appeal.party_label : '',
    subject: '',
    doc_no: '',
    issued_date: todayIso(),
    due_date: ''
  });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const defaultDue = useMemo(() => addDays(f.issued_date, 7), [f.issued_date]);
  if (!open)
    return (
      <button className={btnGhost} onClick={() => setOpen(true)}>
        + {kind === 'CORRECTION' ? 'ออกหนังสือแจ้งให้แก้ไข/ส่งเอกสาร' : 'ออกหนังสือขอข้อเท็จจริง/เอกสาร'}
      </button>
    );
  return (
    <div className="p-3 rounded-lg border border-slate-200 bg-white space-y-2">
      <div className="text-[11px] font-bold text-slate-700">
        {kind === 'CORRECTION' ? 'หนังสือแจ้งให้แก้ไขเพิ่มเติม (แจ้งในคราวเดียวที่ตรวจพบ · ไม่ขยายระยะเวลาอุทธรณ์)' : 'หนังสือ/บันทึกขอข้อเท็จจริงและพยานหลักฐาน (ข้อ 5.4)'}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <div>
          <L>ผู้รับ</L>
          <select value={f.target_type} onChange={set('target_type')} className={smallInput}>
            {Object.entries(TARGET_TYPE).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div className="sm:col-span-2">
          <L>ชื่อผู้รับ / ฝ่ายงาน</L>
          <input value={f.target_name} onChange={set('target_name')} className={smallInput} />
        </div>
        <div className="sm:col-span-3">
          <L>ประเด็น ข้อมูล/เอกสารที่ต้องการ และเหตุผลความจำเป็น</L>
          <input value={f.subject} onChange={set('subject')} className={smallInput} />
        </div>
        <div>
          <L>เลขที่หนังสือ</L>
          <input value={f.doc_no} onChange={set('doc_no')} className={smallInput} />
        </div>
        <div>
          <L>วันที่ออกหนังสือ</L>
          <input type="date" value={f.issued_date} max={todayIso()} onChange={set('issued_date')} className={smallInput} />
        </div>
        <div>
          <L hint={`ค่าเริ่มต้น 7 วัน = ${thDate(defaultDue)}`}>กำหนดตอบกลับ</L>
          <input type="date" value={f.due_date} onChange={set('due_date')} className={smallInput} />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        {compact && (
          <button className={btnGhost} onClick={() => setOpen(false)}>
            ยกเลิก
          </button>
        )}
        <button
          disabled={!f.subject.trim() || !f.target_name.trim()}
          className={`${btnPrimary} flex items-center gap-1`}
          onClick={() =>
            run('บันทึกหนังสือแล้ว', (v) =>
              api.addRequest(d.appeal.appeal_id, v, { ...f, due_date: f.due_date || undefined, doc_no: f.doc_no || undefined })
            ).then(() => setF({ ...f, subject: '', doc_no: '' }))
          }
        >
          <Send className="w-3.5 h-3.5" /> บันทึกหนังสือ
        </button>
      </div>
    </div>
  );
}

function MilestoneForm({ label, onSave }) {
  const [date, setDate] = useState(todayIso());
  const [note, setNote] = useState('');
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 items-end">
      <div className="sm:col-span-3 text-[11px] font-bold text-slate-700">{label}</div>
      <input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} className={smallInput} />
      <input value={note} onChange={(e) => setNote(e.target.value)} className={smallInput} placeholder="หมายเหตุ (ถ้ามี)" />
      <button className={btnPrimary} onClick={() => onSave(date, note || undefined)}>
        บันทึก
      </button>
    </div>
  );
}

function CompleteBar({ label, dateLabel = 'วันที่ดำเนินการ', onDone, disabled, withForce, withNote, hint }) {
  const [date, setDate] = useState(todayIso());
  const [force, setForce] = useState('');
  const [note, setNote] = useState('');
  return (
    <div className="pt-2 border-t border-slate-200 space-y-2">
      {hint && <div className="text-[11px] text-slate-600">{hint}</div>}
      {withForce && (
        <div>
          <L hint="ยังมีหนังสือที่ยังไม่ได้รับคำตอบ">เหตุผลที่ดำเนินการต่อโดยไม่รอคำตอบ</L>
          <input value={force} onChange={(e) => setForce(e.target.value)} className={smallInput} placeholder="เช่น พ้นกำหนดแล้ว ข้อเท็จจริงเพียงพอ" />
        </div>
      )}
      {withNote && (
        <div>
          <L>สรุปความเห็น</L>
          <input value={note} onChange={(e) => setNote(e.target.value)} className={smallInput} />
        </div>
      )}
      <div className="flex flex-wrap items-end justify-end gap-2">
        <div>
          <L>{dateLabel}</L>
          <input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} className={smallInput} />
        </div>
        <button
          disabled={disabled || (withForce && !force.trim())}
          className={`${btnPrimary} flex items-center gap-1`}
          onClick={() => onDone(date, { force_reason: force || undefined, note: note || undefined })}
        >
          <CheckCircle2 className="w-3.5 h-3.5" /> {label}
        </button>
      </div>
    </div>
  );
}

export function RevertButton({ d, run }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const done = d.steps.filter((s) => s.state === 'completed').length;
  if (!d.can_edit || done < 2) return null;
  return open ? (
    <div className="flex flex-wrap items-end gap-2 justify-end">
      <input value={reason} onChange={(e) => setReason(e.target.value)} className={`${smallInput} max-w-xs`} placeholder="เหตุผลการย้อนขั้น (จำเป็น)" />
      <button className={btnGhost} onClick={() => setOpen(false)}>
        ยกเลิก
      </button>
      <button disabled={!reason.trim()} className={`${btnGhost} text-rose-700 border-rose-300 flex items-center gap-1`} onClick={() => run('ย้อนขั้นตอนแล้ว', (v) => api.revert(d.appeal.appeal_id, v, reason))}>
        <XCircle className="w-3.5 h-3.5" /> ยืนยันย้อนขั้น
      </button>
    </div>
  ) : (
    <div className="text-right">
      <button className="text-[11px] font-semibold text-slate-500 hover:text-rose-700" onClick={() => setOpen(true)}>
        ย้อนกลับขั้นก่อนหน้า
      </button>
    </div>
  );
}

export function addDays(iso, n) {
  if (!iso) return '';
  const [y, m, dd] = iso.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, dd + n));
  return t.toISOString().slice(0, 10);
}
