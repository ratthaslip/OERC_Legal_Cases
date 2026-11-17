/** @type {import('tailwindcss').Config} */
// ธีม B "หน่วยงานกำกับพลังงานสมัยใหม่": น้ำเงินหลัก (brand) + ส้มพลังงานเป็นสีเน้น
// สีส้มแทนชุด amber เดิมทั้งระบบ เพื่อให้สีเน้นทุกจุดเปลี่ยนตามธีมโดยไม่ต้องแก้ทีละหน้า
// ค่าสีเป็นค่าเสนอ — ปรับตามคู่มืออัตลักษณ์ของสำนักงาน กกพ. ได้ที่ไฟล์นี้ไฟล์เดียว
const brand = {
  50: '#eef5ff', 100: '#d9e8fd', 200: '#b5d2fa', 300: '#82b3f4', 400: '#4a8fe9',
  500: '#1f74dc', 600: '#0b5fbf', 700: '#0a4e9f', 800: '#0b4282', 900: '#0d376a', 950: '#0a2346'
};
const energy = {
  50: '#fff5eb', 100: '#ffe6cc', 200: '#ffcb99', 300: '#ffad63', 400: '#ff9a3d',
  500: '#ff8a1f', 600: '#e96f00', 700: '#bf5a00', 800: '#984803', 900: '#7a3b07', 950: '#421d01'
};
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: { brand, amber: energy, energy, canvas: '#f3f6fb' },
      fontFamily: { sans: ['"IBM Plex Sans Thai"', 'system-ui', 'sans-serif'], mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'] },
      boxShadow: { xs: '0 1px 2px rgba(11,95,191,.06)', card: '0 4px 14px rgba(11,95,191,.07)' },
      borderRadius: { xs: '2px' }
    }
  },
  plugins: []
};
