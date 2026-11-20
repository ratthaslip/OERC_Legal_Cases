# ส่วนร่วมของ server block (HTTP 8080 และ HTTPS 8443) — หน้าเว็บ static + reverse proxy ไป API
root /usr/share/nginx/html;
server_tokens off;
client_max_body_size 60m;   # อัปโหลดเอกสารได้ถึง 50 MB (API ตรวจซ้ำ)

add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
# style 'unsafe-inline': React ใช้ style attribute (เช่นความกว้างแถบความคืบหน้า); สคริปต์มาจากระบบเท่านั้น
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; object-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;

location = /api/v1/assistant/answers {
    limit_req zone=assistant burst=5 nodelay;   # ผู้ช่วย AI: จำกัดคำถามต่อ IP
    limit_req_status 429;
    proxy_pass http://api:8000;
    include /etc/nginx/snippets/proxy.inc;
    proxy_buffering off;                        # SSE: ส่งทีละประโยค
    proxy_read_timeout 300s;
}

location /api/ {
    limit_req zone=api burst=40 nodelay;
    limit_req_status 429;
    proxy_pass http://api:8000;
    include /etc/nginx/snippets/proxy.inc;
    proxy_read_timeout 120s;
}

# OpenAPI ปิดในระบบจริง (APP_ENV=production) — กันไว้อีกชั้น
location ~ ^/(docs|redoc|openapi\.json) { return 404; }

location /assets/ {
    expires 1y;            # ชื่อไฟล์มี hash — แคชได้นาน
    try_files $uri =404;
}

location / {
    expires -1;            # index.html: ตรวจรุ่นใหม่ทุกครั้ง
    try_files $uri /index.html;
}
