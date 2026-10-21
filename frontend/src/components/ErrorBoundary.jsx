import React from 'react';
import { AlertTriangle } from 'lucide-react';

/**
 * กันไม่ให้ error ระหว่าง render ของหน้าใดหน้าหนึ่งทำให้ทั้งแอปว่างเปล่า
 * แสดงข้อความ error ให้ผู้ใช้/ผู้พัฒนาเห็น แทนการ unmount ทั้ง React tree
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('[ERC] render error', error, info?.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="bg-white border border-rose-200 rounded-2xl p-6 space-y-3">
        <div className="flex items-center gap-2 text-rose-700 font-bold text-sm">
          <AlertTriangle className="w-5 h-5" />
          หน้านี้แสดงผลไม่สำเร็จ
        </div>
        <p className="text-xs text-slate-600">
          ส่วนอื่นของระบบยังใช้งานได้ตามปกติ กรุณาส่งข้อความด้านล่างให้ผู้พัฒนาเพื่อตรวจสอบ
        </p>
        <pre className="text-[11px] bg-slate-50 border border-slate-200 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap text-slate-700">
          {String(error?.message || error)}
        </pre>
        <button
          onClick={() => this.setState({ error: null })}
          className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 text-white"
        >
          ลองใหม่
        </button>
      </div>
    );
  }
}
