from .config import LOCAL_MYSQL_CONFIG


def ensure_tables(connection) -> None:
    with connection.cursor() as cursor:
        _create_exam_tables(cursor)
        _create_course_tables(cursor)
        _ensure_course_join_requests_user_id(cursor)
        _ensure_exam_sessions_user_id(cursor)
        _ensure_exam_sessions_course_id(cursor)
        _ensure_exam_sessions_exam_item_id(cursor)
        _ensure_exam_sessions_extra_fields(cursor)
        _ensure_exam_sessions_ended_at_nullable(cursor)
        _ensure_exam_sessions_unique_user_course_item(cursor)
        _ensure_courses_invite_code_fields(cursor)
        _ensure_course_exam_items_availability_fields(cursor)
        _ensure_course_exam_items_need_code_repository(cursor)
        _ensure_course_exam_items_use_preset_questions(cursor)
        _ensure_exam_preset_questions_extra_fields(cursor)
        _ensure_exam_sessions_no_candidate_id(cursor)
        _ensure_exam_questions_is_preset_question(cursor)
        _ensure_exam_questions_based_on_record_index_type(cursor)
        _ensure_exam_questions_exam_id_index(cursor)


def _create_exam_tables(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_sessions (
            exam_id CHAR(36) PRIMARY KEY,
            user_id VARCHAR(128),
            course_id VARCHAR(128),
            exam_item_id CHAR(36),
            exam_item_name VARCHAR(128) DEFAULT NULL,
            candidate_info_json JSON,
            total_score DOUBLE,
            exam_score DOUBLE DEFAULT NULL,
            dimension_count INT,
            question_count INT,
            dimension_scores_json JSON,
            exam_dimension_scores_json JSON,
            final_review_json JSON,
            repository_url TEXT DEFAULT NULL,
            need_code_repository TINYINT(1) NOT NULL DEFAULT 0,
            use_preset_questions TINYINT(1) NOT NULL DEFAULT 0,
            exam_completed TINYINT(1) NOT NULL DEFAULT 0,
            ended_at DATETIME DEFAULT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_exam_sessions_user_id (user_id),
            INDEX idx_exam_sessions_course_id (course_id),
            INDEX idx_exam_sessions_exam_item_id (exam_item_id),
            UNIQUE KEY uniq_exam_session_user_course_item (user_id, course_id, exam_item_id)
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
            based_on_record_index VARCHAR(128),
            source_detail TEXT,
            student_answer TEXT,
            correctness_level VARCHAR(64),
            evaluation TEXT,
            standard_answer TEXT,
            is_preset_question TINYINT(1) NOT NULL DEFAULT 0,
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
            invite_code VARCHAR(5) DEFAULT NULL,
            invite_code_expires_at DATETIME DEFAULT NULL,
            invite_code_created_at DATETIME DEFAULT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE KEY uniq_courses_course_name (course_name),
            UNIQUE KEY uniq_courses_invite_code (invite_code),
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
            user_id VARCHAR(128) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            requested_at DATETIME NOT NULL,
            reviewed_at DATETIME DEFAULT NULL,
            reviewed_by VARCHAR(128) DEFAULT NULL,
            INDEX idx_course_join_requests_course_id (course_id),
            INDEX idx_course_join_requests_user_id (user_id),
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
            need_code_repository TINYINT(1) NOT NULL DEFAULT 0,
            use_preset_questions TINYINT(1) NOT NULL DEFAULT 0,
            exam_available_from DATETIME NOT NULL,
            exam_available_until DATETIME NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            active_exam_item_name VARCHAR(128)
                GENERATED ALWAYS AS (
                    CASE WHEN status = 'active' THEN exam_item_name ELSE NULL END
                ) STORED,
            created_by VARCHAR(128) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE KEY uniq_course_exam_item_active_name (course_id, active_exam_item_name),
            INDEX idx_course_exam_items_course_id (course_id),
            INDEX idx_course_exam_items_status (status),
            CONSTRAINT fk_course_exam_items_course
                FOREIGN KEY (course_id)
                REFERENCES courses(course_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    _ensure_course_exam_item_active_name_unique_index(cursor)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_preset_questions (
            preset_question_id CHAR(36) PRIMARY KEY,
            exam_item_id CHAR(36) NOT NULL,
            question_dimension VARCHAR(255) NOT NULL,
            question_content TEXT NOT NULL,
            standard_answer TEXT DEFAULT NULL,
            question_blocks_json JSON DEFAULT NULL,
            code_fragments_json JSON DEFAULT NULL,
            score DOUBLE NOT NULL DEFAULT 1,
            sort_order INT NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_by VARCHAR(128) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            INDEX idx_exam_preset_questions_exam_item_id (exam_item_id),
            INDEX idx_exam_preset_questions_dimension (question_dimension),
            INDEX idx_exam_preset_questions_status (status),
            CONSTRAINT fk_exam_preset_questions_exam_item
                FOREIGN KEY (exam_item_id)
                REFERENCES course_exam_items(exam_item_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _ensure_course_join_requests_user_id(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_join_requests'
          AND COLUMN_NAME IN ('student_id', 'user_id')
        """,
        (database,),
    )
    columns = {row[0] for row in cursor.fetchall()}
    if "user_id" not in columns and "student_id" in columns:
        cursor.execute(
            "ALTER TABLE course_join_requests "
            "CHANGE COLUMN student_id user_id VARCHAR(128) NOT NULL"
        )

    cursor.execute(
        """
        SELECT INDEX_NAME
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_join_requests'
          AND INDEX_NAME IN (
              'idx_course_join_requests_student_id',
              'idx_course_join_requests_user_id'
          )
        """,
        (database,),
    )
    index_names = {row[0] for row in cursor.fetchall()}
    if "idx_course_join_requests_user_id" not in index_names:
        cursor.execute(
            "ALTER TABLE course_join_requests "
            "ADD INDEX idx_course_join_requests_user_id (user_id)"
        )
    if "idx_course_join_requests_student_id" in index_names:
        cursor.execute(
            "ALTER TABLE course_join_requests "
            "DROP INDEX idx_course_join_requests_student_id"
        )


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


def _ensure_course_exam_item_active_name_unique_index(cursor) -> None:
    """Ensure only active exam items must have unique names per course."""
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_exam_items'
          AND COLUMN_NAME = 'active_exam_item_name'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """
            ALTER TABLE course_exam_items
            ADD COLUMN active_exam_item_name VARCHAR(128)
                GENERATED ALWAYS AS (
                    CASE WHEN status = 'active' THEN exam_item_name ELSE NULL END
                ) STORED
            """
        )

    cursor.execute(
        """
        SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_exam_items'
          AND NON_UNIQUE = 0
          AND INDEX_NAME <> 'PRIMARY'
        GROUP BY INDEX_NAME
        """,
        (database,),
    )

    active_index_exists = False
    obsolete_index_names = []
    for index_name, columns in cursor.fetchall():
        normalized_columns = columns.replace(" ", "") if columns else ""
        quoted_name = str(index_name).replace("`", "``")
        if index_name == "uniq_course_exam_item_active_name":
            if normalized_columns == "course_id,active_exam_item_name":
                active_index_exists = True
            else:
                cursor.execute(f"ALTER TABLE course_exam_items DROP INDEX `{quoted_name}`")
        elif normalized_columns in {
            "exam_item_name",
            "course_id,exam_item_name",
            "course_id,exam_item_name,status",
        }:
            obsolete_index_names.append(quoted_name)

    if not active_index_exists:
        cursor.execute(
            """
            SELECT course_id, exam_item_name
            FROM course_exam_items
            WHERE status = 'active'
            GROUP BY course_id, exam_item_name
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
        duplicate = cursor.fetchone()
        if duplicate is not None:
            raise ValueError(
                "duplicate active exam_item_name exists in course: "
                f"{duplicate[0]}, {duplicate[1]}"
            )

        cursor.execute(
            "ALTER TABLE course_exam_items "
            "ADD UNIQUE KEY uniq_course_exam_item_active_name "
            "(course_id, active_exam_item_name)"
        )

    for quoted_name in obsolete_index_names:
        cursor.execute(f"ALTER TABLE course_exam_items DROP INDEX `{quoted_name}`")


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


def _ensure_courses_invite_code_fields(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    fields = (
        ("invite_code", "VARCHAR(5) DEFAULT NULL"),
        ("invite_code_expires_at", "DATETIME DEFAULT NULL"),
        ("invite_code_created_at", "DATETIME DEFAULT NULL"),
    )
    for field_name, field_type in fields:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'courses'
              AND COLUMN_NAME = %s
            """,
            (database, field_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE courses ADD COLUMN {field_name} {field_type}")

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'courses'
          AND INDEX_NAME = 'uniq_courses_invite_code'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE courses ADD UNIQUE KEY uniq_courses_invite_code (invite_code)")


def _ensure_course_exam_items_availability_fields(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    for field_name in ("exam_available_from", "exam_available_until"):
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'course_exam_items'
              AND COLUMN_NAME = %s
            """,
            (database, field_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                f"ALTER TABLE course_exam_items ADD COLUMN {field_name} DATETIME DEFAULT NULL"
            )

    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_exam_items'
          AND COLUMN_NAME IN ('invite_code', 'invite_code_created_at', 'invite_code_expires_at')
        """,
        (database,),
    )
    legacy_columns = {row[0] for row in cursor.fetchall()}
    if {"invite_code_created_at", "invite_code_expires_at"} <= legacy_columns:
        cursor.execute(
            """
            UPDATE course_exam_items
            SET exam_available_from = COALESCE(exam_available_from, invite_code_created_at, created_at),
                exam_available_until = COALESCE(exam_available_until, invite_code_expires_at, updated_at)
            WHERE exam_available_from IS NULL
               OR exam_available_until IS NULL
            """
        )
    else:
        cursor.execute(
            """
            UPDATE course_exam_items
            SET exam_available_from = COALESCE(exam_available_from, created_at),
                exam_available_until = COALESCE(exam_available_until, updated_at)
            WHERE exam_available_from IS NULL
               OR exam_available_until IS NULL
            """
        )

    for field_name in (
        "invite_code",
        "invite_code_expires_at",
        "invite_code_created_at",
    ):
        if field_name in legacy_columns:
            cursor.execute(f"ALTER TABLE course_exam_items DROP COLUMN {field_name}")

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_exam_items'
          AND COLUMN_NAME IN ('exam_available_from', 'exam_available_until')
          AND IS_NULLABLE = 'YES'
        """,
        (database,),
    )
    if cursor.fetchone()[0] > 0:
        cursor.execute(
            """
            ALTER TABLE course_exam_items
            MODIFY COLUMN exam_available_from DATETIME NOT NULL,
            MODIFY COLUMN exam_available_until DATETIME NOT NULL
            """
        )


def _ensure_course_exam_items_need_code_repository(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_exam_items'
          AND COLUMN_NAME = 'need_code_repository'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "ALTER TABLE course_exam_items "
            "ADD COLUMN need_code_repository TINYINT(1) NOT NULL DEFAULT 0 AFTER attempt_count"
        )


def _ensure_course_exam_items_use_preset_questions(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_exam_items'
          AND COLUMN_NAME = 'use_preset_questions'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "ALTER TABLE course_exam_items "
            "ADD COLUMN use_preset_questions TINYINT(1) NOT NULL DEFAULT 0 AFTER need_code_repository"
        )


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


def _ensure_exam_questions_is_preset_question(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_questions'
          AND COLUMN_NAME = 'is_preset_question'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "ALTER TABLE exam_questions "
            "ADD COLUMN is_preset_question TINYINT(1) NOT NULL DEFAULT 0 AFTER standard_answer"
        )


def _ensure_exam_questions_based_on_record_index_type(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_questions'
          AND COLUMN_NAME = 'based_on_record_index'
        """,
        (database,),
    )
    row = cursor.fetchone()
    if row and str(row[0]).lower() != "varchar":
        cursor.execute(
            "ALTER TABLE exam_questions "
            "MODIFY COLUMN based_on_record_index VARCHAR(128)"
        )


def _ensure_exam_sessions_extra_fields(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    fields = (
        ("repository_url", "TEXT DEFAULT NULL"),
        ("need_code_repository", "TINYINT(1) NOT NULL DEFAULT 0"),
        ("use_preset_questions", "TINYINT(1) NOT NULL DEFAULT 0"),
        ("exam_completed", "TINYINT(1) NOT NULL DEFAULT 0"),
        ("exam_score", "DOUBLE DEFAULT NULL"),
        ("exam_item_name", "VARCHAR(128) DEFAULT NULL"),
        ("exam_dimension_scores_json", "JSON"),
    )
    for field_name, field_type in fields:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'exam_sessions'
              AND COLUMN_NAME = %s
            """,
            (database, field_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE exam_sessions ADD COLUMN {field_name} {field_type}")

    cursor.execute(
        """
        UPDATE exam_sessions s
        SET s.exam_score = s.total_score,
            s.total_score = COALESCE((
                SELECT i.total_score
                FROM course_exam_items i
                WHERE i.exam_item_id = s.exam_item_id
                LIMIT 1
            ), s.total_score)
        WHERE s.exam_completed = 1
          AND s.exam_score IS NULL
        """
    )
    cursor.execute(
        """
        UPDATE exam_sessions s
        SET s.exam_item_name = (
            SELECT i.exam_item_name
            FROM course_exam_items i
            WHERE i.exam_item_id = s.exam_item_id
            LIMIT 1
        )
        WHERE s.exam_item_name IS NULL
          AND s.exam_item_id IS NOT NULL
        """
    )
    cursor.execute(
        """
        UPDATE exam_sessions s
        SET s.exam_dimension_scores_json = s.dimension_scores_json,
            s.dimension_scores_json = COALESCE((
                SELECT i.dimension_scores_json
                FROM course_exam_items i
                WHERE i.exam_item_id = s.exam_item_id
                LIMIT 1
            ), s.dimension_scores_json)
        WHERE s.exam_completed = 1
          AND s.exam_dimension_scores_json IS NULL
        """
    )


def _ensure_exam_sessions_ended_at_nullable(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND COLUMN_NAME = 'ended_at'
        """,
        (database,),
    )
    row = cursor.fetchone()
    if row and row[0] == "NO":
        cursor.execute("ALTER TABLE exam_sessions MODIFY COLUMN ended_at DATETIME DEFAULT NULL")


def _ensure_exam_preset_questions_extra_fields(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    fields = (
        ("question_blocks_json", "JSON DEFAULT NULL"),
        ("code_fragments_json", "JSON DEFAULT NULL"),
        ("score", "DOUBLE NOT NULL DEFAULT 1"),
        ("sort_order", "INT NOT NULL DEFAULT 0"),
    )
    for field_name, field_type in fields:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'exam_preset_questions'
              AND COLUMN_NAME = %s
            """,
            (database, field_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE exam_preset_questions ADD COLUMN {field_name} {field_type}")


def _ensure_exam_sessions_unique_user_course_item(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_sessions'
          AND INDEX_NAME = 'uniq_exam_session_user_course_item'
        """,
        (database,),
    )
    if cursor.fetchone()[0] > 0:
        return

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT user_id, course_id, exam_item_id
            FROM exam_sessions
            WHERE user_id IS NOT NULL
              AND course_id IS NOT NULL
              AND exam_item_id IS NOT NULL
            GROUP BY user_id, course_id, exam_item_id
            HAVING COUNT(*) > 1
        ) duplicated_sessions
        """
    )
    if cursor.fetchone()[0] > 0:
        return

    cursor.execute(
        "ALTER TABLE exam_sessions "
        "ADD UNIQUE KEY uniq_exam_session_user_course_item (user_id, course_id, exam_item_id)"
    )


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
