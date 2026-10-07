"""URL-only background analysis using the scoped UniversalAgent."""

from __future__ import annotations

import asyncio
import copy
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.responses import PlainTextResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict

from AIOralExamSystem.Agent.UniversalAgent import UniversalAgent
from AIOralExamSystem.Exam.Examdata.analysis_document_repository import (
    get_analysis_document,
    get_analysis_document_metadata,
    save_analysis_document,
)
from AIOralExamSystem.Exam.Examdata import get_exam_item_by_id
from AIOralExamSystem.Exam.Examdata.exam_repository import (
    get_exam_session_by_exam_id,
    update_exam_session_repository_url,
)
from AIOralExamSystem.Exam.Examdata.judge_config_repository import (
    get_exam_judge_config_by_exam_item,
)
from AIOralExamSystem.Tool.git.git_tool import GitRepositoryTool
from AIOralExamSystem.report_template import ReportTemplate
from AIOralExamSystem.routes.other_routes import (
    _extract_a_questions_from_report_result,
    _extract_c_core_module_questions_from_report_result,
    _save_a_questions_to_preset_questions,
    _save_c_core_module_questions_to_preset_questions,
)
from Authentication.auth import get_current_user


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_PATH = _PROJECT_ROOT / "AIOralExamSystem" / "template" / "neihe_report_template.json"
_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-analysis")
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_PENDING_JOBS = 8
_JOB_RETENTION_SECONDS = 24 * 60 * 60
_REPORT_TIMEOUT_SECONDS = 900
_QUESTIONS_TIMEOUT_SECONDS = 300


class AgentAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    exam_id: str
    course_id: str


def _model_settings_for_role(config: dict, role: str = "main_judger") -> dict:
    agent_config = config.get(role)
    settings = agent_config.get("runtime_model_settings") if isinstance(agent_config, dict) else None
    if not isinstance(settings, dict):
        raise RuntimeError(f"该考试项目没有配置 {role} 模型")
    missing = [
        name for name in ("model_name", "model_url", "model_api_key")
        if not settings.get(name)
    ]
    if missing:
        raise RuntimeError(f"{role} 模型缺少配置字段：" + ", ".join(missing))
    return dict(settings)


def _prune_jobs(now: float) -> None:
    for analysis_id, job in list(_JOBS.items()):
        if job["status"] in {"completed", "failed"} and now - job["updated_at"] > _JOB_RETENTION_SECONDS:
            _JOBS.pop(analysis_id, None)


def _set_job(analysis_id: str, **changes) -> None:
    with _JOBS_LOCK:
        job = _JOBS[analysis_id]
        job.update(changes)
        job["updated_at"] = time.time()


def _record_model_round(analysis_id: str, event: str) -> None:
    with _JOBS_LOCK:
        job = _JOBS[analysis_id]
        if event == "started":
            job["current_round"] = job["completed_rounds"] + 1
            job["current_tool"] = None
        elif event == "completed":
            job["completed_rounds"] += 1
            job["current_round"] = None
        job["updated_at"] = time.time()


def _clean_document_title(value: str) -> str | None:
    title = re.sub(r"\s+", " ", value).strip(" #*_`\"'“”")
    if not title or title.startswith("[FIELD:"):
        return None
    if title.lower() in {"none", "null", "n/a", "unknown"}:
        return None
    if title.startswith(("未发现", "未提供", "无法确认", "暂无", "未知", "不详")):
        return None
    return title[:255]


def _analysis_document_title(agent_answer: str, markdown: str) -> str:
    for source, pattern in (
        (agent_answer, r"(?m)^\s*(?:[-*]\s*)?(?:标题|作品名称|项目名称)\s*[:：]\s*(.+?)\s*$"),
        (markdown, r"(?m)^\s*(?:[-*]\s*)?(?:作品名称|项目名称)\s*[:：]\s*(.+?)\s*$"),
    ):
        match = re.search(pattern, source)
        if match:
            title = _clean_document_title(match.group(1))
            if title:
                return title
    for match in re.finditer(r"(?m)^#{1,3}\s+(.+?)\s*$", markdown):
        title = _clean_document_title(match.group(1))
        if title and not re.match(r"^[0-9一二三四五六七八九十]+[、.．\s]", title):
            return title
    return "已上传的作业文档"


def _question_prompt(exam_type: str, dimensions: list[str]) -> str:
    if exam_type == "C":
        return (
            "报告完成后还必须生成口试问题：从仓库中选择最多三个有证据的核心模块，"
            "每个模块生成一道用于核验实际开发工作的题目，并调用 record_oral_questions。"
            "每项包含 module_name、question、Answer、aspect、source；"
            "source 尽量给出可核查的仓库文件路径或 Git 提交。"
        )
    return (
        "报告完成后还必须生成口试问题，并调用 record_oral_questions。"
        "每个评分维度生成一道与仓库实际代码或文档对应的核验题目，"
        "每项包含 dimension、Question、standard_answer；不要编造仓库证据。"
        f"评分维度：{dimensions if dimensions else '依据报告中的主要模块'}。"
    )


def _as_c_question_result(questions: list[dict]) -> dict:
    by_module: dict[str, dict] = {}
    for item in questions:
        module_name = str(item.get("module_name") or item.get("dimension") or "").strip()
        question = str(item.get("question") or item.get("Question") or "").strip()
        if not module_name or not question or module_name in by_module:
            continue
        by_module[module_name] = {
            "ok": True,
            "module_name": module_name,
            "questions": [item],
        }
        if len(by_module) >= 3:
            break
    return _extract_c_core_module_questions_from_report_result(
        {"core_module_questions": by_module}
    )


async def _execute_analysis(job: dict) -> dict:
    analysis_id = job["analysis_id"]
    user_id = job["user_id"]
    course_id = job["course_id"]
    exam_id = job["exam_id"]
    exam_item_id = job["exam_item_id"]
    repository_url = job["url"]
    exam_type = job["exam_type"]

    config = await get_exam_judge_config_by_exam_item(exam_item_id, include_api_key=True)
    if not isinstance(config, dict):
        raise RuntimeError("该考试项目没有可用的数据库模型配置")
    model_settings = _model_settings_for_role(config)
    exam_item = await get_exam_item_by_id(exam_item_id)
    dimensions = exam_item.get("dimension_names") if isinstance(exam_item, dict) else []
    dimensions = [str(value).strip() for value in dimensions if str(value).strip()] if isinstance(dimensions, list) else []

    template = ReportTemplate.from_file(_TEMPLATE_PATH)
    report_dir = _PROJECT_ROOT / "AIreport" / "analysis_jobs" / job["analysis_id"]
    report_dir.mkdir(parents=True, exist_ok=False)
    report_path = template.write_report(template.initial_report(), report_dir)
    agent = UniversalAgent(
        model_settings=model_settings,
        user_id=user_id,
        course_id=course_id,
        exam_id=exam_id,
        repository_url=repository_url,
        report_path=report_path,
        tool_event_callback=lambda name: _set_job(analysis_id, current_tool=name),
        model_round_callback=lambda event: _record_model_round(analysis_id, event),
    )
    report_prompt = (
        "请依据本次仓库中可核查的文件和 Git 记录完成项目分析报告，"
        "逐项填写报告模板中的所有占位符；无法确认的内容如实说明。"
        "最终回复首行请写“标题：<本次提交的简短标题>”；无法确定时省略标题行。"
    )
    try:
        instruction = template.document_instruction(report_prompt, report_path)
        _set_job(analysis_id, stage="report", current_tool=None)
        result = await asyncio.wait_for(
            agent.run(user_prompt=instruction), timeout=_REPORT_TIMEOUT_SECONDS
        )
        if not isinstance(result, dict) or result.get("agent_error"):
            raise RuntimeError(f"Agent 分析失败：{result}")

        report_text = await asyncio.to_thread(report_path.read_text, encoding="utf-8")
        remaining = template.remaining_fields(report_text)
        report_complete = (
            bool(result.get("repository_root"))
            and bool(result.get("branch"))
            and int(agent.report_write_count or 0) > 0
            and not remaining
        )
        if not report_complete:
            raise RuntimeError(f"报告尚未完成；报告路径：{report_path}；剩余字段：{remaining}")

        _set_job(analysis_id, stage="questions", current_tool=None)
        question_result_from_agent = await asyncio.wait_for(
            agent.run(
                user_prompt=(
                    "现在请基于刚完成的报告和当前仓库生成口试问题。"
                    + _question_prompt(exam_type, dimensions)
                    + "必须调用 record_oral_questions，不要只在回复中列出问题。"
                )
            ),
            timeout=_QUESTIONS_TIMEOUT_SECONDS,
        )
        if not isinstance(question_result_from_agent, dict) or question_result_from_agent.get("agent_error"):
            raise RuntimeError(f"Agent 生成口试问题失败：{question_result_from_agent}")
    finally:
        await agent.cleanup()

    if not agent.oral_questions:
        raise RuntimeError(f"Agent 未生成口试问题；报告路径：{report_path}")

    _set_job(analysis_id, stage="saving", current_round=None, current_tool=None)
    if exam_type == "C":
        question_result = _as_c_question_result(agent.oral_questions)
        if not question_result.get("question_count"):
            raise RuntimeError("C 类口试问题缺少核心模块或题目内容")
        preset_result = await _save_c_core_module_questions_to_preset_questions(
            course_id, exam_item_id, user_id, question_result
        )
    else:
        question_result = _extract_a_questions_from_report_result(
            {"questions": agent.oral_questions}
        )
        if not question_result.get("question_count"):
            raise RuntimeError("A 类口试问题为空")
        preset_result = await _save_a_questions_to_preset_questions(
            course_id, exam_item_id, user_id, question_result
        )
    if not preset_result.get("ok") or not preset_result.get("saved_count"):
        raise RuntimeError(f"口试问题保存失败：{preset_result}")

    updated = await update_exam_session_repository_url(
        user_id=user_id,
        course_id=course_id,
        exam_id=exam_id,
        repository_url=repository_url,
    )
    if not updated:
        raise RuntimeError("考试会话不存在，无法保存仓库地址")
    final_markdown = await asyncio.to_thread(report_path.read_text, encoding="utf-8")
    document_title = _analysis_document_title(str(result.get("answer") or ""), final_markdown)
    await save_analysis_document(
        user_id=user_id,
        course_id=course_id,
        exam_id=exam_id,
        analysis_id=analysis_id,
        title=document_title,
        markdown_content=final_markdown,
    )
    return {
        "report_path": str(report_path),
        "document_title": document_title,
        "report_complete": True,
        "repository_root": result.get("repository_root"),
        "branch": result.get("branch"),
        "answer": result.get("answer"),
        "question_count": question_result["question_count"],
        "saved_question_count": preset_result["saved_count"],
        "question_result": question_result,
        "preset_question_result": preset_result,
    }


def _run_job(analysis_id: str) -> None:
    _set_job(analysis_id, status="running", stage="preparing")
    with _JOBS_LOCK:
        job = dict(_JOBS[analysis_id])
    try:
        result = asyncio.run(_execute_analysis(job))
    except Exception as exc:
        logger.exception("UniversalAgent analysis failed: {}", analysis_id)
        _set_job(analysis_id, status="failed", stage="failed", current_round=None, current_tool=None, error=str(exc))
    else:
        _set_job(analysis_id, status="completed", stage="completed", current_round=None, current_tool=None, result=result)


def register_agent_analysis_routes(app, args) -> None:
    @app.post("/Agent/analysis", status_code=202)
    async def start_agent_analysis(
        request: AgentAnalysisRequest,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "").strip()
        repository_url = request.url.strip()
        if not user_id:
            raise HTTPException(status_code=401, detail="用户身份信息无效")
        if not repository_url:
            raise HTTPException(status_code=422, detail="url 不能为空")
        try:
            GitRepositoryTool("analysis_url_validator")._resolve_repository_url(repository_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        exam_id = request.exam_id.strip()
        course_id = request.course_id.strip()
        if not exam_id or not course_id:
            raise HTTPException(status_code=422, detail="exam_id 和 course_id 不能为空")
        session = await get_exam_session_by_exam_id(exam_id)
        if (
            not session
            or str(session.get("user_id") or "") != user_id
            or str(session.get("course_id") or "") != course_id
        ):
            raise HTTPException(status_code=404, detail="当前用户的考试会话不存在")
        if session.get("exam_completed"):
            raise HTTPException(status_code=409, detail="考试已完成，不能重新解析仓库")
        if not session.get("need_code_repository"):
            raise HTTPException(status_code=422, detail="该考试未启用代码仓库")
        analysis_id = str(uuid.uuid4())
        now = time.time()
        job = {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "course_id": str(session["course_id"]),
            "exam_id": str(session["exam_id"]),
            "exam_item_id": str(session["exam_item_id"]),
            "exam_type": str(session.get("exam_type") or "A").upper(),
            "url": repository_url,
            "status": "queued",
            "stage": "queued",
            "current_round": None,
            "completed_rounds": 0,
            "current_tool": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": now,
            "result": None,
            "error": None,
        }
        with _JOBS_LOCK:
            _prune_jobs(now)
            pending = [item for item in _JOBS.values() if item["status"] in {"queued", "running"}]
            if any(item["exam_id"] == job["exam_id"] for item in pending):
                raise HTTPException(status_code=409, detail="该考试已有正在进行的分析")
            if len(pending) >= _MAX_PENDING_JOBS:
                raise HTTPException(status_code=503, detail="分析任务已满，请稍后重试")
            _JOBS[analysis_id] = job
        try:
            _ANALYSIS_EXECUTOR.submit(_run_job, analysis_id)
        except RuntimeError as exc:
            with _JOBS_LOCK:
                _JOBS.pop(analysis_id, None)
            raise HTTPException(status_code=503, detail="分析线程暂不可用") from exc
        return {"analysis_id": analysis_id, "status": "queued"}

    @app.get("/Agent/analysis/{analysis_id}")
    async def get_agent_analysis(
        analysis_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "").strip()
        with _JOBS_LOCK:
            job = _JOBS.get(analysis_id)
            if job is None or job["user_id"] != user_id:
                raise HTTPException(status_code=404, detail="分析任务不存在")
            snapshot = copy.deepcopy(job)
        snapshot.pop("user_id", None)
        snapshot.pop("url", None)
        return snapshot


    @app.get("/Agent/analysis/exams/{exam_id}/progress")
    async def get_agent_analysis_progress(
        exam_id: str,
        course_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "").strip()
        with _JOBS_LOCK:
            matching_jobs = (
                job for job in _JOBS.values()
                if job["user_id"] == user_id
                and job["course_id"] == course_id
                and job["exam_id"] == exam_id
            )
            job = max(matching_jobs, key=lambda item: item["created_at"], default=None)
            progress = (
                {
                    "exam_id": exam_id,
                    "course_id": course_id,
                    "status": job["status"],
                    "stage": job["stage"],
                    "current_round": job["current_round"],
                    "completed_rounds": job["completed_rounds"],
                    "current_tool": job["current_tool"],
                    "updated_at": job["updated_at"],
                    "error": job["error"],
                }
                if job is not None else None
            )
        document = await get_analysis_document_metadata(user_id, course_id, exam_id)
        if progress is None:
            if document is None:
                raise HTTPException(status_code=404, detail="分析任务和解析文档均不存在")
            progress = {
                "exam_id": exam_id,
                "course_id": course_id,
                "status": "completed",
                "stage": "completed",
                "current_round": None,
                "completed_rounds": None,
                "current_tool": None,
                "updated_at": document["updated_at"],
                "error": None,
            }
        progress["title"] = document["title"] if document is not None else None
        return progress

    @app.get("/Agent/analysis/exams/{exam_id}/document")
    async def get_agent_analysis_document(
        exam_id: str,
        course_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "").strip()
        document = await get_analysis_document(user_id, course_id, exam_id)
        if document is None:
            raise HTTPException(status_code=404, detail="解析文档不存在")
        document.pop("user_id")
        return document

    @app.get("/Agent/analysis/{analysis_id}/report", response_class=PlainTextResponse)
    async def get_agent_analysis_report(
        analysis_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "").strip()
        with _JOBS_LOCK:
            job = _JOBS.get(analysis_id)
            if job is None or job["user_id"] != user_id:
                raise HTTPException(status_code=404, detail="分析任务不存在")
        report_path = (
            _PROJECT_ROOT / "AIreport" / "analysis_jobs" / analysis_id / "AIreport" / "report.md"
        )
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail="报告尚未生成")
        return PlainTextResponse(
            await asyncio.to_thread(report_path.read_text, encoding="utf-8")
        )
