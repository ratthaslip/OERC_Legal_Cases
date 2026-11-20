# หน้าเว็บสำหรับระบบจริง: build เป็นไฟล์ static แล้วเสิร์ฟด้วย nginx (ผู้ใช้ไม่ใช่ root, พอร์ต 8080/8443)
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /web/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/app.inc nginx/proxy.inc /etc/nginx/snippets/
EXPOSE 8080 8443
