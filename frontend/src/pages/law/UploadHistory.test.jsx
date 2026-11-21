import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DiffView, WorkflowSteps } from './UploadHistory.jsx';

describe('WorkflowSteps', () => {
  it('ไฮไลต์ขั้นปัจจุบันและติ๊กขั้นที่ผ่านแล้ว', () => {
    render(<WorkflowSteps status="submitted" />);
    expect(screen.getByText('ส่งตรวจ').closest('span')).toHaveClass('bg-brand-600');
    expect(screen.getByText('ตรวจทาน/แก้ร่าง').closest('span')).toHaveClass('bg-emerald-50');
    expect(screen.getByText('อนุมัติและเผยแพร่').closest('span')).toHaveClass('text-slate-400');
  });
  it('ส่งกลับแก้ (rejected) อยู่ขั้นแก้ร่าง', () => {
    render(<WorkflowSteps status="rejected" />);
    expect(screen.getByText('ตรวจทาน/แก้ร่าง').closest('span')).toHaveClass('bg-brand-600');
  });
});

describe('DiffView', () => {
  const diff = {
    fields: [{ field: 'effectiveDate', old: '2026-03-02', new: '2026-05-01' }],
    provisions: [
      { key: 'ข้อ 3', change: 'changed', old: { part: '' }, new: { part: '' }, part_changed: false,
        segments: [{ op: 'eq', text: 'ภายใน' }, { op: 'del', text: 'สามสิบ' }, { op: 'ins', text: 'สิบห้า' }, { op: 'eq', text: 'วัน' }] },
      { key: 'ข้อ 4', change: 'added', new: { part: '' }, segments: [{ op: 'ins', text: 'ให้ประธานรักษาการ' }] }
    ]
  };
  it('แสดงช่องที่แก้ และข้อความที่ลบ/เพิ่มด้วย del/ins', () => {
    const { container } = render(<DiffView diff={diff} />);
    expect(screen.getByText('วันใช้บังคับ')).toBeInTheDocument();
    expect(container.querySelector('del').textContent).toBe('2026-03-02');
    const dels = [...container.querySelectorAll('p del')].map((e) => e.textContent);
    const ins = [...container.querySelectorAll('p ins')].map((e) => e.textContent);
    expect(dels).toEqual(['สามสิบ']);
    expect(ins).toEqual(['สิบห้า', 'ให้ประธานรักษาการ']);
    expect(screen.getByText('เพิ่ม')).toBeInTheDocument();
  });
  it('ไม่มีความต่าง → บอกผู้ใช้', () => {
    render(<DiffView diff={{ fields: [], provisions: [] }} />);
    expect(screen.getByText('ไม่มีความต่างระหว่างสองรุ่นนี้')).toBeInTheDocument();
  });
});
