import React, { useEffect, useState } from 'react';
import { ArrowLeftRight, CheckCircle2, Lock, Search } from 'lucide-react';
import { api } from '../api.js';
import { Card, ClassBadge, ErrorBox, inputCls, Label, LockedPanel, OmittedNote, Spinner, thDate, useDebounced } from '../components/ui.jsx';
import { DocView, Field } from '../components/ui.jsx';

export default function CasesPage({ me, go, route, switchUser }) {
  const [filters, setFilters] = useState({ q: '', court_level: '', sector: '' });
  const [list, setList] = useState(null);
  const [selected, setSelected] = useState(route.id || null);
  const [detail, setDetail] = useState(null);
  const [docIdx, setDocIdx] = useState(0);
  const [error, setError] = useState(null);
  const q = useDebounced(filters.q);

  useEffect(() => {
    if (route.id) setSelected(route.id);
  }, [route.nonce]);

  useEffect(() => {
    if (me.role === 'PUB') return;
    api
      .cases({ q, court_level: filters.court_level, sector: filters.sector })
      .then((r) => {
        setList(r);
        if (!selected && r.items.length) setSelected(r.items[0].case_id);
      })
      .catch(setError);
  }, [q, filters.court_level, filters.sector]);

  useEffect(() => {
    if (!selected || me.role === 'PUB') return;
    setDetail(null);
    setDocIdx(0);
    api.courtCase(selected).then(setDetail).catch((e) => setError(e.status === 404 ? new Error('ไม่พบสำนวนคดีนี้ หรือคุณไม่มีสิทธิ์เข้าถึง') : e));
  }, [selected]);

  if (me.role === 'PUB')
    return <LockedPanel title="ระบบสืบค้นงานคดีสำหรับเจ้าหน้าที่สำนักงาน กกพ." color="indigo" onLogin={() => switchUser('lgl1')} onBack={() => go('law')} />;

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const k = detail?.case;

  return (
    <div className="space-y-5">
      <Card className="p-4 sm:p-5">
        <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-end">
          <div className="md:col-span-6">
            <Label>ค้นหาสำนวนคดี (เลขคดีดำ, เลขคดีแดง, ประเด็นพิพาท, มาตราที่อ้างอิง)</Label>
            <div className="relative">
              <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input value={filters.q} onChange={set('q')} placeholder="เช่น อ. 1120/2566, พ. 89/2565, มาตรา 75, ขยายเวลา SCOD..." className={`${inputCls} pl-10`} />
            </div>
          </div>
          <div className="md:col-span-3">
            <Label>ชั้นศาลปกครอง</Label>
            <select value={filters.court_level} onChange={set('court_level')} className={inputCls}>
              <option value="">ทุกชั้นศาล</option>
              {list?.court_levels.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="md:col-span-3">
            <Label>ประเภทกิจการพลังงาน</Label>
            <select value={filters.sector} onChange={set('sector')} className={inputCls}>
              <option value="">ทุกประเภทกิจการ</option>
              {list?.sectors.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        <div className="lg:col-span-4 space-y-3 lg:max-h-[calc(100vh-160px)] lg:overflow-y-auto lg:pr-1">
          <OmittedNote count={list?.restricted_omitted} what="คดี" />
          {!list && <Spinner />}
          {list?.items.length === 0 && <p className="text-xs text-slate-500 text-center py-6">ไม่พบสำนวนคดีที่ตรงกับเงื่อนไข</p>}
          {list?.items.map((c) => (
            <div
              key={c.case_id}
              onClick={() => setSelected(c.case_id)}
              className={`p-4 rounded-xl border cursor-pointer bg-white ${
                c.case_id === selected ? 'border-indigo-500 ring-2 ring-indigo-500/20 shadow-xs' : 'border-slate-200 hover:border-slate-300'
              }`}
            >
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-slate-900 text-white">{c.black_no}</span>
                  {c.red_no && <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-600 text-white">{c.red_no}</span>}
                  <ClassBadge classification={c.classification} />
                </div>
                <span className="text-[10px] font-semibold text-indigo-700 shrink-0">{c.court_level}</span>
              </div>
              <h3 className="text-xs sm:text-sm font-bold text-slate-900 line-clamp-2 mb-2">{c.subject}</h3>
              <div className="text-[11px] text-slate-500 space-y-1">
                <div className="line-clamp-1">ผลคดี/สถานะ: {c.result}</div>
                <div className="flex items-center justify-between pt-1 border-t border-slate-100">
                  <span>วันเกิดเหตุ: {thDate(c.incident_date)}</span>
                  {c.origin_appeal_no && <span className="text-amber-700 font-semibold">ต้นเรื่อง: {c.origin_appeal_no}</span>}
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="lg:col-span-8">
          <ErrorBox error={error} />
          {selected && !detail && !error && <Spinner />}
          {k && (
            <Card className="p-5 sm:p-6 space-y-5">
              <div className="border-b border-slate-200 pb-4">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="px-3 py-1 rounded-lg text-xs font-bold bg-slate-900 text-white">คดีดำที่ {k.black_no}</span>
                    {k.red_no && <span className="px-3 py-1 rounded-lg text-xs font-bold bg-rose-600 text-white">คดีแดงที่ {k.red_no}</span>}
                    <span className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-indigo-50 text-indigo-800 border border-indigo-200">{k.court_level}</span>
                    <ClassBadge classification={k.classification} />
                  </div>
                  {k.appeal_id && (
                    <button
                      onClick={() => go('appeal', { id: k.appeal_id })}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-50 text-amber-800 border border-amber-200 hover:bg-amber-100 flex items-center gap-1.5"
                    >
                      <ArrowLeftRight className="w-3.5 h-3.5" />
                      <span>ดูสำนวนอุทธรณ์ต้นเรื่อง ({k.origin_appeal_no})</span>
                    </button>
                  )}
                </div>
                <h2 className="text-base sm:text-lg font-bold text-slate-900 leading-snug">{k.subject}</h2>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4 rounded-xl bg-slate-50 border border-slate-200">
                <div>
                  <div className="text-[11px] font-semibold text-slate-500 mb-1">ผู้ฟ้องคดี (โจทก์)</div>
                  <div className="text-xs sm:text-sm font-bold text-slate-900">{k.party_label}</div>
                  <span className="inline-flex items-center gap-1 text-[11px] text-slate-500 mt-2">
                    <Lock className="w-3 h-3" /> แสดงเป็นรหัสนามแฝง
                  </span>
                </div>
                <div className="space-y-1.5 border-t md:border-t-0 md:border-l border-slate-200 pt-3 md:pt-0 md:pl-4">
                  <Field k="ผู้ถูกฟ้องคดี" v={k.defendant} />
                  <Field k="ผลคำพิพากษา / สถานะคดี" v={k.result} strong />
                  <Field k="ขั้นตอนคดี" v={k.stage_text} />
                  <Field k="วันเกิดเหตุต้นเรื่อง" v={thDate(k.incident_date)} />
                </div>
              </div>

              {detail.proceedings.length > 0 && (
                <div>
                  <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-2">ลำดับการดำเนินกระบวนพิจารณาในชั้นศาล</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                    {detail.proceedings.map((p, i) => (
                      <div key={i} className="p-3 rounded-xl border border-slate-200 bg-white">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-bold text-slate-900">{p.title}</span>
                          <span className="text-[11px] font-semibold text-indigo-700 shrink-0">{thDate(p.proceeding_date)}</span>
                        </div>
                        <p className="text-[11px] text-slate-500 mt-1">{p.detail}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-1.5">สรุปสาระสำคัญและบรรทัดฐานคำพิพากษา</h4>
                <p className="text-xs sm:text-sm text-slate-700 leading-relaxed bg-slate-50/70 p-4 rounded-xl border border-slate-200/80">{k.summary}</p>
              </div>

              <div>
                <h4 className="text-xs font-bold text-slate-700 tracking-wider mb-2">
                  บทบัญญัติที่เกี่ยวข้อง (ผูกตามรุ่น ณ วันเกิดเหตุ {thDate(k.incident_date)})
                </h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {detail.citations.map((c, i) => (
                    <div key={i} className="p-3 rounded-xl border border-slate-200 bg-white flex items-center justify-between gap-2 hover:border-indigo-400">
                      <div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-bold text-slate-900">
                            {c.label} (รุ่นที่ {c.cited_version_no})
                          </span>
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                        </div>
                        <p className="text-[11px] text-slate-500 mt-0.5">{c.note}</p>
                      </div>
                      <button
                        onClick={() => go('law', { eli: c.cited_eli_id, asOf: k.incident_date })}
                        className="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold bg-indigo-50 text-indigo-800 hover:bg-indigo-100 border border-indigo-200 shrink-0"
                      >
                        ดูตัวบท ณ วันเกิดเหตุ
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              {detail.documents.length > 0 && (
                <div className="pt-2 border-t border-slate-200">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <h4 className="text-xs font-bold text-slate-700 tracking-wider">เอกสารในสำนวนคดี</h4>
                    <div className="flex flex-wrap gap-1.5">
                      {detail.documents.map((x, i) => (
                        <button
                          key={x.document_id}
                          onClick={() => setDocIdx(i)}
                          className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${docIdx === i ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}
                        >
                          {x.doc_type.length > 40 ? x.doc_type.slice(0, 40) + '…' : x.doc_type}
                        </button>
                      ))}
                    </div>
                  </div>
                  {detail.documents[docIdx] && <DocView doc={detail.documents[docIdx]} />}
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
