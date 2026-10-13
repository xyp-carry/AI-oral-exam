from mysql_config import get_mysql_config


LOCAL_MYSQL_CONFIG = get_mysql_config(
    database_env="MYSQL_EXAM_DATABASE", default_database="ai_oral_exam"
)
