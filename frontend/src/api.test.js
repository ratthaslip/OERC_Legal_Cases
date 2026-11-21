import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, setApiUser } from './api.js';

const reply = (status, body) => vi.fn().mockResolvedValue({ ok: status < 400, status, json: () => Promise.resolve(body) });

afterEach(() => { vi.unstubAllGlobals(); setApiUser('lgl1'); });

describe('request', () => {
  it('ส่งผู้ใช้ปัจจุบันใน header และตัดพารามิเตอร์ว่างออก', async () => {
    const f = reply(200, { items: [] });
    vi.stubGlobal('fetch', f);
    setApiUser('dir1');
    await api.cases({ q: 'คบ. 1/2568', court_level: '', sector: undefined });
    const [url, opts] = f.mock.calls[0];
    expect(url).toBe('/api/v1/court-cases?q=%E0%B8%84%E0%B8%9A.+1%2F2568');
    expect(opts.headers['X-Demo-User']).toBe('dir1');
    expect(opts.body).toBeUndefined();
  });

  it('ส่ง body เป็น JSON', async () => {
    const f = reply(201, { case_id: 7 });
    vi.stubGlobal('fetch', f);
    expect(await api.createCase({ black_no: 'คบ. 1/2569' })).toEqual({ case_id: 7 });
    const [, opts] = f.mock.calls[0];
    expect(opts.method).toBe('POST');
    expect(opts.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(opts.body)).toEqual({ black_no: 'คบ. 1/2569' });
  });

  it('สถานะผิดพลาด → ApiError พร้อมข้อความจาก backend', async () => {
    vi.stubGlobal('fetch', reply(409, { code: 'DUPLICATE', detail: 'มีคดีนี้แล้ว' }));
    const err = await api.createCase({}).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe('DUPLICATE');
    expect(err.message).toBe('มีคดีนี้แล้ว');
  });
});
