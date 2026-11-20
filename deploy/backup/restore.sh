#!/usr/bin/env bash
# กู้คืนจากไฟล์สำรองของ backup.sh — แทนที่ฐานข้อมูลและไฟล์ทั้งหมดด้วยข้อมูลในไฟล์สำรอง
#
#   sudo BACKUP_PASSPHRASE_FILE=/root/erc-backup.pass deploy/backup/restore.sh backups/erc-backup-YYYYmmdd-HHMMSS.tar.enc [--yes]
#
# - ตรวจ checksum ทั้งไฟล์และทุกส่วนก่อนแตะข้อมูลจริง
# - ไฟล์ปัจจุบันถูกย้ายไป data/<ชื่อ>.before-restore-<เวลา> (ไม่ลบ)
# - .env ในไฟล์สำรองถูกวางเป็น .env.from-backup (เทียบ CONFIG_SECRET เอง — key ที่เข้ารหัสในฐานต้องใช้ค่าเดิม)
# - หลังกู้คืน ตรวจจำนวนข้อมูลและ hash ล่าสุดของ audit log ให้ตรงกับตอนสำรอง ไม่ตรง = exit 1
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f "${COMPOSE_FILE:-docker-compose.prod.yml}")
file="${1:?ระบุไฟล์สำรอง}"
PASS="${BACKUP_PASSPHRASE_FILE:?ตั้ง BACKUP_PASSPHRASE_FILE}"

if [ -f "$file.sha256" ]; then
  ( cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" ) || { echo "checksum ไฟล์สำรองไม่ตรง" >&2; exit 1; }
fi
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$PASS" -in "$file" | tar -C "$work" -xf -
( cd "$work" && sha256sum -c --quiet SHA256SUMS ) || { echo "checksum ส่วนประกอบไม่ตรง" >&2; exit 1; }

echo "ไฟล์สำรอง:"; sed 's/^/  /' "$work/MANIFEST"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [print(f"  {k:<20}{v:>8}") for k,v in d["counts"].items()]' "$work/check.json"
if [ "${2:-}" != "--yes" ]; then
  read -r -p "แทนที่ฐานข้อมูลและไฟล์ปัจจุบันด้วยชุดนี้? พิมพ์ RESTORE เพื่อยืนยัน: " ans
  [ "$ans" = "RESTORE" ] || { echo "ยกเลิก"; exit 1; }
fi

ts="$(date +%Y%m%d-%H%M%S)"
echo "[1/4] หยุดหน้าเว็บและ API"
"${COMPOSE[@]}" stop web api

echo "[2/4] ฐานข้อมูล"
"${COMPOSE[@]}" up -d --wait db
"${COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "DROP DATABASE IF EXISTS erc WITH (FORCE)" -c "CREATE DATABASE erc"
"${COMPOSE[@]}" exec -T db pg_restore -U postgres -d erc --no-owner --exit-on-error < "$work/db.dump"

echo "[3/4] ไฟล์"
mkdir -p data
for d in raw audit laws-internal; do
  [ -e "data/$d" ] && mv "data/$d" "data/$d.before-restore-$ts"
done
tar -C data -xzf "$work/files.tar.gz"
for d in raw audit; do [ -d "data/$d" ] && chown -R 10001:10001 "data/$d"; done
cp "$work/env" .env.from-backup && chmod 600 .env.from-backup
if ! cmp -s <(grep '^CONFIG_SECRET=' .env || true) <(grep '^CONFIG_SECRET=' .env.from-backup || true); then
  echo "คำเตือน: CONFIG_SECRET ใน .env ต่างจากตอนสำรอง — API key ที่บันทึกจากหน้าเว็บจะถอดรหัสไม่ได้ (ดู .env.from-backup)" >&2
fi

echo "[4/4] เปิดบริการและตรวจผล"
"${COMPOSE[@]}" up -d --wait
seq="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["audit_last_seq"] or 0)' "$work/check.json")"
"${COMPOSE[@]}" exec -T api python -m app.ops check --json --audit-seq "$seq" > "$work/after.json"
python3 - "$work/check.json" "$work/after.json" <<'PY'
import json, sys
b, a = (json.load(open(p)) for p in sys.argv[1:3])
bad = [k for k in b["counts"] if b["counts"][k] != a["counts"].get(k)]
# การกู้คืนเองเพิ่ม audit event ได้ (เช่นเปิดบริการ) จึงเทียบว่า hash ล่าสุดตอนสำรองยังอยู่ในสาย และสายยังถูกต้อง
same_tip = b["audit_last_hash"] is None or a.get("audit_hash_at_seq") == b["audit_last_hash"]
ok = a["audit_chain_valid"] and same_tip and not [k for k in bad if k != "audit_event"]
if not same_tip:
    print("  audit log: hash ล่าสุดตอนสำรองไม่อยู่ในสายหลังกู้คืน")
for k in bad:
    print(f"  {k}: ตอนสำรอง {b['counts'][k]} / หลังกู้คืน {a['counts'].get(k)}")
print("กู้คืนสำเร็จ — ข้อมูลตรงกับตอนสำรอง" if ok else "กู้คืนแล้วแต่ข้อมูลไม่ตรง — ตรวจสอบก่อนเปิดให้ผู้ใช้")
sys.exit(0 if ok else 1)
PY
