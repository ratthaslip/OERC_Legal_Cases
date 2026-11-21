#!/usr/bin/env bash
# สำรองข้อมูลระบบจริง: ฐานข้อมูล (pg_dump) + คลังไฟล์ต้นฉบับ + สำเนา audit log + ตราสารภายใน + .env
# รวมเป็นไฟล์เดียว เข้ารหัส AES-256 และลบชุดที่เก่ากว่า BACKUP_KEEP_DAYS
#
#   sudo BACKUP_PASSPHRASE_FILE=/root/erc-backup.pass deploy/backup/backup.sh
#
# ตัวแปร: BACKUP_DIR (./backups — ควรเป็นที่เก็บสำรองของสำนักงาน เช่น NFS), BACKUP_KEEP_DAYS (30),
#         BACKUP_PASSPHRASE_FILE (บังคับ — เก็บไว้นอกเครื่องด้วย ไม่มีไฟล์นี้กู้คืนไม่ได้),
#         COMPOSE_FILE (docker-compose.prod.yml), PG_DUMP_URL (ฐานข้อมูลแยกเครื่อง: dump ผ่าน URL แทนบริการ db)
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f "${COMPOSE_FILE:-docker-compose.prod.yml}")
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP="${BACKUP_KEEP_DAYS:-30}"
PASS="${BACKUP_PASSPHRASE_FILE:?ตั้ง BACKUP_PASSPHRASE_FILE (ไฟล์รหัสผ่านสำหรับเข้ารหัสไฟล์สำรอง)}"
[ -s "$PASS" ] || { echo "ไม่พบไฟล์รหัสผ่าน $PASS" >&2; exit 1; }

ts="$(date +%Y%m%d-%H%M%S)"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
mkdir -p "$BACKUP_DIR"

echo "[1/4] สถานะก่อนสำรอง"
"${COMPOSE[@]}" exec -T api python -m app.ops check --json > "$work/check.json"

echo "[2/4] ฐานข้อมูล (pg_dump)"
if [ -n "${PG_DUMP_URL:-}" ]; then
  docker run --rm --network host pgvector/pgvector:pg16 pg_dump -Fc "$PG_DUMP_URL" > "$work/db.dump"
else
  "${COMPOSE[@]}" exec -T db pg_dump -U postgres -Fc erc > "$work/db.dump"
fi
[ -s "$work/db.dump" ] || { echo "pg_dump ว่าง" >&2; exit 1; }

echo "[3/4] ไฟล์ (คลังต้นฉบับ, audit, ตราสารภายใน, .env)"
parts=()
for d in raw audit laws-internal; do [ -d "data/$d" ] && parts+=("$d"); done
tar -C data -czf "$work/files.tar.gz" "${parts[@]}"
cp .env "$work/env"
( cd "$work" && sha256sum db.dump files.tar.gz env check.json > SHA256SUMS )
cat > "$work/MANIFEST" <<MAN
created=$ts
host=$(hostname)
git=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
parts=${parts[*]}
MAN

echo "[4/4] เข้ารหัสและเก็บ"
out="$BACKUP_DIR/erc-backup-$ts.tar.enc"
tar -C "$work" -cf - MANIFEST SHA256SUMS check.json db.dump files.tar.gz env \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$PASS" -out "$out"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256" )
chmod 600 "$out"

find "$BACKUP_DIR" -maxdepth 1 -name 'erc-backup-*.tar.enc*' -mtime +"$KEEP" -print -delete
echo "สำเร็จ: $out ($(du -h "$out" | cut -f1))"
