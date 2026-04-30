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
from pipecat.frames.frames import LLMRunFrame
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


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info(f"Starting bot")


    metrics_frame_processor = MetricsFrameLogger()
    llm = LLMService()
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
            # stt,
            metrics_frame_processor,
            # llm,
            user_aggregator,
            llm,
            ttsaudio,
            transport.output(),
            audiobuffer,  # Add audio buffer to pipeline
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Client connected")
        # Start recording audio
        await audiobuffer.start_recording()
        # Start conversation - empty prompt to let LLM follow system instructions
        await task.queue_frames([LLMRunFrame()])

    # @transport.event_handler("on_client_disconnected")
    # async def on_client_disconnected(transport, client):
    #     logger.info(f"Client disconnected")
    #     await task.cancel()
    
    # @audiobuffer.event_handler("on_user_turn_audio_data")
    # async def on_user_turn_audio_data(buffer, audio: bytes, sample_rate: int, num_channels: int):
    
    #     timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    #     filename = f"recordings1/{timestamp}.wav"
    #     os.makedirs("recordings1", exist_ok=True)
    #     print(type(audio), len(audio))
    #     await save_audio_file(audio, filename, sample_rate, num_channels)
    #     with open(filename, "rb") as f:
    #         resp = requests.post(
    #     "http://localhost:7777/asr" ,
    #     files={"audio": (filename, f, "audio/wav")},
    #     data={"language": "auto"},
    # )

    #     print(resp.json())


    
    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recordings/merged_{timestamp}.wav"
        os.makedirs("recordings", exist_ok=True)
        await save_audio_file(audio, filename, sample_rate, num_channels)

    # Handler for separate tracks
    @audiobuffer.event_handler("on_track_audio_data")
    async def on_track_audio_data(buffer, user_audio, bot_audio, sample_rate, num_channels):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("recordings", exist_ok=True)

        # Save user audio
        user_filename = f"recordings/user_{timestamp}.wav"
        await save_audio_file(user_audio, user_filename, sample_rate, 1)

        # Save bot audio
        bot_filename = f"recordings/bot_{timestamp}.wav"
        await save_audio_file(bot_audio, bot_filename, sample_rate, 1)

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point compatible with Pipecat Cloud."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()