import os

# 默认值用于本机直跑；容器部署时通过 MYSQL_HOST 等环境变量指向 mysql 服务
LOCAL_MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "123456"),
    "database": "ai_oral_exam",
    "charset": "utf8mb4",
}
