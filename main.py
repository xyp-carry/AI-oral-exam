from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
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
from OralService.OralinterviewService import InterviewService
from OralService.OralSTTService import MetricsFrameLogger
from OralService.OralTTSService import TTSAudio
from server import main
import uvicorn
import asyncio
import json
import re

from fastapi import File, UploadFile, HTTPException, Depends, Form
from typing import List, Dict
from AIOralExamSystem.Tool.rag.data_tool import SearchToolInput, SearchTool, InsertTool
from AIOralExamSystem.utils.monitor import GlobalMonitor
from AIOralExamSystem.Exam.Examdata import get_available_exam_item_by_exam_id, get_exam_session_by_exam_id
from AIOralExamSystem.Exam.examSetter import ExamSetterAgent
from AIOralExamSystem.Exam.examObject import CandidateExamState, Question
from AIOralExamSystem.url import exam_routes
from config import get_settings
from pathlib import Path
import shutil
from Authentication.main import auth
from Authentication.auth import get_current_user

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


def parse_outer_json_block(response: str) -> dict:
    text = response.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text, count=1)
        text = re.sub(r"\n?```\s*$", "", text, count=1)

    if not text.startswith("{"):
        obj_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not obj_match:
            raise ValueError("No JSON object found in model response")
        text = obj_match.group(0)

    return json.loads(text)


def get_agent_response_content(response) -> str:
    message = response["messages"][-1]
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, list):
        return "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    return str(content)


async def prepare_initial_questions(
    current_user: dict,
    exam_state: CandidateExamState,
) -> CandidateExamState:
    question_dimensions = exam_state.get_configured_dimensions()
    if not question_dimensions:
        raise ValueError("当前考试项没有配置考试维度")
    question_count = len(question_dimensions)
    settings = get_settings()
    course_id = current_user.get("course_id")
    exam_id = current_user.get("exam_id")
    file_local_address = current_user.get("file_local_address") or (
        f"{course_id}/{exam_id}" if course_id and exam_id else None
    )
    code_local_address = current_user.get("code_local_address") or (
        f"{course_id}/{exam_id}/main" if course_id and exam_id else None
    )
    exam_setter = ExamSetterAgent(
        settings.model_dump(mode="json"),
        current_user["uuid"],
        thinking=False,
        response_format=True,
        temperature=0,
        question_count=question_count,
        question_dimensions=question_dimensions,
        course_id=course_id,
        exam_id=exam_id,
        file_local_address=file_local_address,
        code_local_address=code_local_address,
    )

    response = await exam_setter.run(
        history=[{
            "role": "user",
            "content": (
                f"这是首次生成题目。请先调用 search，并使用 query=\"\" 读取当前用户全部相关资料，"
                f"读完所需批次后生成 {question_count} 个初始化口试问题。"
                "题目必须依次覆盖已配置的考试维度，并用于放入 prepared_question_queue。"
            ),
        }],
        question_count=question_count,
        question_dimensions=question_dimensions,
        is_initial_generation=True,
    )
    question_doc = parse_outer_json_block(get_agent_response_content(response))
    questions = question_doc.get("questions", [])[:question_count]
    if len(questions) != question_count:
        raise ValueError("初始题目数量与考试维度数量不一致")

    for index, item in enumerate(questions, start=1):
        dimension = question_dimensions[index - 1]
        content = str(item.get("Question", item.get("question", ""))).strip()
        if not content:
            raise ValueError(f"维度 {dimension} 未生成有效初始题目")
        question = Question(
            question_id=str(item.get("id", f"prepared-{index}")),
            content=content,
            dimension=dimension,
            question_blocks=item.get("question_blocks", []),
            code_fragments=item.get("code_fragments", []),
            score=float(item.get("score", 1.0)),
            standard_answer=item.get("standard_answer"),
            based_on_record_index=-1,
            source_detail=str(item.get("reason", question_doc.get("project_summary", ""))),
        )
        exam_state.add_prepared_question(question)

    logger.info(
        f"Prepared {len(exam_state.prepared_question_queue)} initial questions "
        f"for current user"
    )
    return exam_state


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments, exam_info: dict, current_user: dict):
    logger.info(f"Starting bot")

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
    dimensions = exam_item.get("dimension_names") or []
    if not dimensions:
        raise ValueError("当前考试项没有配置考试维度")
    course_id = exam_item.get("course_id")
    file_local_address = f"{course_id}/{exam_id}" if course_id and exam_id else None
    code_local_address = f"{course_id}/{exam_id}/main" if course_id and exam_id else None
    exam_user = {
        **current_user,
        "exam_id": exam_id,
        "course_id": course_id,
        "file_local_address": file_local_address,
        "code_local_address": code_local_address,
    }
    
    monitor = GlobalMonitor()
    
    history: List[Dict[str, str]] = []
    exam_state = CandidateExamState(
        initial_score=DEFAULT_INITIAL_SCORE,
        dimensions=dimensions,
    )



    metrics_frame_processor = MetricsFrameLogger(history)
    llm = InterviewService(monitor, exam_user, history, exam_state=exam_state)
    ttsaudio = TTSAudio()

    # Create audio buffer processor
    audiobuffer = AudioBufferProcessor(
        num_channels=1,
        enable_turn_audio=True
    )

    context = LLMContext()
    # user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
    #     context,
    #     user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    # )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(),
    )
    pipeline = Pipeline(
        [
            transport.input(),
            metrics_frame_processor,
            user_aggregator,
            llm,
            assistant_aggregator,
            ttsaudio,
            transport.output(),
            audiobuffer,
              # Add audio buffer to pipeline
        ]
    )

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
    
    await prepare_initial_questions(
        current_user=exam_user,
        exam_state=exam_state,
    )
    await runner.run(task)


async def bot(runner_args: RunnerArguments, exam_info: dict, current_user: dict):
    """Main bot entry point compatible with Pipecat Cloud."""
    
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, exam_info, current_user)

app, args = main()
app = auth(app, args)
app = exam_routes(app, args)


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
        file_tool = InsertTool("insert_tool", settings.mineru_api_key)
        await file_tool.execute(
            data=file_paths,
            source=current_user['uuid'],
            type="file",
            course_id=course_id,
            exam_id=exam_id,
            work_dir=upload_work_dir,
        )
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
    
