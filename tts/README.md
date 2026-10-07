# TTS package

C-mode uses this package in the main.py Pipecat pipeline.

- A backend yields AudioPayload objects. Add a provider by implementing TTSBackend.
- AudioNormalizer decodes PCM WAV, PCM16LE, or float32 PCM; mixes to mono, resamples, and emits PCM16LE chunks.
- TTSFrameProcessor consumes TTSSpeakFrame and sends OutputAudioRawFrame to the next Pipecat processor in utterance order.
- The output sample rate should match the selected transport. A local model can return AudioPayload, WAV/PCM bytes, a NumPy array, a Torch tensor, or an iterator of those. Use a custom converter for other formats.
- The MiniMax API key is read from settings or MINIMAX_API_KEY; no key is embedded in this package.

Example standalone construction:

    backend = create_tts_backend({"provider": "minimax", "sample_rate": 32000})
    processor = TTSFrameProcessor(backend, output_sample_rate=32000)

For a local model, pass a callable accepting TTSRequest as local_synthesizer. New provider formats should be handled inside their backend adapter or an added decoder, leaving the processor unchanged.

## C-mode model binding

C-mode loads its TTS backend from the exam item's bound model, as it does for
Agent models. Create a model through `POST /models` with
`provider="volcengine"`, `provider_model_key="seed-tts-2.0"`,
`model_type="tts"`, and a Volcengine API key. The default voice is
`zh_male_jieshuoxiaoming_uranus_bigtts` (解说小明 2.0). Set
`params.speaker` for another voice and `params.speech_rate` for speed
(an integer from -50 to 100; default 0). `params.sample_rate` controls the
source audio rate. Existing models with `voice_id` still work. The default
HTTP Chunked endpoint uses `model_api_key` as its API Key. Saved WebSocket
configurations continue to use their stored endpoint; an old-console
WebSocket configuration may also provide `params.app_id`.

Bind the returned `model_id` with
`PUT /courses/{course_id}/exam_items/{exam_item_id}/agents` using
`{"tts_model_id": "<model_id>"}`. C-mode readiness requires this binding.
The TTS stream uses the saved model key and settings, so `DOUBAO_API_KEY` is
not needed for this path.


## Saved synthesis audio

C-mode streams each TTSSpeakFrame to the caller and, after its synthesis
finishes successfully, saves one WAV file for that complete utterance under
`exam_records/c_mode/tts/<exam_id>/`. The WAV contains the 32 kHz mono
PCM sent to the transport. Failed or interrupted utterances are not saved.
The manual smoke test (`python -m tts.test`) also saves one uniquely named
WAV per run; use `--output` to choose an unused path.
