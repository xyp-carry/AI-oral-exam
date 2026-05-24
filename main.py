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

from fastapi import File, UploadFile, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict
from AIOralExamSystem.Tool.rag.data_tool import SearchToolInput, SearchTool, InsertTool
from AIOralExamSystem.utils.monitor import GlobalMonitor
from AIOralExamSystem.Exam.QAserver import QAserver
from AIOralExamSystem.Exam.examSetter import ExamSetterAgent
from AIOralExamSystem.Exam.examObject import CandidateExamState, Question
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


DEFAULT_PREPARED_QUESTION_COUNT = 1
DEFAULT_INITIAL_SCORE = 5.0


class CourseCreateRequest(BaseModel):
    course_name: str
    description: str | None = None


class CourseUpdateRequest(BaseModel):
    course_name: str | None = None
    description: str | None = None


class ExamItemDimension(BaseModel):
    name: str
    score: float


class ExamItemCreateRequest(BaseModel):
    exam_item_name: str
    dimensions: List[ExamItemDimension]
    description: str | None = None
    item_type: str | None = None


class ExamItemUpdateRequest(BaseModel):
    exam_item_name: str | None = None
    dimensions: List[ExamItemDimension] | None = None
    description: str | None = None
    item_type: str | None = None


def course_error_detail(code: str, message: str) -> dict:
    return {
        "code": code,
        "message": message,
    }


def raise_course_value_error(error: ValueError) -> None:
    message = str(error)
    if message == "COURSE_NAME_EXISTS":
        raise HTTPException(
            status_code=400,
            detail=course_error_detail("COURSE_NAME_EXISTS", "课程名称已存在"),
        )
    if message == "COURSE_NAME_REQUIRED":
        raise HTTPException(
            status_code=400,
            detail=course_error_detail("COURSE_NAME_REQUIRED", "课程名称不能为空"),
        )
    if message == "EXAM_ITEM_NAME_EXISTS":
        raise HTTPException(
            status_code=400,
            detail=course_error_detail("EXAM_ITEM_NAME_EXISTS", "考试项名称已存在"),
        )
    if message == "EXAM_ITEM_NAME_REQUIRED":
        raise HTTPException(
            status_code=400,
            detail=course_error_detail("EXAM_ITEM_NAME_REQUIRED", "考试项名称不能为空"),
        )
    if message == "EXAM_ITEM_DIMENSIONS_REQUIRED":
        raise HTTPException(
            status_code=400,
            detail=course_error_detail("EXAM_ITEM_DIMENSIONS_REQUIRED", "考试项维度不能为空"),
        )
    if message == "EXAM_ITEM_DIMENSION_NAME_REQUIRED":
        raise HTTPException(
            status_code=400,
            detail=course_error_detail("EXAM_ITEM_DIMENSION_NAME_REQUIRED", "考试项维度名称不能为空"),
        )
    raise HTTPException(status_code=400, detail=message)


def dimensions_to_scores(dimensions: List[ExamItemDimension] | None) -> Dict[str, float] | None:
    if dimensions is None:
        return None
    return {item.name: item.score for item in dimensions}


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
    question_count: int = DEFAULT_PREPARED_QUESTION_COUNT,
) -> CandidateExamState:
    settings = get_settings()
    exam_setter = ExamSetterAgent(
        settings.model_dump(mode="json"),
        current_user["uuid"],
        thinking=False,
        response_format=True,
        temperature=0,
    )

    response = await exam_setter.run(history=[{
        "role": "user",
        "content": (
            f"请先读取当前用户全部相关资料，然后生成 {question_count} 个初始化口试问题。"
            "这些问题需要覆盖不同考察维度，并用于放入 prepared_question_queue。"
        ),
    }])
    question_doc = parse_outer_json_block(get_agent_response_content(response))
    questions = question_doc.get("questions", [])[:question_count]

    for index, item in enumerate(questions, start=1):
        dimension = str(item.get("dimension", "initial"))
        question = Question(
            question_id=str(item.get("id", f"prepared-{index}")),
            content=str(item.get("Question", item.get("question", ""))).strip(),
            dimension=dimension,
            score=float(item.get("score", 1.0)),
            standard_answer=item.get("standard_answer"),
            based_on_record_index=-1,
            source_detail=str(item.get("reason", question_doc.get("project_summary", ""))),
        )
        if question.content:
            exam_state.add_prepared_question(question)

    logger.info(
        f"Prepared {len(exam_state.prepared_question_queue)} initial questions "
        f"for current user"
    )
    return exam_state


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments, exam_info: dict, current_user: dict):
    logger.info(f"Starting bot")
    
    monitor = GlobalMonitor()
    
    history: List[Dict[str, str]] = []
    exam_state = CandidateExamState(
        initial_score=DEFAULT_INITIAL_SCORE,
    )

    await prepare_initial_questions(
        current_user=current_user,
        exam_state=exam_state,
        question_count=DEFAULT_PREPARED_QUESTION_COUNT,
    )

    metrics_frame_processor = MetricsFrameLogger(history)
    llm = InterviewService(monitor, current_user, history, exam_state=exam_state)
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
    monitor.task[current_user['uuid']] = task

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    
    await runner.run(task)


async def bot(runner_args: RunnerArguments, exam_info: dict, current_user: dict):
    """Main bot entry point compatible with Pipecat Cloud."""
    
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, exam_info, current_user)

app, args = main()
app = auth(app, args)


settings = get_settings()
async def setup_monitor(app, args):
    monitor = GlobalMonitor()
    await monitor.start()

    config = uvicorn.Config(app, host="0.0.0.0", port=args.port, ssl_keyfile="./key.pem", ssl_certfile="./cert.pem")
    server = uvicorn.Server(config)
    await server.serve()

@app.post("/file/get_chunks")
async def get_chunks(current_user: dict = Depends(get_current_user), files: List[UploadFile] = File(...)):
    file_paths = []
    print(current_user)
    for file in files:
        save_dir = "./updateFile"
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        Path(f"{save_dir}/{current_user['uuid']}").mkdir(parents=True, exist_ok=True)
        file_location = f"{save_dir}/{current_user['uuid']}/{file.filename}"
        file_paths.append(file_location)
    try:
        for file_location in file_paths:
            with open(file_location, "wb+") as file_object:
                    # shutil.copyfileobj 高效地复制文件流
                    shutil.copyfileobj(file.file, file_object)
        file_tool = InsertTool("insert_tool", settings.mineru_api_key)
        await file_tool.execute(data=file_paths, source=current_user['uuid'], type="file")
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        raise HTTPException(status_code=500, detail=f"处理文件时出错: {str(e)}")
    
@app.post("/close")
async def close(current_user: dict = Depends(get_current_user)):
    monitor = GlobalMonitor()
    task = monitor.task.get(current_user['uuid'])
    if task:
        await task.cancel()
        await task.cleanup()
        monitor.task.pop(current_user['uuid'])


@app.get("/exam_history")
async def exam_history(current_user: dict = Depends(get_current_user)):
    try:
        history = await QAserver.get_exam_history(current_user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("查询考试历史失败")
        raise HTTPException(status_code=500, detail=f"查询考试历史失败: {str(e)}")
    return {
        "success": True,
        "data": history,
    }


@app.get("/exam_record")
async def exam_record(exam_id: str, current_user: dict = Depends(get_current_user)):
    try:
        records = await QAserver.get_exam_record(current_user, exam_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("查询考试记录失败")
        raise HTTPException(status_code=500, detail=f"查询考试记录失败: {str(e)}")
    return {
        "success": True,
        "data": records,
    }


@app.post("/courses")
async def create_course(req: CourseCreateRequest, current_user: dict = Depends(get_current_user)):
    try:
        result = await QAserver.create_course(
            current_user=current_user,
            course_name=req.course_name,
            description=req.description,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
    except ValueError as e:
        raise_course_value_error(e)
    except Exception as e:
        logger.exception("创建课程失败")
        raise HTTPException(status_code=500, detail=f"创建课程失败: {str(e)}")
    return {
        "success": True,
        "message": "课程创建成功",
        "data": result,
    }


@app.get("/courses")
async def list_courses(current_user: dict = Depends(get_current_user)):
    try:
        courses = await QAserver.list_courses(current_user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
    except Exception as e:
        logger.exception("查询课程失败")
        raise HTTPException(status_code=500, detail=f"查询课程失败: {str(e)}")
    return {
        "success": True,
        "data": courses,
    }


@app.put("/courses/{course_id}")
async def update_course(
    course_id: str,
    req: CourseUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        updated = await QAserver.update_course(
            current_user=current_user,
            course_id=course_id,
            course_name=req.course_name,
            description=req.description,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
    except ValueError as e:
        raise_course_value_error(e)
    except Exception as e:
        logger.exception("修改课程失败")
        raise HTTPException(status_code=500, detail=f"修改课程失败: {str(e)}")
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=course_error_detail("COURSE_NOT_FOUND", "课程不存在或无权修改"),
        )
    return {
        "success": True,
        "message": "课程修改成功",
    }


@app.delete("/courses/{course_id}")
async def delete_course(course_id: str, current_user: dict = Depends(get_current_user)):
    try:
        deleted = await QAserver.delete_course(current_user, course_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
    except Exception as e:
        logger.exception("删除课程失败")
        raise HTTPException(status_code=500, detail=f"删除课程失败: {str(e)}")
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=course_error_detail("COURSE_NOT_FOUND", "课程不存在或无权删除"),
        )
    return {
        "success": True,
        "message": "课程删除成功",
    }


@app.post("/courses/{course_id}/exam_items")
async def create_exam_item(
    course_id: str,
    req: ExamItemCreateRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await QAserver.create_exam_item(
            current_user=current_user,
            course_id=course_id,
            exam_item_name=req.exam_item_name,
            dimension_scores=dimensions_to_scores(req.dimensions) or {},
            description=req.description,
            item_type=req.item_type,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
    except ValueError as e:
        raise_course_value_error(e)
    except Exception as e:
        logger.exception("创建考试项失败")
        raise HTTPException(status_code=500, detail=f"创建考试项失败: {str(e)}")
    return {
        "success": True,
        "message": "考试项创建成功",
        "data": result,
    }


@app.get("/courses/{course_id}/exam_items")
async def list_exam_items(course_id: str, current_user: dict = Depends(get_current_user)):
    try:
        items = await QAserver.list_exam_items(current_user, course_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
    except Exception as e:
        logger.exception("查询考试项失败")
        raise HTTPException(status_code=500, detail=f"查询考试项失败: {str(e)}")
    return {
        "success": True,
        "data": items,
    }


@app.put("/courses/{course_id}/exam_items/{exam_item_id}")
async def update_exam_item(
    course_id: str,
    exam_item_id: str,
    req: ExamItemUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        updated = await QAserver.update_exam_item(
            current_user=current_user,
            course_id=course_id,
            exam_item_id=exam_item_id,
            exam_item_name=req.exam_item_name,
            dimension_scores=dimensions_to_scores(req.dimensions),
            description=req.description,
            item_type=req.item_type,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
    except ValueError as e:
        raise_course_value_error(e)
    except Exception as e:
        logger.exception("修改考试项失败")
        raise HTTPException(status_code=500, detail=f"修改考试项失败: {str(e)}")
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=course_error_detail("EXAM_ITEM_NOT_FOUND", "考试项不存在或无权修改"),
        )
    return {
        "success": True,
        "message": "考试项修改成功",
    }


@app.delete("/courses/{course_id}/exam_items/{exam_item_id}")
async def delete_exam_item(
    course_id: str,
    exam_item_id: str,
    current_user: dict = Depends(get_current_user),
):
    try:
        deleted = await QAserver.delete_exam_item(current_user, course_id, exam_item_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
    except Exception as e:
        logger.exception("删除考试项失败")
        raise HTTPException(status_code=500, detail=f"删除考试项失败: {str(e)}")
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=course_error_detail("EXAM_ITEM_NOT_FOUND", "考试项不存在或无权删除"),
        )
    return {
        "success": True,
        "message": "考试项删除成功",
    }


if __name__ == "__main__":
    asyncio.run(setup_monitor(app, args))

    # from pipecat.runner.run import main
    # main()
    
