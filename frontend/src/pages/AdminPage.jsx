import { useState } from 'react';
import { Settings } from 'lucide-react';
import { EmbedIndexes, ModelSection } from './AdminModels.jsx';

const TABS = [
  ['llm', 'โมเดลภาษา (LLM)'],
  ['rerank', 'Reranker'],
  ['embed', 'Embedding']
];

/** หน้าตั้งค่าผู้ดูแลระบบ: LLM / reranker / embedding (endpoint / model / API key) */
export default function AdminPage({ notify }) {
  const [tab, setTab] = useState('llm');
  const [embedStamp, setEmbedStamp] = useState('');
  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2">
        <Settings className="w-5 h-5 text-brand-600" />
        <h2 className="text-lg font-bold text-slate-900">ตั้งค่าระบบ — โมเดล</h2>
      </div>
      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-3 py-2 text-sm -mb-px border-b-2 ${tab === k ? 'border-brand-600 text-brand-700 font-semibold' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
            {label}
          </button>
        ))}
      </div>
      {(tab === 'llm' || tab === 'rerank') && <ModelSection key={tab} name={tab} notify={notify} />}
      {tab === 'embed' && (
        <>
          <ModelSection name="embed" notify={notify} onLoaded={(v) => setEmbedStamp(`${v.provider}|${v.base_url}|${v.model}|${v.updated_at}`)} />
          <EmbedIndexes key={embedStamp} notify={notify} />
        </>
      )}
    </div>
  );
}
