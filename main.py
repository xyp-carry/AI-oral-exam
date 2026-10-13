"""Oral exam entry point: session lifecycle, audio pipeline, and HTTP routes."""

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, List

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, File, Form, HTTPException, UploadFile
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

load_dotenv(override=True)

from AIOralExamSystem.Exam.Examdata import (
    get_available_exam_item_by_exam_id,
    get_exam_judge_config_by_exam_id,
    get_exam_session_by_exam_id,
)
from AIOralExamSystem.Exam.Examdata.exam_activity_repository import (
    begin_exam_session,
    end_exam_session,
    renew_exam_session,
)
from AIOralExamSystem.Exam.exam_pipeline_registry import ExamPipelineRegistry
from AIOralExamSystem.Tool.rag.data_tool import InsertTool
from AIOralExamSystem.url import exam_routes
from Authentication.auth import get_current_user
from Authentication.main import auth
from LLM.url import llm_routes
from OralService.CAnswerBoundaryProcessor import CAnswerBoundaryProcessor
from OralService.OralSTTService import STTServicer
from OralService.OralinterviewServiceA import InterviewServiceA
from OralService.OralinterviewServiceC import InterviewServiceC
from config import get_settings
from server import main
from tts import TTSFrameProcessor, create_tts_backend_from_binding


# --- Transport and process-local exam state ---

# We use lambdas to defer transport parameter creation until the transport
# type is selected at runtime.
transport_params = {
    "daily": lambda: DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "twilio": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


_pipeline_owner_pid: int | None = None
_pipeline_owner_id: str | None = None
active_exam_pipelines = ExamPipelineRegistry()


def pipeline_owner_id() -> str:
    """Give each worker process a stable identity for exam-session claims."""
    global _pipeline_owner_pid, _pipeline_owner_id
    pid = os.getpid()
    if _pipeline_owner_pid != pid:
        _pipeline_owner_pid = pid
        _pipeline_owner_id = str(uuid.uuid4())
    return _pipeline_owner_id


def flag_enabled(value) -> bool:
    """Interpret database flags stored as booleans, numbers, or strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def normalize_exam_type(value) -> str:
    exam_type = str(value or "A").strip().upper()
    return exam_type if exam_type in {"A", "B", "C"} else "A"


def build_startup_error(message: str, exc: Exception | None = None) -> dict:
    payload = {
        "type": "exam_error",
        "code": "EXAM_STARTUP_ERROR",
        "message": message,
    }
    if exc is not None:
        payload["error_class"] = exc.__class__.__name__
        payload["detail"] = str(exc)
    return payload


# --- Exam-session lifecycle ---


async def _keep_exam_session_active(
    exam_id: str, token: str, pipeline_id: str, owner_id: str, task: PipelineTask
) -> None:
    failures = 0
    while True:
        await asyncio.sleep(30)
        try:
            renewed = await renew_exam_session(
                exam_id, token, pipeline_id, owner_id
            )
        except Exception:
            logger.exception("考试运行标记续期失败")
            failures += 1
            if failures >= 3:
                logger.error("考试运行标记连续续期失败，终止考试以避免配置切换冲突")
                await task.cancel(reason="考试运行状态无法确认")
                return
            continue
        if not renewed:
            logger.error("考试运行标记已失效，终止旧 pipeline")
            await task.cancel(reason="考试运行状态已失效")
            return
        failures = 0


async def _load_exam_context(exam_info: dict, current_user: dict) -> tuple[dict, dict, str]:
    """Check exam ownership and availability, then resolve its exam type."""
    exam_id = str(exam_info.get("exam_id", "")).strip()
    if not exam_id:
        raise ValueError("exam_info 缺少 exam_id")
    exam_session = await get_exam_session_by_exam_id(exam_id)
    if not exam_session:
        raise ValueError("考试记录不存在")
    current_user_id = str(current_user.get("uuid") or current_user.get("id") or current_user.get("user_id") or "")
    if current_user_id and str(exam_session.get("user_id")) != current_user_id:
        raise ValueError("当前用户无权开启该考试")
    exam_item = await get_available_exam_item_by_exam_id(exam_id)
    if not exam_item:
        raise ValueError("考试项不存在或不在可开启时间内")
    exam_type = normalize_exam_type(
        exam_session.get("exam_type")
        or exam_item.get("exam_type")
        or exam_item.get("item_type")
        or exam_info.get("exam_type")
        or exam_info.get("type")
    )
    return exam_session, exam_item, exam_type


async def _build_exam_user(
    exam_id: str, exam_type: str, exam_session: dict,
    exam_item: dict, current_user: dict,
) -> tuple[dict, dict]:
    """Check repository and model settings, then prepare interviewer inputs."""
    course_id = exam_item.get("course_id")
    need_code_repository = flag_enabled(exam_session.get("need_code_repository"))
    use_preset_questions = flag_enabled(exam_session.get("use_preset_questions"))
    if exam_type == "B":
        need_code_repository = False
        use_preset_questions = True
    if need_code_repository and not str(exam_session.get("repository_url") or "").strip():
        raise ValueError("当前考试需要代码仓库，请先上传仓库")
    judge_config = await get_exam_judge_config_by_exam_id(exam_id)
    if not judge_config:
        raise ValueError("未配置模型参数，请联系管理员")
    exam_user = {
        **current_user,
        "exam_id": exam_id,
        "exam_type": exam_type,
        "course_id": course_id,
        "exam_item_id": exam_item.get("exam_item_id"),
        "dimensions": exam_item.get("dimension_names") or [],
        "dimension_scores": exam_item.get("dimension_scores") or {},
        "need_code_repository": need_code_repository,
        "use_preset_questions": use_preset_questions,
        "file_local_address": (
            f"{course_id}/{exam_id}/main/doc"
            if need_code_repository and course_id and exam_id else None
        ),
        "code_local_address": (
            f"{course_id}/{exam_id}/main/code"
            if need_code_repository and course_id and exam_id else None
        ),
        "course_document_sources": exam_item.get("course_document_sources") or [],
        "judge_config": judge_config,
    }
    return exam_user, judge_config


# --- Audio pipeline construction ---


def _create_tts_processor(
    exam_id: str, exam_type: str, judge_config: dict
) -> TTSFrameProcessor:
    safe_exam_id = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in exam_id
    ).strip("_") or "unknown"
    return TTSFrameProcessor(
        create_tts_backend_from_binding(judge_config.get("tts")),
        output_sample_rate=32000,
        recordings_dir=(
            Path(__file__).resolve().parent
            / "exam_records"
            / f"{exam_type.lower()}_mode"
            / "tts"
            / safe_exam_id
        ),
    )


def _create_interviewer(
    exam_type: str, exam_id: str, exam_user: dict,
    history: List[Dict[str, str]], startup_error: dict | None,
):
    if exam_type == "A":
        logger.info("Using InterviewServiceA for exam_type=A")
        return InterviewServiceA(exam_user, history, startup_error=startup_error)
    if exam_type == "B":
        logger.info("Using follow-up interview service for exam_type=B")
        return InterviewServiceC(exam_id=exam_id, startup_error=startup_error, mode="B")
    logger.info("Using InterviewServiceC for exam_type=C")
    return InterviewServiceC(exam_id=exam_id, startup_error=startup_error)


def _build_pipeline_task(
    transport: BaseTransport, exam_type: str, STT_server,
    interviewer, tts_processor: TTSFrameProcessor | None,
) -> PipelineTask:
    """Connect audio input, context, metrics, interview, TTS, and output."""
    audiobuffer = AudioBufferProcessor(num_channels=1, enable_turn_audio=True)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        LLMContext(), user_params=LLMUserAggregatorParams(),
    )
    pipeline_steps = [transport.input(), user_aggregator, STT_server]
    if exam_type in {"B", "C"}:
        pipeline_steps.append(CAnswerBoundaryProcessor())
    pipeline_steps.append(interviewer)
    if tts_processor is not None:
        pipeline_steps.append(tts_processor)
    pipeline_steps.extend([assistant_aggregator, transport.output(), audiobuffer])
    return PipelineTask(
        Pipeline(pipeline_steps),
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=100000000,
    )


# --- Exam execution and cleanup ---


async def run_bot(
    transport: BaseTransport, runner_args: RunnerArguments,
    exam_info: dict, current_user: dict,
):
    """Claim the exam, run its pipeline, then release all runtime state."""
    startup_error = None
    judge_config = None
    exam_run_token = None
    pipeline_id = str(uuid.uuid4())
    owner_id = pipeline_owner_id()
    pipeline_registered = False
    exam_id = str(exam_info.get("exam_id", "")).strip()
    exam_type = normalize_exam_type(exam_info.get("exam_type") or exam_info.get("type"))
    exam_user = {**current_user, "exam_id": exam_id, "exam_type": exam_type}
    try:
        exam_session, exam_item, exam_type = await _load_exam_context(
            exam_info, current_user
        )
        exam_user, judge_config = await _build_exam_user(
            exam_id, exam_type, exam_session, exam_item, current_user
        )
        active_exam_pipelines.register(
            pipeline_id, exam_id, str(exam_session.get("user_id") or "")
        )
        pipeline_registered = True
        claim_task = asyncio.create_task(begin_exam_session(
            exam_id,
            str(exam_session.get("user_id") or ""),
            pipeline_id,
            owner_id,
            active_exam_pipelines.is_active,
        ))
        try:
            exam_run_token = await asyncio.shield(claim_task)
        except asyncio.CancelledError:
            try:
                claimed_token = await claim_task
            except Exception:
                claimed_token = None
            if claimed_token is not None:
                try:
                    await end_exam_session(
                        exam_id, claimed_token, pipeline_id, owner_id
                    )
                except Exception:
                    logger.exception("取消启动后清理考试运行标记失败")
            raise
    except asyncio.CancelledError:
        if pipeline_registered:
            active_exam_pipelines.unregister(pipeline_id)
        raise
    except Exception as exc:
        if pipeline_registered and exam_run_token is None:
            active_exam_pipelines.unregister(pipeline_id)
            pipeline_registered = False
        startup_error = build_startup_error(
            str(exc) or "考试启动失败，请联系老师或稍后重试。",
            exc,
        )
        print(startup_error)

    heartbeat_task = None
    try:
        history: List[Dict[str, str]] = []
        STT_server = STTServicer(exam_user, history)
        tts_processor = None
        if startup_error is None:
            try:
                tts_processor = _create_tts_processor(
                    exam_id, exam_type, judge_config or {}
                )
            except Exception as exc:
                logger.exception("考试 TTS 初始化失败")
                startup_error = build_startup_error("考试语音模型初始化失败。", exc)

        interviewer = _create_interviewer(
            exam_type, exam_id, exam_user, history, startup_error
        )
        task = _build_pipeline_task(
            transport, exam_type, STT_server, interviewer, tts_processor
        )
        runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
        if pipeline_registered:
            active_exam_pipelines.bind_task(pipeline_id, task)
            heartbeat_task = asyncio.create_task(
                _keep_exam_session_active(
                    exam_id, exam_run_token, pipeline_id, owner_id, task
                )
            )
        await runner.run(task)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        try:
            if exam_run_token is not None:
                await end_exam_session(
                    exam_id, exam_run_token, pipeline_id, owner_id
                )
        finally:
            if pipeline_registered:
                active_exam_pipelines.unregister(pipeline_id)


async def bot(runner_args: RunnerArguments, exam_info: dict, current_user: dict):
    """Create the requested transport and start the oral exam."""

    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, exam_info, current_user)


# --- HTTP application and support endpoints ---


app, args = main()
app = auth(app, args)
app = exam_routes(app, args)
app = llm_routes(app, args)

# Validate required settings during startup, as before.
settings = get_settings()


async def serve_app(app, args):
    config = uvicorn.Config(
        app, host="0.0.0.0", port=args.port,
        ssl_keyfile="./key.pem", ssl_certfile="./cert.pem",
    )
    server = uvicorn.Server(config)
    await server.serve()


# Upload reference files to the current user's retrieval source.
@app.post("/file/get_chunks")
async def get_chunks(
    course_id: str = Form(...),
    exam_id: str = Form(...),
    current_user: dict = Depends(get_current_user),
    files: List[UploadFile] = File(...),
):
    file_paths = []
    upload_work_dir = f"./updateFile/{current_user['uuid']}/{course_id}/{exam_id}"
    for file in files:
        Path("./updateFile").mkdir(parents=True, exist_ok=True)
        Path(upload_work_dir).mkdir(parents=True, exist_ok=True)
        file_location = f"{upload_work_dir}/{file.filename}"
        file_paths.append(file_location)
    try:
        for file_location in file_paths:
            with open(file_location, "wb+") as file_object:
                shutil.copyfileobj(file.file, file_object)
        rag_config = await get_exam_judge_config_by_exam_id(
            exam_id, include_api_key=True,
        ) or {}
        embedding_agent = rag_config.get("embedding") or {}
        mineru_agent = rag_config.get("mineru") or {}
        file_tool = InsertTool(
            "insert_tool",
            mineru_settings=mineru_agent.get("runtime_model_settings") or {},
            embedding_settings=embedding_agent.get("runtime_model_settings") or {},
            embedding_model_id=(embedding_agent.get("model") or {}).get("model_id"),
        )
        insert_result = await file_tool.execute(
            data=file_paths,
            source=current_user['uuid'],
            type="file",
            course_id=course_id,
            exam_id=exam_id,
            work_dir=upload_work_dir,
        )
        if not isinstance(insert_result, str) or not insert_result.startswith("\u6210\u529f\u63d2\u5165 "):
            raise RuntimeError(str(insert_result or "document insertion failed"))
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        raise HTTPException(status_code=500, detail=f"处理文件时出错: {str(e)}")

# Stop the current user's running oral exams.
@app.post("/close")
async def close(current_user: dict = Depends(get_current_user)):
    for task in active_exam_pipelines.tasks_for_user(current_user["uuid"]):
        await task.cancel()
        await task.cleanup()

if __name__ == "__main__":
    asyncio.run(serve_app(app, args))
