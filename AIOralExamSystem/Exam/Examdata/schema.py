from .config import LOCAL_MYSQL_CONFIG
from LLM.schema import create_llm_tables


def ensure_tables(connection) -> None:
    with connection.cursor() as cursor:
        _create_stage_0_base_tables(cursor)
        _create_stage_1_dependent_tables(cursor)
        _create_stage_2_dependent_tables(cursor)
        _create_stage_3_dependent_tables(cursor)
        _ensure_schema_migrations(cursor)


def _create_stage_0_base_tables(cursor) -> None:
    _create_exam_sessions_table(cursor)
    _create_exam_report_scores_table(cursor)
    _create_courses_table(cursor)
    # 表说明：user_model_library 记录用户配置的可用 LLM 模型，供后续评分 Agent 配置引用。
    create_llm_tables(cursor)


def _create_stage_1_dependent_tables(cursor) -> None:
    _create_exam_questions_table(cursor)
    _create_course_teachers_table(cursor)
    _create_course_students_table(cursor)
    _create_course_join_requests_table(cursor)
    _create_course_exam_items_table(cursor)


def _create_stage_2_dependent_tables(cursor) -> None:
    _create_exam_preset_questions_table(cursor)
    _create_exam_judge_configs_table(cursor)
    _create_exam_report_templates_table(cursor)


def _create_stage_3_dependent_tables(cursor) -> None:
    _create_exam_judge_config_agents_table(cursor)


def _ensure_schema_migrations(cursor) -> None:
    ensure_final_review_html_column(cursor)
    migrations = (
        _ensure_course_join_requests_user_id,
        _ensure_exam_sessions_user_id,
        _ensure_exam_sessions_course_id,
        _ensure_exam_sessions_exam_item_id,
        _ensure_exam_sessions_extra_fields,
        _ensure_exam_sessions_activity_fields,
        _ensure_exam_sessions_ended_at_nullable,
        _ensure_exam_sessions_unique_user_course_item,
        _ensure_courses_course_name_unique_index,
        _ensure_courses_invite_code_fields,
        _ensure_course_exam_items_availability_fields,
        _ensure_course_exam_items_need_code_repository,
        _ensure_course_exam_items_use_preset_questions,
        _ensure_course_exam_items_report_analysis,
        _ensure_course_exam_items_course_documents,
        _ensure_course_exam_items_lifecycle_fields,
        _ensure_course_exam_item_active_name_unique_index,
        _ensure_exam_preset_questions_extra_fields,
        _ensure_exam_sessions_no_candidate_id,
        _ensure_exam_questions_is_preset_question,
        _ensure_exam_questions_based_on_record_index_type,
        _ensure_exam_questions_exam_id_index,
        _ensure_exam_report_scores_extra_fields,
        _ensure_exam_report_templates_exam_item_schema,
        _ensure_exam_report_templates_content_schema,
    )
    for migration in migrations:
        migration(cursor)


def _create_exam_sessions_table(cursor) -> None:
    # 表说明：exam_sessions 记录一次学生考试会话的基础信息、总分、维度得分和完成状态。
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
            final_review_html MEDIUMTEXT DEFAULT NULL,
            repository_url TEXT DEFAULT NULL,
            repository_updated_at DATETIME(6) DEFAULT NULL,
            need_code_repository TINYINT(1) NOT NULL DEFAULT 0,
            use_preset_questions TINYINT(1) NOT NULL DEFAULT 0,
            exam_completed TINYINT(1) NOT NULL DEFAULT 0,
            exam_active_token CHAR(36) DEFAULT NULL,
            exam_active_until DATETIME DEFAULT NULL,
            ended_at DATETIME DEFAULT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_exam_sessions_user_id (user_id),
            INDEX idx_exam_sessions_course_id (course_id),
            INDEX idx_exam_sessions_exam_item_id (exam_item_id),
            INDEX idx_exam_sessions_active_until (exam_item_id, exam_active_until),
            UNIQUE KEY uniq_exam_session_user_course_item (user_id, course_id, exam_item_id)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _create_exam_report_scores_table(cursor) -> None:
    # 表说明：exam_report_scores 记录课程考试项下学生报告分析的得分、总分和分析结果。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_report_scores (
            report_score_id CHAR(36) PRIMARY KEY,
            course_id CHAR(36) NOT NULL,
            exam_item_id CHAR(36) NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            exam_id CHAR(36) DEFAULT NULL,
            report_score DOUBLE NOT NULL,
            report_total_score DOUBLE NOT NULL,
            report_result_json JSON DEFAULT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            INDEX idx_report_scores_course_item_user (course_id, exam_item_id, user_id),
            INDEX idx_report_scores_exam_id (exam_id),
            INDEX idx_report_scores_status (status)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _create_courses_table(cursor) -> None:
    # 表说明：courses 记录课程的基础资料、所属教师、邀请码和课程状态。
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


def _create_exam_questions_table(cursor) -> None:
    # 表说明：exam_questions 记录考试会话中的每道题、学生答案、评分结果和追问来源。
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


def _create_course_teachers_table(cursor) -> None:
    # 表说明：course_teachers 记录课程与教师的关联关系以及教师在课程中的角色。
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


def _create_course_students_table(cursor) -> None:
    # 表说明：course_students 记录课程与学生的正式加入关系和加入时间。
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


def _create_course_join_requests_table(cursor) -> None:
    # 表说明：course_join_requests 记录学生申请加入课程的待审核请求和审核结果。
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


def _create_course_exam_items_table(cursor) -> None:
    # 表说明：course_exam_items 记录课程下发布的考试项目、分值配置、开放时间和考试选项。
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
            enable_report_analysis TINYINT(1) NOT NULL DEFAULT 0,
            report_total_score DOUBLE DEFAULT NULL,
            report_judge_rule TEXT DEFAULT NULL,
            course_document_sources_json JSON DEFAULT NULL,
            exam_available_valid_times INT NOT NULL DEFAULT 0,
            exam_available_from DATETIME NOT NULL,
            exam_available_until DATETIME NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            version INT NOT NULL DEFAULT 1,
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


def _create_exam_preset_questions_table(cursor) -> None:
    # 表说明：exam_preset_questions 记录考试项目预设题目、标准答案、代码片段和排序信息。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_preset_questions (
            preset_question_id CHAR(36) PRIMARY KEY,
            exam_item_id CHAR(36) NOT NULL,
            user_id VARCHAR(128) DEFAULT NULL,
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
            INDEX idx_exam_preset_questions_user_source (exam_item_id, user_id, created_by, status),
            INDEX idx_exam_preset_questions_dimension (question_dimension),
            INDEX idx_exam_preset_questions_status (status),
            CONSTRAINT fk_exam_preset_questions_exam_item
                FOREIGN KEY (exam_item_id)
                REFERENCES course_exam_items(exam_item_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _create_exam_report_templates_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_report_templates (
            template_id CHAR(36) PRIMARY KEY,
            exam_item_id CHAR(36) NOT NULL,
            template_name VARCHAR(255) NOT NULL,
            module_queue_json JSON NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE KEY uniq_exam_report_template (exam_item_id),
            CONSTRAINT fk_exam_report_templates_exam_item
                FOREIGN KEY (exam_item_id)
                REFERENCES course_exam_items(exam_item_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _ensure_exam_report_templates_exam_item_schema(cursor) -> None:
    """Migrate report templates from per-session ownership to per-exam-item ownership."""
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_report_templates'
          AND COLUMN_NAME = 'exam_id'
        """,
        (database,),
    )
    has_exam_id = cursor.fetchone()[0] > 0

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_report_templates'
          AND COLUMN_NAME = 'exam_item_id'
        """,
        (database,),
    )
    has_exam_item_id = cursor.fetchone()[0] > 0

    if not has_exam_item_id:
        cursor.execute(
            "ALTER TABLE exam_report_templates "
            "ADD COLUMN exam_item_id CHAR(36) NULL AFTER template_id"
        )
        has_exam_item_id = True

    if has_exam_id:
        cursor.execute(
            """
            UPDATE exam_report_templates AS template
            INNER JOIN exam_sessions AS session
                ON session.exam_id = template.exam_id
            SET template.exam_item_id = session.exam_item_id
            WHERE template.exam_item_id IS NULL
            """
        )
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM exam_report_templates
            WHERE exam_item_id IS NULL
            """
        )
        unmapped_count = cursor.fetchone()[0]
        if unmapped_count:
            raise RuntimeError(
                "cannot migrate exam_report_templates: "
                f"{unmapped_count} row(s) have no exam_item_id"
            )

        # Multiple session templates may now belong to one exam item. Keep the
        # most recently updated template, breaking timestamp ties by template ID.
        cursor.execute(
            """
            DELETE older
            FROM exam_report_templates AS older
            INNER JOIN exam_report_templates AS newer
                ON older.exam_item_id = newer.exam_item_id
               AND (
                    older.updated_at < newer.updated_at
                    OR (
                        older.updated_at = newer.updated_at
                        AND older.template_id < newer.template_id
                    )
               )
            """
        )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = %s
              AND TABLE_NAME = 'exam_report_templates'
              AND CONSTRAINT_NAME = 'fk_exam_report_templates_exam'
              AND CONSTRAINT_TYPE = 'FOREIGN KEY'
            """,
            (database,),
        )
        if cursor.fetchone()[0] > 0:
            cursor.execute(
                "ALTER TABLE exam_report_templates "
                "DROP FOREIGN KEY fk_exam_report_templates_exam"
            )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'exam_report_templates'
              AND INDEX_NAME = 'uniq_exam_report_template'
            """,
            (database,),
        )
        if cursor.fetchone()[0] > 0:
            cursor.execute(
                "ALTER TABLE exam_report_templates "
                "DROP INDEX uniq_exam_report_template"
            )

        cursor.execute(
            "ALTER TABLE exam_report_templates "
            "MODIFY COLUMN exam_item_id CHAR(36) NOT NULL"
        )
        cursor.execute(
            "ALTER TABLE exam_report_templates DROP COLUMN exam_id"
        )

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_report_templates'
          AND INDEX_NAME = 'uniq_exam_report_template'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "ALTER TABLE exam_report_templates "
            "ADD UNIQUE KEY uniq_exam_report_template (exam_item_id)"
        )

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = %s
          AND TABLE_NAME = 'exam_report_templates'
          AND CONSTRAINT_NAME = 'fk_exam_report_templates_exam_item'
          AND CONSTRAINT_TYPE = 'FOREIGN KEY'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "ALTER TABLE exam_report_templates "
            "ADD CONSTRAINT fk_exam_report_templates_exam_item "
            "FOREIGN KEY (exam_item_id) "
            "REFERENCES course_exam_items(exam_item_id) "
            "ON DELETE CASCADE"
        )


def _ensure_exam_report_templates_content_schema(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = %s
          AND TABLE_NAME = 'exam_report_templates'
          AND CONSTRAINT_NAME = 'fk_exam_report_templates_course'
          AND CONSTRAINT_TYPE = 'FOREIGN KEY'
        """,
        (database,),
    )
    if cursor.fetchone()[0] > 0:
        cursor.execute(
            "ALTER TABLE exam_report_templates DROP FOREIGN KEY fk_exam_report_templates_course"
        )

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_report_templates'
          AND INDEX_NAME = 'idx_exam_report_templates_course'
        """,
        (database,),
    )
    if cursor.fetchone()[0] > 0:
        cursor.execute(
            "ALTER TABLE exam_report_templates DROP INDEX idx_exam_report_templates_course"
        )

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_report_templates'
          AND COLUMN_NAME = 'course_id'
        """,
        (database,),
    )
    if cursor.fetchone()[0] > 0:
        cursor.execute(
            "ALTER TABLE exam_report_templates DROP COLUMN course_id"
        )

    obsolete_columns = (
        "template_source_dir",
        "question_module_key",
        "created_by",
    )
    for column_name in obsolete_columns:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'exam_report_templates'
              AND COLUMN_NAME = %s
            """,
            (database, column_name),
        )
        if cursor.fetchone()[0] > 0:
            cursor.execute(
                f"ALTER TABLE exam_report_templates DROP COLUMN {column_name}"
            )


def _create_exam_judge_configs_table(cursor) -> None:
    # 表说明：exam_judge_configs 记录考试项目的评分流程配置、评委数量和仲裁策略。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_judge_configs (
            config_id CHAR(36) PRIMARY KEY,
            exam_item_id CHAR(36) NOT NULL,
            flow_type VARCHAR(32) NOT NULL DEFAULT 'single',
            judge_count INT NOT NULL DEFAULT 1,
            adjudicator_enabled TINYINT(1) NOT NULL DEFAULT 0,
            fail_policy VARCHAR(32) NOT NULL DEFAULT 'majority',
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_by VARCHAR(128) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE KEY uniq_exam_judge_config_exam_item (exam_item_id),
            INDEX idx_exam_judge_configs_status (status),
            CONSTRAINT fk_exam_judge_configs_exam_item
                FOREIGN KEY (exam_item_id)
                REFERENCES course_exam_items(exam_item_id)
                ON DELETE CASCADE
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _create_exam_judge_config_agents_table(cursor) -> None:
    # 表说明：exam_judge_config_agents 记录评分配置中每个 Agent 使用的模型和参数设置。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_judge_config_agents (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            config_id CHAR(36) NOT NULL,
            agent_role VARCHAR(32) NOT NULL,
            agent_index INT NOT NULL DEFAULT 0,
            model_id CHAR(36) NOT NULL,
            model_settings_json JSON DEFAULT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE KEY uniq_exam_judge_config_agent (config_id, agent_role, agent_index),
            INDEX idx_exam_judge_config_agents_config (config_id),
            INDEX idx_exam_judge_config_agents_model (model_id),
            CONSTRAINT fk_exam_judge_config_agents_config
                FOREIGN KEY (config_id)
                REFERENCES exam_judge_configs(config_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_exam_judge_config_agents_model
                FOREIGN KEY (model_id)
                REFERENCES user_model_library(model_id)
                ON DELETE RESTRICT
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



def _ensure_course_exam_items_lifecycle_fields(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    fields = (
        ("exam_available_valid_times", "INT NOT NULL DEFAULT 0"),
        ("version", "INT NOT NULL DEFAULT 1"),
    )
    for field_name, field_type in fields:
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
                f"ALTER TABLE course_exam_items ADD COLUMN {field_name} {field_type}"
            )
    cursor.execute(
        """
        UPDATE course_exam_items
        SET exam_available_valid_times = GREATEST(
            1,
            TIMESTAMPDIFF(SECOND, exam_available_from, exam_available_until)
        )
        WHERE status = 'active'
          AND exam_available_valid_times = 0
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


def _ensure_course_exam_items_report_analysis(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    fields = (
        ("enable_report_analysis", "TINYINT(1) NOT NULL DEFAULT 0 AFTER use_preset_questions"),
        ("report_total_score", "DOUBLE DEFAULT NULL AFTER enable_report_analysis"),
        ("report_judge_rule", "TEXT DEFAULT NULL AFTER report_total_score"),
    )
    for field_name, field_type in fields:
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
            cursor.execute(f"ALTER TABLE course_exam_items ADD COLUMN {field_name} {field_type}")


def _ensure_course_exam_items_course_documents(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'course_exam_items'
          AND COLUMN_NAME = 'course_document_sources_json'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "ALTER TABLE course_exam_items "
            "ADD COLUMN course_document_sources_json JSON DEFAULT NULL AFTER use_preset_questions"
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
        ("repository_updated_at", "DATETIME(6) DEFAULT NULL"),
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


def ensure_final_review_html_column(cursor) -> None:
    cursor.execute(
        """SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'exam_sessions'
             AND COLUMN_NAME = 'final_review_html'""",
        (LOCAL_MYSQL_CONFIG["database"],),
    )
    if cursor.fetchone()[0] == 0:
        try:
            cursor.execute("ALTER TABLE exam_sessions ADD COLUMN final_review_html MEDIUMTEXT DEFAULT NULL")
        except Exception as exc:
            if not exc.args or exc.args[0] != 1060:
                raise


def _ensure_exam_sessions_activity_fields(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    for column_name, column_type in (
        ("exam_active_token", "CHAR(36) DEFAULT NULL"),
        ("exam_active_until", "DATETIME DEFAULT NULL"),
    ):
        cursor.execute(
            """
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'exam_sessions'
              AND COLUMN_NAME = %s
            """,
            (database, column_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                f"ALTER TABLE exam_sessions ADD COLUMN {column_name} {column_type}"
            )
    cursor.execute(
        """
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'exam_sessions'
          AND INDEX_NAME = 'idx_exam_sessions_active_until'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "ALTER TABLE exam_sessions "
            "ADD INDEX idx_exam_sessions_active_until (exam_item_id, exam_active_until)"
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
        ("user_id", "VARCHAR(128) DEFAULT NULL AFTER exam_item_id"),
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
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'exam_preset_questions'
          AND INDEX_NAME = 'idx_exam_preset_questions_user_source'
        """,
        (database,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """
            ALTER TABLE exam_preset_questions
            ADD INDEX idx_exam_preset_questions_user_source (exam_item_id, user_id, created_by, status)
            """
        )


def _ensure_exam_report_scores_extra_fields(cursor) -> None:
    database = LOCAL_MYSQL_CONFIG["database"]
    fields = (
        ("report_score_id", "CHAR(36) NOT NULL"),
        ("course_id", "CHAR(36) NOT NULL"),
        ("exam_item_id", "CHAR(36) NOT NULL"),
        ("user_id", "VARCHAR(128) NOT NULL"),
        ("exam_id", "CHAR(36) DEFAULT NULL"),
        ("report_score", "DOUBLE NOT NULL"),
        ("report_total_score", "DOUBLE NOT NULL"),
        ("report_result_json", "JSON DEFAULT NULL"),
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),
        ("created_at", "DATETIME NOT NULL"),
        ("updated_at", "DATETIME NOT NULL"),
    )
    for field_name, field_type in fields:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'exam_report_scores'
              AND COLUMN_NAME = %s
            """,
            (database, field_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE exam_report_scores ADD COLUMN {field_name} {field_type}")

    indexes = (
        ("idx_report_scores_course_item_user", "course_id, exam_item_id, user_id"),
        ("idx_report_scores_exam_id", "exam_id"),
        ("idx_report_scores_status", "status"),
    )
    for index_name, index_columns in indexes:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'exam_report_scores'
              AND INDEX_NAME = %s
            """,
            (database, index_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE exam_report_scores ADD INDEX {index_name} ({index_columns})")


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
