FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libsndfile/ffmpeg: librosa 读取音频需要；build-essential: 部分 wheel 需要现场编译；git: 代码仓库考核功能需要
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libsndfile1 \
        ffmpeg \
        git \
        openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，充分利用构建缓存
COPY requirements.txt .
# cryptography: pymysql 连接 MySQL 8 的 caching_sha2_password 认证需要
# email-validator: Authentication 中 pydantic EmailStr 需要
# argon2-cffi: passlib 的 argon2 密码哈希需要
RUN pip install -r requirements.txt \
        cryptography \
        email-validator \
        argon2-cffi

COPY . .

EXPOSE 7860

# main.py 以 HTTPS 方式启动，启动前若无证书则自动生成自签名证书（容器内部生成，无需提交到仓库）
CMD ["sh", "-c", "if [ ! -f key.pem ] || [ ! -f cert.pem ]; then echo 'generating self-signed cert'; openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'; fi; exec python main.py"]
