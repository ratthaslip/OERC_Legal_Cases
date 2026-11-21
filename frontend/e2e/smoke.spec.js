// เส้นทางหลักของผู้ใช้แต่ละบทบาท — ใช้ข้อมูลสาธิตจาก seed (ไม่มีข้อมูลจริง)
import { expect, test } from '@playwright/test';

async function openAs(page, user, pageKey) {
  await page.goto('/');
  await page.evaluate(([u, p]) => { localStorage.setItem('erc.user', u); localStorage.setItem('erc.page', p); }, [user, pageKey]);
  await page.reload();
}

test('ประชาชนค้นกฎหมายได้ แต่ไม่เห็นแท็บนำเข้าเอกสาร', async ({ page }) => {
  await openAs(page, 'pub', 'law');
  await page.getByPlaceholder(/มาตรา 121/).fill('ระยะเวลายื่นอุทธรณ์');
  await expect(page.getByText(/มาตรา 121/).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('button', { name: 'นำเข้าเอกสาร' })).toHaveCount(0);
});

test('นิติกรเห็นแท็บนำเข้าเอกสารภายใต้สืบค้นกฎหมาย', async ({ page }) => {
  await openAs(page, 'lgl1', 'law');
  await page.getByRole('button', { name: 'นำเข้าเอกสาร' }).click();
  await expect(page.getByRole('heading', { name: 'นำเข้าเอกสารกฎหมาย' })).toBeVisible();
  await expect(page.getByText('รายการนำเข้า')).toBeVisible();
});

test('เปิดหน้างานอุทธรณ์และดูรายละเอียดเรื่อง', async ({ page }) => {
  await openAs(page, 'lgl1', 'appeals');
  await expect(page.getByText(/อธ\. \d+\/\d{4}/).first()).toBeVisible({ timeout: 20_000 });
});

test('นิติกรเพิ่มคดี แก้ไข และเพิ่มกระบวนพิจารณา', async ({ page }) => {
  const black = `คบ. ${Date.now() % 100000}/2569`;
  await openAs(page, 'lgl1', 'court');
  await page.getByRole('button', { name: 'เพิ่มคดี' }).click();
  const field = (label) => page.getByLabel(label, { exact: false });
  await field('เลขคดีดำ').fill(black);
  await field('เรื่อง (ประเด็นพิพาท)').fill('คดีทดสอบ E2E เรื่องคำสั่งปรับทางปกครอง');
  await field('รหัสคู่กรณี (นามแฝง)').fill('EL-E2E-01');
  await field('ประเภทกิจการพลังงาน').fill('ไฟฟ้า');
  await page.getByRole('button', { name: 'บันทึก' }).click();
  await expect(page.getByText('บันทึกคดีแล้ว')).toBeVisible();
  await expect(page.getByText('คดีทดสอบ E2E เรื่องคำสั่งปรับทางปกครอง').first()).toBeVisible();

  await page.getByRole('button', { name: 'แก้ไข', exact: true }).click();
  await field('ผลคดี').fill('ยกฟ้อง');
  await page.getByRole('button', { name: 'บันทึก' }).click();
  await expect(page.getByText('บันทึกคดีแล้ว')).toBeVisible();

  await page.getByLabel('วันที่', { exact: true }).fill('2026-06-10');
  await page.getByLabel('รายการ', { exact: true }).fill('สำนักงานยื่นคำให้การ');
  await page.getByRole('button', { name: 'เพิ่ม', exact: true }).click();
  await expect(page.getByText('สำนักงานยื่นคำให้การ').first()).toBeVisible();

  await page.getByText('ประวัติการแก้ไข').click();
  await expect(page.getByText('กระบวนพิจารณา', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('ยกฟ้อง').last()).toBeVisible();
});

test('เจ้าหน้าที่ทั่วไปไม่เห็นปุ่มเพิ่มคดีหรือนำเข้า', async ({ page }) => {
  await openAs(page, 'stf1', 'court');
  await expect(page.getByText(/คดีพิพาท/).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('button', { name: 'เพิ่มคดี' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'นำเข้าจาก Excel' })).toHaveCount(0);
});
