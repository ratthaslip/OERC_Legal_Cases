#!/bin/sh
# ปรับโครงสร้างฐานข้อมูลให้เป็นรุ่นล่าสุดก่อนเปิดบริการ (ปิดได้ด้วย MIGRATE_ON_START=0)
set -e
if [ "${MIGRATE_ON_START:-1}" = "1" ] && [ "$1" = "uvicorn" ]; then
    alembic upgrade head
fi
exec "$@"
