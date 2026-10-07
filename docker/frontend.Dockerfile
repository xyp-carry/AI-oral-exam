FROM node:20-alpine

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ .

EXPOSE 5173

# --host 0.0.0.0 让容器外的浏览器能访问 Vite 开发服务器
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
