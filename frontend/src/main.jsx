import { createRoot } from 'react-dom/client';
import App from './App.jsx';
// ฟอนต์เก็บในระบบเอง (ไม่โหลดจาก Google Fonts — ไม่ส่ง IP ผู้ใช้ออกนอกระบบ และใช้ได้ในเครือข่ายปิด)
import '@fontsource/ibm-plex-sans-thai/400.css';
import '@fontsource/ibm-plex-sans-thai/500.css';
import '@fontsource/ibm-plex-sans-thai/600.css';
import '@fontsource/ibm-plex-sans-thai/700.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import './index.css';

createRoot(document.getElementById('root')).render(<App />);
