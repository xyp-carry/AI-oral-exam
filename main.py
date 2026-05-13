#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Audio Recording Example with Pipecat.

This example demonstrates how to record audio from a conversation between a user and an AI assistant,
saving both merged and individual audio tracks. It showcases the AudioBufferProcessor's capabilities
to handle both combined and separate audio streams.

The example:
    1. Sets up a basic conversation with an AI assistant
    2. Records the entire conversation
    3. Saves three separate WAV files:
        - A merged recording of both participants
        - Individual recording of user audio
        - Individual recording of assistant audio

Requirements:
    - OpenAI API key (for GPT-4)
    - Cartesia API key (for text-to-speech)
    - Daily API key (for video/audio transport)

    Environment variables (.env file):
        OPENAI_API_KEY=your_openai_key
        CARTESIA_API_KEY=your_cartesia_key
        DAILY_API_KEY=your_daily_key
        DEEPGRAM_API_KEY=your_deepgram_key

The recordings will be saved in a 'recordings' directory with timestamps:
    recordings/
        merged_20240315_123456.wav  (Combined audio)
        user_20240315_123456.wav    (User audio only)
        bot_20240315_123456.wav     (Bot audio only)

Note:
    This example requires the AudioBufferProcessor with track-specific audio support,
    which provides both 'on_audio_data' and 'on_track_audio_data' events for
    handling merged and separate audio tracks respectively.
"""

import datetime
import os

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
from OralService.OralLLMService import LLMService
from OralService.OralSTTService import MetricsFrameLogger
from OralService.OralTTSService import TTSAudio
from server import main
import uvicorn
import asyncio

from fastapi import File, UploadFile, HTTPException, Depends
from typing import List, Dict
from AIOralExamSystem.Tool.rag.data_tool import SearchToolInput, SearchTool, InsertTool
from AIOralExamSystem.utils.monitor import GlobalMonitor
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


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments, current_user: dict):
    logger.info(f"Starting bot")
    print("XXX", current_user)
    monitor = GlobalMonitor()

    history: List[Dict[str, str]] = []
    metrics_frame_processor = MetricsFrameLogger(history)
    llm = LLMService(monitor, current_user, history)
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


async def bot(runner_args: RunnerArguments, current_user: dict):
    """Main bot entry point compatible with Pipecat Cloud."""
    
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, current_user)

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
        save_dir = "./File"
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

if __name__ == "__main__":
    asyncio.run(setup_monitor(app, args))

    # from pipecat.runner.run import main
    # main()
    