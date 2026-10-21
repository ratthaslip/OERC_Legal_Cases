import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, BookOpen, Calendar, Gavel, Loader2, Lock, Power, SearchX, Shield, Sparkles, Timer } from 'lucide-react';
import { askAssistant } from '../api.js';
import { Card, thDate, todayIso } from '../components/ui.jsx';

const PRESETS = [
  'เปรียบเทียบแนววินิจฉัยชั้นอุทธรณ์และคำพิพากษาศาลปกครองสูงสุด เรื่องการยื่นอุทธรณ์ตามมาตรา 75',
  'หลักเกณฑ์การสั่งปรับทางปกครองตามมาตรา 128 กรณีท่อส่งก๊าซธรรมชาติ',
  'การประเมินความสามารถรองรับของสายป้อน (Feeder Capacity) ตามข้อ 12 มีแนววินิจฉัยอย่างไร',
  'หลักเกณฑ์การอุดหนุนภาษีคาร์บอนสำหรับโรงไฟฟ้าพลังงานนิวเคลียร์ขนาดเล็ก (SMR)'
];

const KIND = {
  provision: { label: 'ตัวบท', icon: BookOpen, chip: 'bg-amber-100 text-amber-900 border-amber-300' },
  appeal: { label: 'งานอุทธรณ์', icon: Timer, chip: 'bg-orange-50 text-orange-900 border-orange-200' },
  court_case: { label: 'งานคดี', icon: Gavel, chip: 'bg-indigo-50 text-indigo-900 border-indigo-200' }
};

export default function AssistantPage({ me, go, enabled, llm }) {
  const [turns, setTurns] = useState([]);
  const [input, setInput] = useState('');
  const [asOf, setAsOf] = useState(todayIso());
  const [busy, setBusy] = useState(false);
  const bottom = useRef(null);
  const abort = useRef(null);

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }), [turns]);

  const ask = async (question) => {
    if (!question.trim() || busy) return;
    setBusy(true);
    setInput('');
    const idx = turns.length;
    setTurns((t) => [...t, { question, asOf, sources: [], sentences: [], done: null, error: null }]);
    const patch = (fn) => setTurns((t) => t.map((x, i) => (i === idx ? fn(x) : x)));
    abort.current = new AbortController();
    try {
      await askAssistant(
        question,
        asOf,
        (name, data) => {
          if (name === 'sources') patch((x) => ({ ...x, sources: data.sources }));
          if (name === 'sentence') patch((x) => ({ ...x, sentences: [...x.sentences, data] }));
          if (name === 'done') patch((x) => ({ ...x, done: data }));
        },
        abort.current.signal
      );
    } catch (e) {
      if (e.name !== 'AbortError') patch((x) => ({ ...x, error: e }));
    } finally {
      setBusy(false);
    }
  };

  const openSource = (s) => {
    if (s.kind === 'provision') go('law', { eli: s.eli_id, asOf: s.valid_from > asOf ? s.valid_from : asOf });
    else if (s.kind === 'appeal') go('appeal', { id: s.ref_id });
    else go('court', { id: s.ref_id });
  };

  const scope =
    me.role === 'PUB' ? (
      <>
        บัญชีของคุณค้นได้เฉพาะ <strong>ตัวบทกฎหมายที่เผยแพร่</strong> ระบบจะไม่นำสำนวนอุทธรณ์หรือคดีมาใช้ตอบ
      </>
    ) : (
      <>
        ระบบดึงข้อมูลเฉพาะจาก <strong>ตัวบทกฎหมาย</strong>, <strong>สำนวนงานอุทธรณ์</strong> และ <strong>สำนวนงานคดี</strong> ที่บัญชีของคุณมีสิทธิ์เข้าถึง
        โดยกรองสิทธิ์ก่อนค้นหา ทุกประโยคในคำตอบต้องอ้างแหล่งที่มา ประโยคที่อ้างไม่ได้จะถูกตัดออกก่อนแสดง
      </>
    );

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
      <div className="lg:col-span-4 space-y-4">
        <Card className="p-4">
          <h3 className="text-xs font-bold text-slate-700 tracking-wider mb-3 flex items-center gap-1.5">
            <Sparkles className="w-4 h-4 text-amber-600" />
            <span>ประเด็นค้นคว้าเชื่อมโยง (กฎหมาย + อุทธรณ์ + คดี)</span>
          </h3>
          <div className="space-y-2">
            {PRESETS.map((p, i) => (
              <button
                key={i}
                disabled={busy || !enabled}
                onClick={() => ask(p)}
                className="w-full text-left p-3 rounded-xl border text-xs bg-slate-50 border-slate-200 text-slate-700 hover:bg-amber-50 hover:border-amber-300 disabled:opacity-50"
              >
                {i + 1}. {p}
              </button>
            ))}
          </div>
        </Card>
        <Card className="p-4 text-xs space-y-2 text-slate-600">
          <div className="font-bold text-slate-900 flex items-center gap-1.5">
            <Shield className="w-4 h-4 text-emerald-600" />
            <span>ขอบเขตการประมวลผลข้อมูลตามสิทธิ์</span>
          </div>
          <p className="leading-relaxed">{scope}</p>
        </Card>
        <Card className="p-4 text-xs space-y-2 text-slate-600">
          <label className="font-bold text-slate-900 flex items-center gap-1.5">
            <Calendar className="w-4 h-4 text-amber-600" />
            <span>ตอบตามตัวบทที่มีผล ณ วันที่</span>
          </label>
          <input
            type="date"
            value={asOf}
            onChange={(e) => e.target.value && setAsOf(e.target.value)}
            className="w-full px-3 py-1.5 text-sm border border-slate-300 rounded-lg"
          />
          <p className="text-[11px]">ใช้วันเกิดเหตุของเรื่อง เพื่อให้ผู้ช่วยอ้างตัวบทรุ่นที่ถูกต้อง</p>
        </Card>
      </div>

      <div className="lg:col-span-8">
        <Card className="flex flex-col min-h-[560px]">
          <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-amber-500/15 text-amber-600 flex items-center justify-center">
                <Sparkles className="w-4 h-4" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-slate-900">ระบบช่วยค้นคว้าและวิเคราะห์ข้อกฎหมายอัจฉริยะ</h2>
                <p className="text-[11px] text-slate-500">
                  {llm?.provider === 'extractive'
                    ? 'โหมดสาธิตแบบไม่ใช้ LLM: ยกข้อความจากแหล่งที่ค้นพบมาแสดงพร้อมอ้างอิง (ตั้ง LLM_PROVIDER เพื่อให้เรียบเรียงคำตอบ)'
                    : `สังเคราะห์คำตอบข้ามฐานข้อมูลกฎหมาย งานอุทธรณ์ และงานคดี${llm?.model ? ` · โมเดล ${llm.model}` : ''}`}
                </p>
              </div>
            </div>
            <span
              className={`px-2.5 py-1 rounded-full text-[11px] font-semibold border ${
                enabled ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-rose-50 text-rose-700 border-rose-200'
              }`}
            >
              {enabled ? 'พร้อมให้บริการ' : 'ปิดบริการชั่วคราว'}
            </span>
          </div>

          {!enabled ? (
            <div className="flex-1 p-8 flex flex-col items-center justify-center text-center">
              <Power className="w-10 h-10 text-rose-500 mb-3" />
              <h3 className="text-base font-bold text-slate-900 mb-1">ระบบช่วยค้นคว้าถูกระงับการใช้งานชั่วคราวโดยผู้ดูแลระบบ</h3>
              <p className="text-xs text-slate-600 max-w-md mb-5">เมนูสืบค้นกฎหมาย งานอุทธรณ์ และงานคดี ยังใช้งานได้ตามปกติ</p>
              <button onClick={() => go('law')} className="px-4 py-2 rounded-lg text-xs font-semibold bg-slate-900 text-white">
                ไปที่หน้าสืบค้นกฎหมาย
              </button>
            </div>
          ) : (
            <>
              <div className="flex-1 p-5 space-y-6 overflow-y-auto max-h-[70vh]">
                {turns.length === 0 && (
                  <div className="text-center text-xs text-slate-500 py-16">เลือกประเด็นทางซ้าย หรือพิมพ์คำถามด้านล่าง</div>
                )}
                {turns.map((t, i) => (
                  <Turn key={i} t={t} onOpen={openSource} />
                ))}
                <div ref={bottom} />
              </div>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  ask(input);
                }}
                className="p-4 border-t border-slate-200 flex gap-2"
              >
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="พิมพ์คำถามข้อกฎหมาย แนวคำวินิจฉัยอุทธรณ์ หรือคำพิพากษา (เช่น มาตรา 75, อธ. 14/2562)"
                  className="flex-1 px-4 py-2.5 text-sm border border-slate-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-amber-500"
                />
                <button disabled={busy || !input.trim()} className="px-5 py-2.5 rounded-xl text-sm font-bold bg-slate-900 text-white hover:bg-slate-800 disabled:opacity-50">
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : 'ค้นคว้าและสรุปคำตอบ'}
                </button>
              </form>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}

function Turn({ t, onOpen }) {
  const byKey = Object.fromEntries(t.sources.map((s) => [s.key, s]));
  const status = t.done?.status;
  const used = new Set(t.done?.used_sources || t.sentences.flatMap((s) => s.citations));
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="bg-slate-900 text-white px-4 py-3 rounded-2xl rounded-tr-xs max-w-xl text-xs sm:text-sm">
          {t.question}
          <div className="text-[10px] text-slate-400 mt-1">ตัวบท ณ วันที่ {thDate(t.asOf)}</div>
        </div>
      </div>

      <div className="bg-slate-50 border border-slate-200 rounded-2xl rounded-tl-xs p-5 space-y-4">
        {t.error && (
          <div className="flex items-center gap-2 text-xs text-rose-800">
            <AlertTriangle className="w-4 h-4" /> {t.error.message}
          </div>
        )}
        {!t.done && !t.error && t.sentences.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Loader2 className="w-4 h-4 animate-spin" />
            {t.sources.length ? `พบแหล่งอ้างอิง ${t.sources.length} รายการ กำลังเรียบเรียงคำตอบ…` : 'กำลังค้นหาตามสิทธิ์ของคุณ…'}
          </div>
        )}

        {(status === 'NO_EVIDENCE' || status === 'MODEL_UNAVAILABLE' || status === 'SERVICE_DISABLED') && (
          <div className="flex items-start gap-2.5 text-xs sm:text-sm text-slate-700">
            <SearchX className="w-5 h-5 text-slate-400 shrink-0" />
            <div>
              <p className="font-semibold text-slate-900">
                {status === 'NO_EVIDENCE' && 'ไม่พบหลักฐานในฐานข้อมูลที่ตอบคำถามนี้ได้'}
                {status === 'MODEL_UNAVAILABLE' && 'โมเดลภาษาไม่พร้อมใช้งานในขณะนี้'}
                {status === 'SERVICE_DISABLED' && 'ระบบช่วยค้นคว้าปิดให้บริการชั่วคราว'}
              </p>
              <p className="text-xs text-slate-500 mt-1">
                ระบบไม่สร้างคำตอบเมื่อไม่มีแหล่งอ้างอิงรองรับ กรุณาระบุเลขมาตรา ชื่อระเบียบ เลขรับคำอุทธรณ์ หรือเลขคดีให้ชัดเจนขึ้น
              </p>
            </div>
          </div>
        )}

        {t.sentences.length > 0 && (
          <div className="text-xs sm:text-sm text-slate-800 leading-relaxed space-y-2.5">
            {t.sentences.map((s, i) => (
              <p key={i}>
                {s.text}{' '}
                {s.citations.map((k) => {
                  const src = byKey[k];
                  const meta = KIND[src?.kind] || KIND.provision;
                  return (
                    <button
                      key={k}
                      onClick={() => src && onOpen(src)}
                      title={src?.title}
                      className={`inline-flex items-center px-1.5 py-0 mx-0.5 rounded border text-[11px] font-semibold align-baseline ${meta.chip}`}
                    >
                      {k}
                    </button>
                  );
                })}
              </p>
            ))}
          </div>
        )}

        {t.sources.length > 0 && status === 'ANSWERED' && (
          <div className="pt-3 border-t border-slate-200">
            <div className="text-[11px] font-bold text-slate-600 mb-2">แหล่งอ้างอิง</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {t.sources
                .filter((s) => used.has(s.key))
                .map((s) => {
                  const meta = KIND[s.kind];
                  const Icon = meta.icon;
                  return (
                    <button
                      key={s.key}
                      onClick={() => onOpen(s)}
                      className="text-left p-2.5 rounded-lg bg-white border border-slate-200 hover:border-amber-400 flex gap-2"
                    >
                      <span className={`shrink-0 h-5 px-1.5 rounded border text-[11px] font-bold ${meta.chip}`}>{s.key}</span>
                      <span className="min-w-0">
                        <span className="flex items-center gap-1 text-[11px] font-bold text-slate-900">
                          <Icon className="w-3 h-3" /> <span className="truncate">{s.title}</span>
                          {s.classification === 'restricted' && <Lock className="w-3 h-3 text-rose-600 shrink-0" />}
                        </span>
                        {s.kind === 'provision' && (
                          <span className="block text-[10px] text-slate-500">
                            มีผล {thDate(s.valid_from)} – {s.valid_to ? thDate(s.valid_to) : 'ปัจจุบัน'}
                          </span>
                        )}
                        <span className="block text-[11px] text-slate-500 line-clamp-2">{s.snippet}</span>
                      </span>
                    </button>
                  );
                })}
            </div>
          </div>
        )}

        {t.done && (
          <div className="space-y-1.5">
            {t.done.restricted_omitted > 0 && (
              <div className="flex items-center gap-1.5 text-[11px] text-slate-600">
                <Lock className="w-3.5 h-3.5 text-slate-400" />
                มีอีก {t.done.restricted_omitted} เรื่องที่เกี่ยวข้องแต่อยู่นอกสิทธิ์ของคุณ จึงไม่นำมาใช้ตอบ
              </div>
            )}
            {t.done.dropped_sentences > 0 && (
              <div className="text-[11px] text-slate-500">ตัดประโยคที่อ้างอิงไม่ได้ออก {t.done.dropped_sentences} ประโยค</div>
            )}
            <p className="text-[11px] text-slate-400 pt-1 border-t border-slate-200">{t.done.disclaimer}</p>
          </div>
        )}
      </div>
    </div>
  );
}
