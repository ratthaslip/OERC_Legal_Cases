"""ดึงข้อความ พ.ร.บ. จาก PDF ของกฤษฎีกา
- ตัดลายน้ำ (สีเทา) และเลขเชิงอรรถตัวยก
- จัดลำดับอักษรใหม่: ตัวหลักเรียงตามแกน x, สระบน/ล่างและวรรณยุกต์ผูกกับตัวหลักที่อยู่ใต้มัน
- เติมช่องว่างจากระยะห่างระหว่างตัวหลัก, แก้ ํา → ำ"""
import sys, pymupdf
VOWEL = set(chr(c) for c in [0x0E31, *range(0x0E34, 0x0E3B), 0x0E47])
TONE = set(chr(c) for c in range(0x0E48, 0x0E4C))
OTHER = set(chr(c) for c in range(0x0E4C, 0x0E4F))
MARKS = VOWEL | TONE | OTHER
DIGITS = set('๐๑๒๓๔๕๖๗๘๙0123456789')

def mark_rank(ch):
    return 0 if ch in VOWEL else 1 if ch in TONE else 2

d = pymupdf.open(sys.argv[1])
out = []
for pno, page in enumerate(d):
    rows = []
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                if s['color'] != 0: continue
                for c in s['chars']:
                    if c['c'].strip():
                        rows.append((l['bbox'][1], c, s['size']))
    rows.sort(key=lambda r: r[0])
    lines = []
    for y, c, size in rows:
        if lines and abs(lines[-1][0] - y) <= 3: lines[-1][1].append((c, size))
        else: lines.append([y, [(c, size)]])
    for y, chars in lines:
        body = max(sz for _, sz in chars)
        chars = [(c, sz) for c, sz in chars if not (sz < body - 1.5 and c['c'] in DIGITS)]  # ตัวยกเชิงอรรถ
        # รวมเป็นกลุ่มอักษร (ตัวหลัก + เครื่องหมายที่ตามมาในลำดับของไฟล์) แล้วเรียงกลุ่มตามแกน x
        clusters = []
        for c, sz in chars:
            if c['c'] in MARKS and clusters:
                clusters[-1][2].append(c['c'])
            elif c['c'] not in MARKS:
                clusters.append([c, sz, []])
        clusters.sort(key=lambda t: t[0]['bbox'][0])
        bases = [(c, sz) for c, sz, _ in clusters]
        attach = {i: m for i, (_, _, m) in enumerate(clusters)}
        if not bases: continue
        text, prev_end = '', None
        for i, (b, sz) in enumerate(bases):
            if prev_end is not None and b['bbox'][0] - prev_end > 0.2 * sz:
                text += ' '
            text += b['c'] + ''.join(sorted(attach[i], key=mark_rank))
            prev_end = max(prev_end or 0, b['bbox'][2])
        text = ' '.join(text.replace('ํา', 'ำ').split())
        x = min(b['bbox'][0] for b, _ in bases)
        out.append(f"{pno+1}\t{int(x)}\t{body:.1f}\t{text}")
open(sys.argv[2], 'w').write('\n'.join(out))
