import React, { useEffect, useState } from 'react';
import { AlertTriangle, Database, Download, FileText, History, Layers } from 'lucide-react';
import { api, downloadRegisterExport } from '../../api.js';
import { Card, ErrorBox, Spinner, thDate } from '../../components/ui.jsx';

const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);
const dt = (iso) => (iso ? `${thDate(iso.slice(0, 10))} ${iso.slice(11, 16)} น.` : '—');

/** แถบความคืบหน้าเดียว (มีตัวบท / ทั้งหมด) — สีเดียว ตัวเลขเป็นสีตัวอักษร ไม่ใช้สีของแถบ */
function Meter({ label, sub, done, total, onClick }) {
  const p = pct(done, total);
  const Row = onClick ? 'button' : 'div';
  return (
    <Row
      onClick={onClick}
      title={`${label}: มีตัวบท ${done} จาก ${total} ฉบับ (${p}%)`}
      className={`w-full text-left grid grid-cols-12 items-center gap-3 py-1.5 ${onClick ? 'hover:bg-slate-50 rounded' : ''}`}
    >
      <span className="col-span-5 min-w-0">
        <span className="block text-xs text-slate-800 truncate">{label}</span>
        {sub && <span className="block text-[10px] text-slate-500 truncate">{sub}</span>}
      </span>
      <span className="col-span-5 h-2 rounded bg-slate-100 overflow-hidden">
        <span className="block h-full rounded bg-slate-700" style={{ width: `${Math.max(p, done ? 2 : 0)}%` }} />
      </span>
      <span className="col-span-2 text-right text-xs tabular-nums text-slate-700">
        {done}/{total}
      </span>
    </Row>
  );
}

function Tile({ icon: Icon, label, value, sub, children }) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-500">
        <Icon className="w-3.5 h-3.5" /> {label}
      </div>
      <div className="mt-1 text-2xl font-bold text-slate-900 tabular-nums">{value}</div>
      {sub && <div className="text-[11px] text-slate-500 mt-0.5">{sub}</div>}
      {children}
    </Card>
  );
}

function Section({ icon: Icon, title, right, children }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-2 mb-3">
        <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
          <Icon className="w-4 h-4 text-amber-600" /> {title}
        </h3>
        {right}
      </div>
      {children}
    </Card>
  );
}

/** ความครอบคลุมของข้อมูลกฎหมาย: นำเข้าแล้วกี่ฉบับ ยังขาดอะไร ประเด็นค้าง และบันทึกการนำเข้า */
export default function CoveragePanel({ go }) {
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);
  const [allGroups, setAllGroups] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.coverage().then(setD).catch(setError);
  }, []);
  if (error) return <ErrorBox error={error} />;
  if (!d) return <Spinner />;
  const { laws, register: r } = d;
  const groups = [...r.by_group].sort((a, b) => b.total - a.total);
  const exportMissing = async () => {
    setBusy(true);
    try {
      await downloadRegisterExport({ has_text: false });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const types = Object.entries(laws.by_type).map(([t, n]) => `${t} ${n}`).join(' · ');

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-500">
          นำเข้าล่าสุด {dt(d.last_import)} · ทะเบียนจากไฟล์ {r.source_file || '—'}
        </p>
        <button
          onClick={exportMissing}
          disabled={busy}
          className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 text-white hover:bg-slate-800 disabled:opacity-50 flex items-center gap-1.5"
        >
          <Download className="w-3.5 h-3.5" /> {busy ? 'กำลังสร้างไฟล์…' : `ส่งออกรายการที่ยังไม่มีตัวบท (${r.total - r.with_text})`}
        </button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Tile icon={FileText} label="กฎหมายที่มีตัวบทเต็มในระบบ" value={`${laws.instruments} ฉบับ`} sub={types} />
        <Tile
          icon={Layers}
          label="มาตรา / ข้อ"
          value={laws.provisions.toLocaleString()}
          sub={`${laws.versions.toLocaleString()} รุ่นถ้อยคำ · แก้ไขแล้ว ${laws.amended_provisions} มาตรา/ข้อ`}
        />
        <Tile icon={Database} label="ทะเบียนลำดับรองที่มีตัวบทเต็ม" value={`${r.with_text} / ${r.total}`} sub={`${pct(r.with_text, r.total)}%`}>
          <div className="mt-2 h-2 rounded bg-slate-100 overflow-hidden">
            <div className="h-full rounded bg-slate-700" style={{ width: `${Math.max(pct(r.with_text, r.total), 2)}%` }} />
          </div>
        </Tile>
        <Tile
          icon={AlertTriangle}
          label="ใช้บังคับแต่ยังไม่มีตัวบท"
          value={`${r.in_force_missing} ฉบับ`}
          sub={`ฝ่ายกฎหมายมีไฟล์แนบตามทะเบียน ${r.with_files} ฉบับ (Word ${r.with_word})`}
        />
      </div>
      {laws.demo_instruments > 0 && (
        <p className="text-[11px] text-rose-700">มีตัวบทสาธิต {laws.demo_instruments} ฉบับในระบบ (ไม่นับรวมข้างต้น)</p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Section icon={Database} title="ทะเบียนมีตัวบทเต็ม แยกตามมาตราที่ให้อำนาจ (15 อันดับแรก)">
          {r.by_section.map((s) => (
            <Meter key={s.key} label={`มาตรา ${s.key}`} done={s.with_text} total={s.total}
              onClick={() => go('law', { tab: 'register', section: s.key })} />
          ))}
        </Section>
        <div className="space-y-4">
          <Section icon={Database} title="แยกตามสถานะ">
            {r.by_status.map((s) => (
              <Meter key={s.key} label={s.label} sub={s.assumed ? `ทะเบียนไม่ระบุสถานะ ${s.assumed} ฉบับ (ถือว่าใช้บังคับ)` : null}
                done={s.with_text} total={s.total} />
            ))}
          </Section>
          <Section icon={Database} title="แยกตามประเภท">
            {r.by_doc_type.map((s) => (
              <Meter key={s.key} label={s.key} done={s.with_text} total={s.total} />
            ))}
          </Section>
        </div>
      </div>

      <Section
        icon={Layers}
        title={`แยกตามกลุ่มเรื่องในทะเบียน (${groups.length} กลุ่ม)`}
        right={
          groups.length > 12 && (
            <button onClick={() => setAllGroups(!allGroups)} className="text-[11px] font-semibold text-amber-700 hover:underline">
              {allGroups ? 'แสดง 12 กลุ่มใหญ่' : 'แสดงทุกกลุ่ม'}
            </button>
          )
        }
      >
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6">
          {(allGroups ? groups : groups.slice(0, 12)).map((g) => (
            <Meter key={g.key} label={g.label || `กลุ่ม ${g.key}`} sub={`กลุ่ม ${g.key}`} done={g.with_text} total={g.total} />
          ))}
        </div>
      </Section>

      <Section icon={AlertTriangle} title={`ประเด็นค้างที่ต้องให้นิติกรยืนยัน (${d.open_issues.length})`}
        right={
          <button onClick={() => go('law', { tab: 'register', issues: 1 })} className="text-[11px] font-semibold text-amber-700 hover:underline">
            ทะเบียน: {r.with_issues} ระเบียนมีประเด็นข้อมูล →
          </button>
        }
      >
        <ul className="divide-y divide-slate-100">
          {d.open_issues.map((x) => (
            <li key={x.issue_id} className="py-2 flex gap-3 items-start">
              <span className="shrink-0 mt-0.5 text-[10px] font-semibold px-1.5 py-0.5 rounded border border-amber-300 bg-amber-50 text-amber-900">
                {x.kind_th}
              </span>
              <div className="min-w-0 flex-1">
                <button onClick={() => x.open_eli && go('law', { eli: x.open_eli })} className="text-xs font-semibold text-slate-900 hover:underline text-left">
                  {x.title_th || x.instrument_eli}
                </button>
                <p className="text-[11px] text-slate-600">{x.note}</p>
              </div>
            </li>
          ))}
        </ul>
      </Section>

      <Section icon={FileText} title="ตัวบทในระบบ">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-[11px] text-slate-500 text-left">
              <tr className="border-b border-slate-200">
                <th className="py-1.5 pr-3 font-semibold">ชื่อ</th>
                <th className="py-1.5 pr-3 font-semibold">ประเภท</th>
                <th className="py-1.5 pr-3 font-semibold text-right">มาตรา/ข้อ</th>
                <th className="py-1.5 pr-3 font-semibold text-right">รุ่น</th>
                <th className="py-1.5 pr-3 font-semibold">ทะเบียน</th>
                <th className="py-1.5 pr-3 font-semibold">นำเข้าจาก</th>
                <th className="py-1.5 font-semibold text-right">ประเด็นค้าง</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {d.instruments.map((i) => (
                <tr key={i.eli_id} className="hover:bg-slate-50 cursor-pointer" onClick={() => i.first_provision_eli && go('law', { eli: i.first_provision_eli })}>
                  <td className="py-1.5 pr-3 text-slate-900 max-w-md"><span className="line-clamp-2">{i.title_th}</span></td>
                  <td className="py-1.5 pr-3 text-slate-600 whitespace-nowrap">{i.type_th}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{i.provisions}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{i.versions}</td>
                  <td className="py-1.5 pr-3 font-mono text-[11px] text-slate-500">{i.register_no ? `#${i.register_no}` : '—'}</td>
                  <td className="py-1.5 pr-3 text-[11px] text-slate-500 whitespace-nowrap">
                    {i.last_import ? <>{i.last_import.source_name.split('/').pop()} <span className="font-mono">{i.last_import.sha}</span></> : '—'}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{i.open_issues || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section icon={History} title="บันทึกการนำเข้า (30 รายการล่าสุด)">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-[11px] text-slate-500 text-left">
              <tr className="border-b border-slate-200">
                <th className="py-1.5 pr-3 font-semibold">เวลา</th>
                <th className="py-1.5 pr-3 font-semibold">ชนิด</th>
                <th className="py-1.5 pr-3 font-semibold">ไฟล์ต้นฉบับ (SHA-256)</th>
                <th className="py-1.5 pr-3 font-semibold">รายการ</th>
                <th className="py-1.5 pr-3 font-semibold text-right">จำนวน</th>
                <th className="py-1.5 font-semibold">ผู้นำเข้า</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {d.imports.map((x) => (
                <tr key={x.import_id}>
                  <td className="py-1.5 pr-3 whitespace-nowrap text-slate-600">{dt(x.imported_at)}</td>
                  <td className="py-1.5 pr-3 whitespace-nowrap">{x.kind === 'register' ? 'ทะเบียน (Excel)' : 'ตัวบท (JSON)'}</td>
                  <td className="py-1.5 pr-3 text-[11px] whitespace-nowrap">
                    {x.source_name.split('/').pop()} <span className="font-mono text-slate-500">{x.sha}</span>
                  </td>
                  <td className="py-1.5 pr-3 max-w-sm"><span className="line-clamp-1">{x.title_th || '—'}</span></td>
                  <td className="py-1.5 pr-3 text-right whitespace-nowrap tabular-nums">
                    {x.kind === 'register' ? `${x.rows_total} แถว · ผูกตัวบท ${x.rows_linked}` : `${x.provisions} มาตรา · ${x.versions} รุ่น`}
                  </td>
                  <td className="py-1.5 text-slate-600">{x.actor}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
