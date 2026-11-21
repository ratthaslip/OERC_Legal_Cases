import { describe, expect, it } from 'vitest';
import { thDate } from './ui.jsx';

describe('thDate', () => {
  it('แสดงวันที่แบบไทย ปี พ.ศ.', () => {
    expect(thDate('2026-01-30')).toBe('30 ม.ค. 2569');
    expect(thDate('2025-12-31T23:00:00Z')).toBe('31 ธ.ค. 2568');
  });
  it('ไม่มีวันที่ → ขีด', () => {
    expect(thDate(null)).toBe('-');
    expect(thDate('')).toBe('-');
  });
});
