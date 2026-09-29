from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import LLMRunFrame, EndFrame
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


from OralService.BaseService import save_audio_file
# from OralService.OralLLMService import LLMService
from OralService.OralinterviewServiceA import InterviewServiceA
from OralService.OralinterviewServiceB import InterviewServiceB
from OralService.OralSTTService import MetricsFrameLogger
from OralService.OralTTSService import TTSAudio
from OralService.OralinterviewServiceC import VoiceLogger
from server import main
import uvicorn
import asyncio

from fastapi import File, UploadFile, HTTPException, Depends, Form
from typing import List, Dict
from AIOralExamSystem.Tool.rag.data_tool import SearchToolInput, SearchTool, InsertTool
from AIOralExamSystem.utils.monitor import GlobalMonitor
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
from AIOralExamSystem.Exam.examObject import CandidateExamState
from AIOralExamSystem.url import exam_routes
from config import get_settings
from pathlib import Path
import shutil
from Authentication.main import auth
from Authentication.auth import get_current_user
from LLM.url import llm_routes

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


DEFAULT_INITIAL_SCORE = 5.0


def flag_enabled(value) -> bool:
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



async def _keep_exam_session_active(exam_id: str, token: str, task: PipelineTask) -> None:
    failures = 0
    while True:
        await asyncio.sleep(30)
        try:
            if await renew_exam_session(exam_id, token):
                failures = 0
                continue
        except Exception:
            logger.exception("考试运行标记续期失败")
        failures += 1
        if failures >= 3:
            logger.error("考试运行标记连续续期失败，终止考试以避免配置切换冲突")
            await task.cancel(reason="考试运行状态无法确认")
            return


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments, exam_info: dict, current_user: dict):
    startup_error = None
    dimensions = []
    exam_session = None
    exam_item = None
    exam_run_token = None
    exam_id = str(exam_info.get("exam_id", "")).strip()
    exam_type = normalize_exam_type(exam_info.get("exam_type") or exam_info.get("type"))
    exam_user = {**current_user, "exam_id": exam_id, "exam_type": exam_type}
    try:
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
        dimensions = exam_item.get("dimension_names") or []
        if not dimensions:
            raise ValueError("当前考试项没有配置考试维度")
        exam_type = normalize_exam_type(
            exam_session.get("exam_type")
            or exam_item.get("exam_type")
            or exam_item.get("item_type")
            or exam_info.get("exam_type")
            or exam_info.get("type")
        )
        course_id = exam_item.get("course_id")
        course_document_sources = exam_item.get("course_document_sources") or []
        need_code_repository = flag_enabled(exam_session.get("need_code_repository"))
        use_preset_questions = flag_enabled(exam_session.get("use_preset_questions"))
        if need_code_repository and not str(exam_session.get("repository_url") or "").strip():
            raise ValueError("当前考试需要代码仓库，请先上传仓库")
        judge_config = await get_exam_judge_config_by_exam_id(exam_id)
        if not judge_config:
            raise ValueError("未配置模型参数，请联系管理员")
        exam_run_token = await begin_exam_session(exam_id, str(exam_session.get("user_id") or ""))
        file_local_address = (
            f"{course_id}/{exam_id}/main/doc"
            if need_code_repository and course_id and exam_id
            else None
        )
        code_local_address = (
            f"{course_id}/{exam_id}/main/code"
            if need_code_repository and course_id and exam_id
            else None
        )
        exam_user = {
            **current_user,
            "exam_id": exam_id,
            "exam_type": exam_type,
            "course_id": course_id,
            "exam_item_id": exam_item.get("exam_item_id"),
            "dimensions": dimensions,
            "dimension_scores": exam_item.get("dimension_scores") or {},
            "need_code_repository": need_code_repository,
            "use_preset_questions": use_preset_questions,
            "file_local_address": file_local_address,
            "code_local_address": code_local_address,
            "course_document_sources": course_document_sources,
            "judge_config": judge_config,
        }
    except Exception as exc:
        logger.error("考试启动校验失败，将通过 WebRTC 发送错误提示")
        startup_error = build_startup_error(
            str(exc) or "考试启动失败，请联系老师或稍后重试。",
            exc,
        )
     
    monitor = GlobalMonitor()
     
    history: List[Dict[str, str]] = []

    metrics_frame_processor = MetricsFrameLogger(exam_user, history)
    llm = None
    if exam_type == "A":
        logger.info("Using InterviewServiceA for exam_type=A")
        llm = InterviewServiceA(
            monitor,
            exam_user,
            history,
            startup_error=startup_error,
        )
    elif exam_type == "B":
        logger.info("Using InterviewServiceB for exam_type=B")
        exam_user["exam_type"] = exam_type
        exam_state = CandidateExamState(
            initial_score=DEFAULT_INITIAL_SCORE,
            dimensions=dimensions,
            dimension_scores=(exam_item or {}).get("dimension_scores") or {},
        )
        llm = InterviewServiceB(
            monitor,
            exam_user,
            history,
            exam_state=exam_state,
            startup_error=startup_error,
        )
    else:
        logger.info("Using VoiceLogger/ExamCFlow for exam_type=C")
    ttsaudio = TTSAudio()

    # Create audio buffer processor
    audiobuffer = AudioBufferProcessor(
        num_channels=1,
        enable_turn_audio=True
    )

    context = LLMContext()

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(),
    )


    if exam_type == "C":
        pipeline_steps = [
            transport.input(),
            user_aggregator,
            metrics_frame_processor,
            VoiceLogger(mode="C", exam_id=exam_id),
            assistant_aggregator,
            ttsaudio,
            transport.output(),
            audiobuffer,
        ]
    else:
        pipeline_steps = [
            transport.input(),
            user_aggregator,
            metrics_frame_processor,
            llm,
            assistant_aggregator,
            ttsaudio,
            transport.output(),
            audiobuffer,
        ]

    pipeline = Pipeline(pipeline_steps)

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=100000000,
    )
    monitor.task[exam_user['uuid']] = task



    
    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)

    heartbeat_task = (
        asyncio.create_task(_keep_exam_session_active(exam_id, exam_run_token, task))
        if exam_run_token else None
    )
    try:
        await runner.run(task)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if exam_run_token is not None:
            await end_exam_session(exam_id, exam_run_token)


async def bot(runner_args: RunnerArguments, exam_info: dict, current_user: dict):
    """Main bot entry point compatible with Pipecat Cloud."""
    
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, exam_info, current_user)

app, args = main()
app = auth(app, args)
app = exam_routes(app, args)
app = llm_routes(app, args)


settings = get_settings()
async def setup_monitor(app, args):
    monitor = GlobalMonitor()
    await monitor.start()

    config = uvicorn.Config(app, host="0.0.0.0", port=args.port, ssl_keyfile="./key.pem", ssl_certfile="./cert.pem")
    server = uvicorn.Server(config)
    await server.serve()


# ==================== 文件与会话控制 ====================
# 上传资料文件并写入当前用户的检索数据源。
@app.post("/file/get_chunks")
async def get_chunks(
    course_id: str = Form(...),
    exam_id: str = Form(...),
    current_user: dict = Depends(get_current_user),
    files: List[UploadFile] = File(...),
):
    file_paths = []
    print(current_user)
    upload_work_dir = f"./updateFile/{current_user['uuid']}/{course_id}/{exam_id}"
    for file in files:
        save_dir = "./updateFile"
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        Path(upload_work_dir).mkdir(parents=True, exist_ok=True)
        file_location = f"{upload_work_dir}/{file.filename}"
        file_paths.append(file_location)
    try:
        for file_location in file_paths:
            with open(file_location, "wb+") as file_object:
                    # shutil.copyfileobj 高效地复制文件流
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

# 结束当前用户正在进行的口试任务并清理运行状态。
@app.post("/close")
async def close(current_user: dict = Depends(get_current_user)):
    monitor = GlobalMonitor()
    print(monitor.task)
    task = monitor.task.get(current_user['uuid'])
    if task:
        # await task.queue_frame(EndFrame())
        await task.cancel()
        
        await task.cleanup()
        monitor.task.pop(current_user['uuid'])

if __name__ == "__main__":
    asyncio.run(setup_monitor(app, args))

    # from pipecat.runner.run import main
    # main()
    
