"""全部业务表的 SQLAlchemy 声明式模型。

约定：
- 建表与增量迁移仍由 schema.py 的 DDL 负责，模型只用于查询与写入，
  因此列类型按"绑定/读取行为与原 pymysql 等价"的原则声明：
  JSON 列声明为 Text（读出仍是字符串，由调用方 json.loads），
  TINYINT(1) 声明为 Integer（读出是 0/1 整数而非布尔）。
- 属性名与数据库列名一一同名，SELECT 按位置取值时与旧实现完全一致。
- 生成列（active_course_name / active_exam_item_name）声明为 Computed，
  SQLAlchemy 不会在 INSERT/UPDATE 中包含它们。
"""

from sqlalchemy import (
    CHAR,
    BigInteger,
    Column,
    Computed,
    DateTime,
    Double,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class ExamSession(Base):
    """一次学生考试会话：基础信息、总分、维度得分、终审报告与活动租约。"""

    __tablename__ = "exam_sessions"

    exam_id = Column(CHAR(36), primary_key=True)
    user_id = Column(String(128))
    course_id = Column(String(128))
    exam_item_id = Column(CHAR(36))
    exam_item_name = Column(String(128))
    candidate_info_json = Column(Text)
    total_score = Column(Double)
    c_max_probe_turns = Column(Integer)
    exam_score = Column(Double)
    dimension_count = Column(Integer)
    question_count = Column(Integer)
    dimension_scores_json = Column(Text)
    exam_dimension_scores_json = Column(Text)
    final_review_json = Column(Text)
    final_review_html = Column(Text)
    repository_url = Column(Text)
    repository_updated_at = Column(DateTime)
    need_code_repository = Column(Integer, nullable=False, default=0)
    use_preset_questions = Column(Integer, nullable=False, default=0)
    exam_completed = Column(Integer, nullable=False, default=0)
    exam_active_token = Column(CHAR(36))
    exam_active_until = Column(DateTime)
    exam_pipeline_id = Column(CHAR(36))
    exam_pipeline_owner = Column(CHAR(36))
    ended_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False)


class ExamQuestion(Base):
    """考试会话中的每道题：题面、学生答案、评分结果和追问链来源。"""

    __tablename__ = "exam_questions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    exam_id = Column(CHAR(36), nullable=False)
    record_index = Column(Integer, nullable=False)
    question_id = Column(String(128))
    question_content = Column(Text)
    question_dimension = Column(String(255))
    question_score = Column(Double)
    based_on_record_index = Column(String(128))
    source_detail = Column(Text)
    student_answer = Column(Text)
    correctness_level = Column(String(64))
    evaluation = Column(Text)
    standard_answer = Column(Text)
    is_preset_question = Column(Integer, nullable=False, default=0)
    root_question_id = Column(String(128))
    parent_question_id = Column(String(128))
    root_order = Column(Integer)
    followup_order = Column(Integer, nullable=False, default=0)
    chain_depth = Column(Integer, nullable=False, default=0)
    relation = Column(String(32))
    question_evaluation_json = Column(Text)
    question_evaluation_status = Column(String(32))
    created_at = Column(DateTime, nullable=False)


class ExamAnalysisDocument(Base):
    """每个 user/course/exam 的最新一份学情分析 Markdown 文档。"""

    __tablename__ = "exam_analysis_documents"

    user_id = Column(String(128), primary_key=True)
    course_id = Column(String(128), primary_key=True)
    exam_id = Column(CHAR(36), primary_key=True)
    analysis_id = Column(CHAR(36), nullable=False)
    title = Column(String(255), nullable=False)
    markdown_content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class ExamReportScore(Base):
    """课程考试项下学生报告分析的得分、总分和分析结果。"""

    __tablename__ = "exam_report_scores"

    report_score_id = Column(CHAR(36), primary_key=True)
    course_id = Column(CHAR(36), nullable=False)
    exam_item_id = Column(CHAR(36), nullable=False)
    user_id = Column(String(128), nullable=False)
    exam_id = Column(CHAR(36))
    report_score = Column(Double, nullable=False)
    report_total_score = Column(Double, nullable=False)
    report_result_json = Column(Text)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class Course(Base):
    """课程基础资料、所属教师、邀请码和课程状态。"""

    __tablename__ = "courses"

    course_id = Column(CHAR(36), primary_key=True)
    course_name = Column(String(128), nullable=False)
    description = Column(Text)
    owner_teacher_id = Column(String(128), nullable=False)
    invite_code = Column(String(5))
    invite_code_expires_at = Column(DateTime)
    invite_code_created_at = Column(DateTime)
    status = Column(String(20), nullable=False, default="active")
    active_course_name = Column(
        String(128),
        Computed("CASE WHEN status = 'active' THEN course_name ELSE NULL END", persisted=True),
    )
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class CourseTeacher(Base):
    """课程与教师的关联关系以及教师在课程中的角色。"""

    __tablename__ = "course_teachers"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    course_id = Column(CHAR(36), nullable=False)
    teacher_id = Column(String(128), nullable=False)
    teacher_role = Column(String(20), nullable=False, default="owner")
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False)


class CourseStudent(Base):
    """课程与学生的正式加入关系和加入时间。"""

    __tablename__ = "course_students"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    course_id = Column(CHAR(36), nullable=False)
    student_id = Column(String(128), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    joined_at = Column(DateTime, nullable=False)


class CourseJoinRequest(Base):
    """学生申请加入课程的待审核请求和审核结果。"""

    __tablename__ = "course_join_requests"

    request_id = Column(CHAR(36), primary_key=True)
    course_id = Column(CHAR(36), nullable=False)
    user_id = Column(String(128), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    requested_at = Column(DateTime, nullable=False)
    reviewed_at = Column(DateTime)
    reviewed_by = Column(String(128))


class CourseExamItem(Base):
    """课程下发布的考试项目、分值配置、开放时间和考试选项。"""

    __tablename__ = "course_exam_items"

    exam_item_id = Column(CHAR(36), primary_key=True)
    course_id = Column(CHAR(36), nullable=False)
    exam_item_name = Column(String(128), nullable=False)
    description = Column(Text)
    item_type = Column(String(32))
    dimension_names_json = Column(Text, nullable=False)
    dimension_scores_json = Column(Text, nullable=False)
    total_score = Column(Double, nullable=False, default=0)
    participant_count = Column(Integer, nullable=False, default=0)
    attempt_count = Column(Integer, nullable=False, default=0)
    need_code_repository = Column(Integer, nullable=False, default=0)
    use_preset_questions = Column(Integer, nullable=False, default=0)
    enable_report_analysis = Column(Integer, nullable=False, default=0)
    report_total_score = Column(Double)
    report_judge_rule = Column(Text)
    c_max_probe_turns = Column(Integer, nullable=False, default=2)
    analysis_question_count = Column(Integer, nullable=False, default=3)
    course_document_sources_json = Column(Text)
    exam_available_valid_times = Column(Integer, nullable=False, default=0)
    exam_available_from = Column(DateTime, nullable=False)
    exam_available_until = Column(DateTime, nullable=False)
    status = Column(String(20), nullable=False, default="active")
    version = Column(Integer, nullable=False, default=1)
    active_exam_item_name = Column(
        String(128),
        Computed(
            "CASE WHEN status = 'active' THEN exam_item_name ELSE NULL END",
            persisted=True,
        ),
    )
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class ExamPresetQuestion(Base):
    """考试项目预设题目、标准答案、代码片段和排序信息。"""

    __tablename__ = "exam_preset_questions"

    preset_question_id = Column(CHAR(36), primary_key=True)
    exam_item_id = Column(CHAR(36), nullable=False)
    user_id = Column(String(128))
    question_dimension = Column(String(255), nullable=False)
    question_content = Column(Text, nullable=False)
    standard_answer = Column(Text)
    question_blocks_json = Column(Text)
    code_fragments_json = Column(Text)
    score = Column(Double, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="active")
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class ExamReportTemplate(Base):
    """考试项的报告模板：模块队列配置。"""

    __tablename__ = "exam_report_templates"

    template_id = Column(CHAR(36), primary_key=True)
    exam_item_id = Column(CHAR(36), nullable=False)
    template_name = Column(String(255), nullable=False)
    module_queue_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class ExamJudgeConfig(Base):
    """考试项目的评分流程配置、评委数量和仲裁策略。"""

    __tablename__ = "exam_judge_configs"

    config_id = Column(CHAR(36), primary_key=True)
    exam_item_id = Column(CHAR(36), nullable=False)
    flow_type = Column(String(32), nullable=False, default="single")
    judge_count = Column(Integer, nullable=False, default=1)
    adjudicator_enabled = Column(Integer, nullable=False, default=0)
    fail_policy = Column(String(32), nullable=False, default="majority")
    status = Column(String(20), nullable=False, default="active")
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class ExamJudgeConfigAgent(Base):
    """评分配置中每个 Agent 使用的模型和参数设置。"""

    __tablename__ = "exam_judge_config_agents"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    config_id = Column(CHAR(36), nullable=False)
    agent_role = Column(String(32), nullable=False)
    agent_index = Column(Integer, nullable=False, default=0)
    model_id = Column(CHAR(36), nullable=False)
    model_settings_json = Column(Text)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
