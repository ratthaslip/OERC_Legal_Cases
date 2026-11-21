import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CaseForm from './CaseForm.jsx';
import { api } from '../../api.js';

vi.mock('../../api.js', () => ({ api: { createCase: vi.fn(), updateCase: vi.fn() } }));

const opts = {
  court_levels: ['ศาลปกครองชั้นต้น', 'ศาลปกครองสูงสุด'], courts: ['ศาลปกครองกลาง'], sectors: ['ไฟฟ้า'],
  statuses: { PENDING: 'อยู่ระหว่างพิจารณา', FINAL: 'ถึงที่สุด' }, classes: { internal: 'ใช้ภายใน', restricted: 'จำกัดเฉพาะผู้รับผิดชอบ' },
  officers: [{ user_id: 'lgl1', name: 'นิติกร ก.' }], can_assign: false, public_data_only: false
};
const field = (label) => screen.getByLabelText(label, { exact: false });

beforeEach(() => vi.clearAllMocks());

describe('CaseForm', () => {
  it('ส่งช่องที่เว้นว่างเป็น null และไม่ส่งผู้รับผิดชอบเมื่อไม่ใช่ ผอ.คบ.', async () => {
    api.createCase.mockResolvedValue({ case_id: 12 });
    const onSaved = vi.fn();
    render(<CaseForm opts={opts} initial={null} onSaved={onSaved} onCancel={() => {}} />);
    fireEvent.change(field('เลขคดีดำ'), { target: { value: 'คบ. 9/2569' } });
    fireEvent.change(field('เรื่อง (ประเด็นพิพาท)'), { target: { value: 'คดีทดสอบ' } });
    fireEvent.change(field('รหัสคู่กรณี'), { target: { value: 'EL-1' } });
    fireEvent.click(screen.getByRole('button', { name: /บันทึก/ }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(12, undefined));
    const body = api.createCase.mock.calls[0][0];
    expect(body).toMatchObject({ black_no: 'คบ. 9/2569', subject: 'คดีทดสอบ', party_code: 'EL-1', red_no: null, status: 'PENDING' });
    expect(body).not.toHaveProperty('owners');
  });

  it('แก้ไข: ส่ง row_version เดิมไปด้วย', async () => {
    api.updateCase.mockResolvedValue({ case_id: 3, changed: ['result'] });
    const initial = { case_id: 3, row_version: 5, black_no: 'คบ. 1/2569', court_level: 'ศาลปกครองชั้นต้น', court_name: 'ศาลปกครองกลาง',
      subject: 'เรื่องเดิม', party_code: 'EL-2', energy_sector: 'ไฟฟ้า', status: 'PENDING', classification: 'internal', owners: ['lgl1'] };
    render(<CaseForm opts={opts} initial={initial} onSaved={() => {}} onCancel={() => {}} />);
    fireEvent.change(field('ผลคดี'), { target: { value: 'ยกฟ้อง' } });
    fireEvent.click(screen.getByRole('button', { name: /บันทึก/ }));
    await waitFor(() => expect(api.updateCase).toHaveBeenCalled());
    expect(api.updateCase.mock.calls[0][0]).toBe(3);
    expect(api.updateCase.mock.calls[0][1]).toMatchObject({ row_version: 5, result: 'ยกฟ้อง' });
  });

  it('เครื่องที่เก็บเฉพาะข้อมูลสาธารณะ → เตือนห้ามใส่คดีจริง', () => {
    render(<CaseForm opts={{ ...opts, public_data_only: true }} initial={null} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/ห้ามใส่ข้อมูลคดีจริง/)).toBeInTheDocument();
  });
});
