# นำเข้าไฟล์กฎหมายลำดับรองจากฝ่ายกฎหมาย (Google Drive zip)

โฟลเดอร์ใน Drive ตั้งชื่อตามเลขทะเบียน: `01.1-01.52/1.12.1/…` = ทะเบียน 1.12.1, `23/…` = ฉบับหลักของกลุ่ม 23,
`23/2. ชื่อ/…` = แถวที่ 2 ของกลุ่ม 23 (กลุ่มที่ไม่มีไฟล์ฉบับหลักใช้เลขนำ 1., 2., … แทนแถวที่ 1, 2, …)

```bash
export LEGAL_IMPORT_WORK=/tmp/legal-work         # นอก git: มีข้อมูลทะเบียน (ข้อมูลภายใน)
export LEGAL_IMPORT_DSN="host=localhost port=5432 user=postgres password=postgres dbname=erc"
python tools/legal_import/extract_zip.py drive-1.zip $LEGAL_IMPORT_WORK/d1     # ถอดชื่อไทย เก็บไฟล์ตาม sha256
python tools/legal_import/inventory.py $LEGAL_IMPORT_WORK/d1 $LEGAL_IMPORT_WORK/d2 …   # จับคู่กับทะเบียน + ดึงข้อความ
python tools/legal_import/ocr.py                                                # PDF สแกน/ข้อความเพี้ยน → $LEGAL_IMPORT_WORK/ocr (Tesseract ในเครื่อง)
python tools/legal_import/convert_bulk.py                                       # → app/seed/laws/legal-team/*.json
python -m app.seed.raw_files zip drive-1.zip … --class public --from "ฝ่ายกฎหมาย"  # เก็บไฟล์ต้นฉบับ
python -m app.seed.seed && python -m eval.run_eval --inproc --baseline eval/baseline.json
```

ขอบเขตชุดที่ 1 (convert_bulk.py): ใช้บังคับ + มีข้อความ (Word หรือ PDF มีชั้นข้อความ) + ยังไม่มีตัวบท + เอกสารเดียวในแถว
- ใช้ Word เป็นหลักเมื่อมีทั้ง Word และ PDF; PDF ฟอนต์เก่าแปลงอักขระ Private Use (U+F700–F71A) กลับเป็นวรรณยุกต์
- แบ่งเป็นข้อ/มาตรา ไม่แยกข้อที่อยู่ในเครื่องหมายคำพูด (ข้อความที่ฉบับแก้ไขยกมา); เอกสารแนบท้ายเก็บเป็นบทบัญญัติ "แนบท้าย"
- ฉบับแก้ไขเพิ่มเติมนำเข้าเป็นตราสารแยก — ฉบับหลักแสดงคำเตือน "ยังไม่ได้ประกอบเข้าตัวบท" จนกว่าจะประกอบ
- ข้อสังเกตรายฉบับ (ถอดจาก PDF, เลขข้อไม่ต่อเนื่อง, ไม่มีวันใช้บังคับในทะเบียน) → ประเด็นรอตรวจ (`import_note`)

OCR (ocr.py) — ชุดที่ 2: PDF สแกน 86 ไฟล์ (1,081 หน้า) ของทะเบียนที่ใช้บังคับ → นำเข้าได้ 83 ฉบับ
- Tesseract `tha+eng` ที่ 300 dpi ในเครื่อง (ไม่ส่งไฟล์ออกนอกระบบ); รักษาการเยื้องบรรทัดจาก TSV เพื่อแยกย่อหน้า/ข้อ; ตัดบรรทัดขยะ
  (สระบน/วรรณยุกต์ที่ OCR แยกบรรทัด ตราประทับ)
- แก้เฉพาะรูปที่ไม่ใช่คำ เช่น ซือ→ซื้อ เรือง→เรื่อง ทัง→ทั้ง หรือมีบริบทกำกับ (ยืนข้อเสนอ→ยื่นข้อเสนอ) — ปรับรายการแล้ว `ocr.py --reclean`
- เลขข้อที่ OCR อ่านผิด (๔↔๕↔๖) กำหนดตามลำดับ และแจ้งใน import_note; ทุกฉบับมีประเด็นรอตรวจ "ถอดจากภาพสแกนด้วย OCR"
- `--engine endpoint` ใช้โมเดล OCR แบบ OpenAI-compatible vision (เช่น Typhoon OCR บน vLLM ภายใน); endpoint ภายนอกต้องใส่
  `--allow-external` และไม่ส่งระเบียบบริหารงานภายในเด็ดขาด

ตรรกะการแปลง (แยกข้อ/มาตรา, แก้อักขระ, OCR) อยู่ใน `app/law/docparse.py` และ `app/law/ocr.py` — ใช้ร่วมกับหน้า
"นำเข้าเอกสาร" บนเว็บ (`app/law/uploads.py`) ซึ่งให้นิติกร/ผู้ดูแลอัปโหลดทีละไฟล์ → ร่าง → อนุมัติโดยผู้อื่น
