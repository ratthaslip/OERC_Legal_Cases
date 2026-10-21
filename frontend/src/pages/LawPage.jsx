import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, Calendar, ChevronRight, Clock, Copy, Gavel, History, Lock, Scale, Search, Timer } from 'lucide-react';
import { api } from '../api.js';
import { Card, ErrorBox, inputCls, Label, Spinner, thDate, todayIso, useDebounced } from '../components/ui.jsx';

const DEFAULT_ELI = 'eli/th/act/2550/sec-75';

export default function LawPage({ me, go, notify, route }) {
  const [instruments, setInstruments] = useState([]);
  const [categories, setCategories] = useState([]);
  const [category, setCategory] = useState('');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState(null);
  const [eli, setEli] = useState(route.eli || DEFAULT_ELI);
  const [asOf, setAsOf] = useState(route.asOf || todayIso());
  const [detail, setDetail] = useState(null);
  const [compare, setCompare] = useState(false);
  const [error, setError] = useState(null);
  const q = useDebounced(query);

  useEffect(() => {
    if (route.eli) setEli(route.eli);
    if (route.asOf) setAsOf(route.asOf);
  }, [route.nonce]);

  useEffect(() => {
    api.instruments(category || undefined).then((r) => {
      setInstruments(r.items);
      if (!categories.length) setCategories(r.categories);
    });
  }, [category]);

  useEffect(() => {
    if (!q.trim()) return setHits(null);
    api.searchLaws(q, asOf).then((r) => setHits(r.items)).catch(setError);
  }, [q, asOf]);

  useEffect(() => {
    setDetail(null);
    setError(null);
    api.provision(eli, asOf).then(setDetail).catch(setError);
  }, [eli, asOf]);

  const active = useMemo(
    () => detail?.versions.find((v) => v.version_no === detail.resolved_version_no) || null,
    [detail]
  );
  const cites = (detail?.citations || []).filter((c) => active && c.cited_version_no === active.version_no);
  const appealCites = cites.filter((c) => c.owner_type === 'appeal');
  const courtCites = cites.filter((c) => c.owner_type === 'court_case');
  const internal = me.role !== 'PUB';

  const copyRef = async () => {
    if (!detail || !active) return;
    const p = detail.provision;
    const text = `${p.provision_no} ${p.instrument_title} (รุ่นที่ ${active.version_no} มีผล ${thDate(active.valid_from)} – ${
      active.valid_to ? thDate(active.valid_to) : 'ปัจจุบัน'
    })\n${active.body_text}\nรหัสอ้างอิง: ${p.eli_id}/v${active.version_no}`;
    try {
      await navigator.clipboard.writeText(text);
      notify('คัดลอกตัวบทพร้อมรหัสอ้างอิงแล้ว');
    } catch {
      notify('เบราว์เซอร์ไม่อนุญาตให้คัดลอกอัตโนมัติ');
    }
  };

  return (
    <div className="space-y-5">
      <Card className="p-4 sm:p-5">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-end">
          <div className="lg:col-span-5">
            <Label>ค้นหากฎหมาย ระเบียบ ประกาศ หรือเลขมาตรา</Label>
            <div className="relative">
              <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="เช่น มาตรา 75, อุทธรณ์คำสั่ง, ใบอนุญาตผลิตไฟฟ้า, จุดเชื่อมโยง..."
                className={`${inputCls} pl-10`}
              />
            </div>
          </div>
          <div className="lg:col-span-3">
            <Label>หมวดหมู่กฎหมายพลังงาน</Label>
            <select value={category} onChange={(e) => setCategory(e.target.value)} className={inputCls}>
              <option value="">ทั้งหมดทุกหมวดหมู่</option>
              {categories.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </div>
          <div className="lg:col-span-4">
            <label className="block text-xs font-semibold text-slate-700 mb-1.5 flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Calendar className="w-3.5 h-3.5 text-amber-600" />
                <span>แสดงตัวบทที่มีผลบังคับใช้ ณ วันที่</span>
              </span>
              <span className="text-[11px] font-normal text-slate-500">ตรวจสอบย้อนหลังตามวันเกิดเหตุ</span>
            </label>
            <div className="flex items-center gap-1.5">
              <input type="date" value={asOf} onChange={(e) => e.target.value && setAsOf(e.target.value)} className={`${inputCls} flex-1 py-1.5`} />
              <button
                onClick={() => setAsOf('2019-05-15')}
                className={`px-2.5 py-2 rounded-lg text-xs font-medium border ${
                  asOf === '2019-05-15' ? 'bg-amber-600 text-white border-amber-600' : 'bg-slate-50 text-slate-700 border-slate-300 hover:bg-slate-100'
                }`}
              >
                15 พ.ค. 62
              </button>
              <button
                onClick={() => setAsOf(todayIso())}
                className={`px-2.5 py-2 rounded-lg text-xs font-medium border ${
                  asOf === todayIso() ? 'bg-slate-900 text-white border-slate-900' : 'bg-slate-50 text-slate-700 border-slate-300 hover:bg-slate-100'
                }`}
              >
                ปัจจุบัน
              </button>
            </div>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        <div className="lg:col-span-4 space-y-4">
          {hits && (
            <Card className="overflow-hidden">
              <div className="bg-amber-50 px-4 py-3 border-b border-amber-200 flex items-center justify-between">
                <span className="text-xs font-bold text-amber-900">ผลค้นหาตัวบท ณ วันที่ {thDate(asOf)}</span>
                <span className="text-[11px] text-amber-800">{hits.length} รายการ</span>
              </div>
              <div className="divide-y divide-slate-100">
                {hits.length === 0 && <p className="text-xs text-slate-500 p-4 text-center">ไม่พบตัวบทที่ตรงกับคำค้น</p>}
                {hits.map((h) => (
                  <button key={h.chunk_id} onClick={() => setEli(h.eli_id)} className="w-full text-left px-4 py-3 hover:bg-slate-50">
                    <div className="text-xs font-bold text-slate-900">{h.title}</div>
                    <div className="text-[11px] text-slate-500 line-clamp-2 mt-0.5">{h.chunk_text}</div>
                  </button>
                ))}
              </div>
            </Card>
          )}

          <Card className="overflow-hidden">
            <div className="bg-slate-50 px-4 py-3 border-b border-slate-200 flex items-center justify-between">
              <span className="text-xs font-bold text-slate-700 tracking-wider">สารบัญตราสารกฎหมายและรายมาตรา</span>
              <span className="text-[11px] text-slate-500">{instruments.length} ฉบับ</span>
            </div>
            <div className="divide-y divide-slate-100">
              {instruments.map((inst) => (
                <div key={inst.instrument_id} className="p-3.5">
                  <span className="inline-block px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-700 mb-1">
                    {inst.instrument_type}
                  </span>
                  <h3 className="text-xs sm:text-sm font-bold text-slate-900">{inst.title_th}</h3>
                  <p className="text-[11px] text-slate-500 mt-0.5">วันที่มีผลบังคับใช้: {thDate(inst.effective_date)}</p>
                  <div className="space-y-1 mt-2 pl-2 border-l-2 border-slate-200">
                    {inst.provisions.map((p) => (
                      <button
                        key={p.eli_id}
                        onClick={() => {
                          setEli(p.eli_id);
                          setCompare(false);
                        }}
                        className={`w-full text-left px-3 py-2 rounded-lg text-xs flex items-center justify-between ${
                          p.eli_id === eli ? 'bg-amber-50 text-amber-900 font-bold border border-amber-200' : 'text-slate-700 hover:bg-slate-50'
                        }`}
                      >
                        <div className="min-w-0">
                          <span className="font-semibold">{p.provision_no}</span>
                          <span className="block text-slate-500 text-[11px] line-clamp-1 font-normal">{p.latest_summary}</span>
                        </div>
                        <div className="flex items-center gap-1 shrink-0 ml-2">
                          {p.version_count > 1 && (
                            <span className="px-1.5 py-0.5 text-[10px] rounded bg-amber-100 text-amber-800 font-medium">{p.version_count} รุ่น</span>
                          )}
                          <ChevronRight className="w-3.5 h-3.5 text-slate-400" />
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="lg:col-span-8 space-y-4">
          <Card className="p-5 sm:p-6">
            <ErrorBox error={error} />
            {!detail && !error && <Spinner />}
            {detail && (
              <>
                <div className="flex flex-wrap items-start justify-between gap-4 pb-4 border-b border-slate-200">
                  <div>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 mb-1">
                      <span>{detail.provision.instrument_title}</span>
                      <span>•</span>
                      <span>{detail.provision.part_heading}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-3">
                      <h2 className="text-xl font-bold text-slate-900">{detail.provision.provision_no}</h2>
                      {active && (
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                          ตัวบทรุ่นที่ {active.version_no} (มีผล {thDate(active.valid_from)} ถึง {active.valid_to ? thDate(active.valid_to) : 'ปัจจุบัน'})
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {detail.versions.length > 1 && (
                      <button
                        onClick={() => setCompare(!compare)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 border ${
                          compare ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                        }`}
                      >
                        <History className="w-3.5 h-3.5" />
                        <span>{compare ? 'ปิดหน้าต่างเปรียบเทียบรุ่น' : 'เปรียบเทียบประวัติการแก้ไข'}</span>
                      </button>
                    )}
                    <button onClick={copyRef} title="คัดลอกตัวบทพร้อมรหัสอ้างอิง" className="p-2 rounded-lg text-slate-500 hover:text-slate-800 hover:bg-slate-100 border border-slate-200">
                      <Copy className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {detail.problem ? (
                  <div className="mt-4 bg-rose-50 border border-rose-200 rounded-lg px-4 py-3 flex items-start gap-2 text-xs text-rose-900">
                    <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0" />
                    <span>
                      {detail.problem === 'NOT_YET_IN_FORCE'
                        ? `ณ วันที่ ${thDate(asOf)} มาตรานี้ยังไม่มีผลใช้บังคับ`
                        : `ณ วันที่ ${thDate(asOf)} มาตรานี้ถูกยกเลิกแล้ว`}{' '}
                      — ระบบไม่แสดงรุ่นอื่นแทน เพื่อไม่ให้อ้างตัวบทผิดช่วงเวลา
                    </span>
                  </div>
                ) : (
                  <div className="mt-4 bg-amber-50/80 border border-amber-200/80 rounded-lg px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 text-xs text-amber-900">
                    <div className="flex items-center gap-2">
                      <Clock className="w-4 h-4 text-amber-600 shrink-0" />
                      <span>
                        กำลังแสดงตัวบทที่มีผลบังคับใช้ ณ วันที่ <strong>{thDate(asOf)}</strong> — {active?.amended_by}
                      </span>
                    </div>
                    <span className="text-[11px] font-mono text-amber-700">
                      รหัสอ้างอิง: {detail.provision.eli_id}/v{active?.version_no}
                    </span>
                  </div>
                )}

                {!compare ? (
                  active && (
                    <div className="mt-5">
                      <div className="p-5 rounded-xl bg-slate-50 border border-slate-200/80 text-slate-900 text-sm sm:text-base leading-relaxed">
                        <span className="font-bold text-slate-950 mr-2">{detail.provision.provision_no}</span>
                        {active.body_text}
                      </div>
                      {active.relations.length > 0 && (
                        <div className="mt-4 pt-4 border-t border-slate-100">
                          <h4 className="text-xs font-bold text-slate-600 tracking-wider mb-2">ความสัมพันธ์ทางกฎหมายและลำดับศักดิ์</h4>
                          <div className="space-y-1.5">
                            {active.relations.map((r, i) => (
                              <div key={i} className="flex items-center gap-2 text-xs text-slate-700 bg-slate-50 px-3 py-2 rounded-lg border border-slate-200/60">
                                <span className="px-2 py-0.5 rounded bg-slate-200 text-slate-800 font-semibold text-[11px] shrink-0">{r.relation_type}</span>
                                <span>{r.target_text}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                ) : (
                  <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-4">
                    {detail.versions.map((v) => (
                      <div
                        key={v.version_no}
                        className={`p-4 rounded-xl border ${v.version_no === active?.version_no ? 'bg-amber-50/40 border-amber-300' : 'bg-slate-50 border-slate-200'}`}
                      >
                        <div className="flex items-center justify-between mb-2 gap-2">
                          <span className="text-xs font-bold text-slate-900">
                            รุ่นที่ {v.version_no} ({thDate(v.valid_from)} ถึง {v.valid_to ? thDate(v.valid_to) : 'ปัจจุบัน'})
                          </span>
                          {v.version_no === active?.version_no && (
                            <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-200 text-amber-900 shrink-0">ตรงกับวันที่เลือก</span>
                          )}
                        </div>
                        <p className="text-[11px] text-slate-500 mb-2.5">{v.amended_by}</p>
                        <p className="text-xs sm:text-sm text-slate-800 leading-relaxed bg-white p-3 rounded-lg border border-slate-200/80">{v.body_text}</p>
                        <button onClick={() => setAsOf(v.valid_from)} className="mt-2 text-[11px] font-semibold text-amber-700 hover:underline">
                          ดูตัวบทและบรรทัดฐานของรุ่นนี้
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                {active && (
                  <div className="mt-6 pt-5 border-t border-slate-200">
                    <h3 className="text-xs sm:text-sm font-bold text-slate-900 flex items-center gap-2 mb-3">
                      <Scale className="w-4 h-4 text-amber-600" />
                      <span>บรรทัดฐานที่อ้างอิง {detail.provision.provision_no} (รุ่นที่ {active.version_no}) แยกตามชั้นพิจารณา</span>
                    </h3>
                    {!internal ? (
                      <div className="flex items-center gap-2 text-xs text-slate-600 bg-slate-50 border border-dashed border-slate-300 rounded-lg px-3 py-3">
                        <Lock className="w-4 h-4 text-slate-400" />
                        <span>สำนวนอุทธรณ์และคดีที่อ้างอิงมาตรานี้เปิดให้เฉพาะเจ้าหน้าที่สำนักงาน กกพ.</span>
                      </div>
                    ) : (
                      <>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          <CiteColumn
                            title="คำวินิจฉัยและสำนวนงานอุทธรณ์"
                            icon={<Timer className="w-3.5 h-3.5 text-amber-600" />}
                            items={appealCites}
                            unit="เรื่อง"
                            empty="ไม่มีสำนวนอุทธรณ์ที่อ้างอิงมาตรารุ่นนี้"
                            tag="bg-amber-100 text-amber-900"
                            btn="bg-slate-900 hover:bg-slate-800"
                            btnLabel="เปิดดูในระบบงานอุทธรณ์"
                            onOpen={(c) => go('appeal', { id: c.owner_id })}
                          />
                          <CiteColumn
                            title="คำพิพากษาและสำนวนงานคดีปกครอง"
                            icon={<Gavel className="w-3.5 h-3.5 text-indigo-600" />}
                            items={courtCites}
                            unit="คดี"
                            empty="ไม่มีสำนวนคดีที่อ้างอิงมาตรารุ่นนี้"
                            tag="bg-indigo-100 text-indigo-900"
                            btn="bg-indigo-900 hover:bg-indigo-800"
                            btnLabel="เปิดดูในระบบงานคดี"
                            onOpen={(c) => go('court', { id: c.owner_id })}
                          />
                        </div>
                        {detail.citations_hidden > 0 && (
                          <div className="mt-3 flex items-center gap-2 text-[11px] text-slate-600 bg-slate-50 border border-dashed border-slate-300 rounded-lg px-3 py-2">
                            <Lock className="w-3.5 h-3.5 text-slate-400" />
                            <span>อีก {detail.citations_hidden} เรื่อง (ทุกรุ่น) อยู่นอกสิทธิ์การเข้าถึงของคุณ</span>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function CiteColumn({ title, icon, items, unit, empty, tag, btn, btnLabel, onOpen }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? items : items.slice(0, 4);
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <span className="text-xs font-bold text-slate-800 flex items-center gap-1.5">
          {icon}
          <span>{title}</span>
        </span>
        <span className="text-[11px] font-semibold text-slate-500">
          {items.length} {unit}
        </span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-slate-500 py-3 text-center">{empty}</p>
      ) : (
        <div className="space-y-2">
          {shown.map((c) => (
            <div key={`${c.owner_type}-${c.owner_id}`} className="p-3 rounded-lg border border-slate-200 bg-white flex flex-col gap-2">
              <div>
                <div className="flex flex-wrap items-center gap-1.5 mb-1">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${tag}`}>{c.ref_no}</span>
                  <span className="text-[10px] text-slate-500">วันเกิดเหตุ: {thDate(c.incident_date)}</span>
                  {c.classification === 'restricted' && <Lock className="w-3 h-3 text-rose-600" />}
                </div>
                <p className="text-xs font-semibold text-slate-900 line-clamp-2">{c.subject}</p>
                {c.result && <p className="text-[11px] text-slate-500 mt-0.5 line-clamp-1">ผล: {c.result}</p>}
              </div>
              <button onClick={() => onOpen(c)} className={`self-end px-2.5 py-1 rounded text-[11px] font-semibold text-white flex items-center gap-1 ${btn}`}>
                <span>{btnLabel}</span>
                <ArrowRight className="w-3 h-3" />
              </button>
            </div>
          ))}
          {items.length > 4 && (
            <button onClick={() => setShowAll(!showAll)} className="w-full text-[11px] font-semibold text-slate-600 hover:text-slate-900 py-1">
              {showAll ? 'แสดงน้อยลง' : `แสดงทั้งหมด ${items.length} ${unit}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
