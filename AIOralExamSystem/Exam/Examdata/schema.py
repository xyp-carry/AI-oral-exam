from .config import LOCAL_MYSQL_CONFIG


def ensure_tables(connection) -> None:
    with connection.cursor() as cursor:
        _create_exam_tables(cursor)
        _create_course_tables(cursor)
        _ensure_exam_sessions_user_id(cursor)
        _ensure_exam_sessions_course_id(cursor)
        _ensure_exam_sessions_exam_item_id(cursor)
        _ensure_exam_sessions_no_candidate_id(cursor)
        _ensure_exam_questions_exam_id_index(cursor)


def _create_exam_tables(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_sessions (
            exam_id CHAR(36) PRIMARY KEY,
            user_id VARCHAR(128),
            course_id VARCHAR(128),
            exam_item_id CHAR(36),
            candidate_info_json JSON,
            total_score DOUBLE,
            dimension_count INT,
            question_count INT,
            dimension_scores_json JSON,
            final_review_json JSON,
            ended_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_exam_sessions_user_id (user_id),
            INDEX idx_exam_sessions_course_id (course_id),
            INDEX idx_exam_sessions_exam_item_id (exam_item_id)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_questions (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            exam_id CHAR(36) NOT NULL,
            record_index INT NOT NULL,
            question_id VARCHAR(128),
            question_content TEXT,
            question_dimension VARCHAR(255),
            question_score DOUBLE,
            based_on_record_index INT,
            source_detail TEXT,
            student_answer TEXT,
            correctness_level VARCHAR(64),
            evaluation TEXT,
            standard_answer TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_exam_questions_exam_id (exam_id),
            INDEX idx_exam_questions_dimension (question_dimension),
            CONSTRAINT fk_exam_questions_exam
                FOREIGN KEY (exam_id)
                REFERENCES exam_sessions(exam_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _create_course_tables(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            course_id CHAR(36) PRIMARY KEY,
            course_name VARCHAR(128) NOT NULL,
            description TEXT DEFAULT NULL,
            owner_teacher_id VARCHAR(128) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE KEY uniq_courses_course_name (course_name),
            INDEX idx_courses_owner_teacher_id (owner_teacher_id),
            INDEX idx_courses_status (status)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS course_teachers (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            course_id CHAR(36) NOT NULL,
            teacher_id VARCHAR(128) NOT NULL,
            teacher_role VARCHAR(20) NOT NULL DEFAULT 'owner',
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at DATETIME NOT NULL,
            UNIQUE KEY uniq_course_teacher (course_id, teacher_id),
            INDEX idx_course_teachers_teacher_id (teacher_id),
            INDEX idx_course_teachers_course_id (course_id),
            CONSTRAINT fk_course_teachers_course
                FOREIGN KEY (course_id)
                REFERENCES courses(course_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    _ensure_courses_course_name_unique_index(cursor)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS course_students (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            course_id CHAR(36) NOT NULL,
            student_id VARCHAR(128) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            joined_at DATETIME NOT NULL,
            UNIQUE KEY uniq_course_student (course_id, student_id),
            INDEX idx_course_students_student_id (student_id),
            INDEX idx_course_students_course_id (course_id),
            CONSTRAINT fk_course_students_course
                FOREIGN KEY (course_id)
                REFERENCES courses(course_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS course_join_requests (
            request_id CHAR(36) PRIMARY KEY,
            course_id CHAR(36) NOT NULL,
            student_id VARCHAR(128) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            requested_at DATETIME NOT NULL,
            reviewed_at DATETIME DEFAULT NULL,
            reviewed_by VARCHAR(128) DEFAULT NULL,
            INDEX idx_course_join_requests_course_id (course_id),
            INDEX idx_course_join_requests_student_id (student_id),
            INDEX idx_course_join_requests_status (status),
            CONSTRAINT fk_course_join_requests_course
                FOREIGN KEY (course_id)
                REFERENCES courses(course_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS course_exam_items (
            exam_item_id CHAR(36) PRIMARY KEY,
            course_id CHAR(36) NOT NULL,
            exam_item_name VARCHAR(128) NOT NULL,
            description TEXT DEFAULT NULL,
            item_type VARCHAR(32) DEFAULT NULL,
            dimension_names_json JSON NOT NULL,
            dimension_scores_json JSON NOT NULL,
            total_score DOUBLE NOT NULL DEFAULT 0,
            participant_count INT NOT NULL DEFAULT 0,
            attempt_count INT NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_by VARCHAR(128) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE KEY uniq_course_exam_item_name (course_id, exam_item_name),
            INDEX idx_course_exam_items_course_id (course_id),
            INDEX idx_course_exam_items_status (status),
            CONSTRAINT fk_course_exam_items_course
                FOREIGN KEY (course_id)
                REFERENCES courses(course_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _ensure_exam_sessions_user_id(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND COLUMN_NAME = 'user_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE exam_sessions ADD COLUMN user_id VARCHAR(128) AFTER exam_id")

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND INDEX_NAME = 'idx_exam_sessions_user_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE exam_sessions ADD INDEX idx_exam_sessions_user_id (user_id)")


def _ensure_courses_course_name_unique_index(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'courses'
          AND INDEX_NAME = 'uniq_courses_course_name'
        """,
        (database,),
    )
    if cursor.fetchone()[0] > 0:
        return

    cursor.execute(
        """
        SELECT course_name
        FROM courses
        GROUP BY course_name
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    duplicate = cursor.fetchone()
    if duplicate is not None:
        raise ValueError(f"duplicate course_name exists: {duplicate[0]}")

    cursor.execute("ALTER TABLE courses ADD UNIQUE KEY uniq_courses_course_name (course_name)")


def _ensure_exam_sessions_course_id(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND COLUMN_NAME = 'course_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE exam_sessions ADD COLUMN course_id VARCHAR(128) AFTER user_id")

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND INDEX_NAME = 'idx_exam_sessions_course_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE exam_sessions ADD INDEX idx_exam_sessions_course_id (course_id)")


def _ensure_exam_sessions_exam_item_id(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND COLUMN_NAME = 'exam_item_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE exam_sessions ADD COLUMN exam_item_id CHAR(36) AFTER course_id")

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND INDEX_NAME = 'idx_exam_sessions_exam_item_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE exam_sessions ADD INDEX idx_exam_sessions_exam_item_id (exam_item_id)")


def _ensure_exam_questions_exam_id_index(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_questions'
          AND INDEX_NAME = 'idx_exam_questions_exam_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE exam_questions ADD INDEX idx_exam_questions_exam_id (exam_id)")


def _ensure_exam_sessions_no_candidate_id(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND COLUMN_NAME = 'candidate_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] > 0:
        cursor.execute("ALTER TABLE exam_sessions DROP COLUMN candidate_id")
