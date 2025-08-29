"""
OpenAI Agents SDK — VoicePipeline mic streaming (robust & instrumented)

What this does
--------------
• Captures microphone audio with `sounddevice`.
• Ensures audio fed to the SDK is **PCM16 mono at 24 kHz** (what the STT expects).
• Streams audio chunks into `StreamedAudioInput`.
• Runs a `VoicePipeline` with explicit **STT** and **TTS** models.
• Prints lifecycle + audio event diagnostics and plays TTS audio.
• Graceful shutdown on Ctrl+C, and sends an end-of-stream sentinel to the pipeline.

Why these choices
-----------------
• The SDK labels input as `pcm16` on the realtime STT websocket. So we must
  deliver **np.int16** buffers (not float32) and at the expected sample rate.
• We open the mic at 24kHz if possible; otherwise resample.
• We add a tiny `on_start` greeting so you can immediately see events emit.

Run:
  python -u voice_pipeline_stream.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import signal
from dataclasses import dataclass
from typing import Any, AsyncIterator

import numpy as np
import sounddevice as sd

from agents import Agent, function_tool
from agents.extensions.handoff_prompt import prompt_with_handoff_instructions
from agents.voice import (
    VoicePipeline,
    SingleAgentVoiceWorkflow,
    StreamedAudioInput,
    VoicePipelineConfig,
)

# ------------------------ Logging ------------------------
# Make the SDK chatty so you can see STT/TTS state transitions.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")
# You can uncomment these for deeper SDK logs:
# logging.getLogger("agents.voice").setLevel(logging.DEBUG)
# logging.getLogger("agents.voice.models.openai_stt").setLevel(logging.DEBUG)

# ------------------------ Tool ------------------------

@function_tool
def get_weather(city: str) -> str:
    """Get the weather for a given city."""
    import random
    logger.info(f"[tool] get_weather({city!r})")
    choices = ["sunny", "cloudy", "rainy", "snowy"]
    return f"The weather in {city} is {random.choice(choices)}."

# ------------------------ Agents ------------------------

# korean_agent = Agent(
#     name="Korean",
#     handoff_description="A Korean speaking agent.",
#     instructions=prompt_with_handoff_instructions(
#         "You're speaking to a human, so be polite and concise. Speak in Korean.",
#     ),
#     model="gpt-4o-mini",
#     tools=[get_weather],
# )

agent = Agent(
    name="Assistant",
    instructions=prompt_with_handoff_instructions(
        "You're speaking to a human, so be polite and concise. "
        "If the user speaks in Korean, handoff to the Korean agent.",
    ),
    model="gpt-4o-mini",
    # handoffs=[korean_agent],
    # tools=[get_weather],
)

# ------------------------ Workflow with a greeting ------------------------

class HelloOnStartWorkflow(SingleAgentVoiceWorkflow):
    async def on_start(self) -> AsyncIterator[str]:
        # Optional: greet so we get immediate TTS events even before you speak.
        yield "안녕하세요! I'm listening. Say something and I'll respond."

# ------------------------ Audio capture helpers ------------------------

TARGET_SR = 24_000  # VoicePipeline/STT expects 24 kHz
CHANNELS = 1
DTYPE = "int16"     # Feed PCM16 (STT session advertises `pcm16`)
BLOCKSIZE = 2048


def _linear_resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or x.size == 0:
        return x
    n_out = int(round(x.size * sr_to / sr_from))
    xp = np.linspace(0.0, 1.0, num=x.size, endpoint=False, dtype=np.float32)
    xq = np.linspace(0.0, 1.0, num=n_out, endpoint=False, dtype=np.float32)
    return np.interp(xq, xp, x).astype(np.float32, copy=False)


async def pump_mic_to_streamed_input(stream_in: StreamedAudioInput, stop_evt: asyncio.Event):
    """Capture mic → send **PCM16 @ 24 kHz mono** chunks into the SDK.

    We ensure dtype=int16. If the device cannot open at 24kHz, we resample.
    """
    q: "queue.Queue[np.ndarray]" = queue.Queue()

    def _cb(indata, frames, time, status):
        if status:
            print("[mic][status]", status, flush=True)
        q.put(indata.copy())  # shape: (frames, 1), dtype=int16

    # Try opening the mic at 24 kHz; fall back to device default if needed.
    try:
        sr = TARGET_SR
        stream = sd.InputStream(
            samplerate=sr,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=_cb,
        )
        stream.start()
        print(f"[mic] opened at {sr} Hz")
    except Exception as e:
        info = sd.query_devices(sd.default.device[0])
        sr = int(info.get("default_samplerate", 48_000))
        print(f"[mic] {TARGET_SR} Hz not supported, falling back to {sr} Hz ({e!r})")
        stream = sd.InputStream(
            samplerate=sr,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=_cb,
        )
        stream.start()

    loop = asyncio.get_running_loop()

    try:
        while not stop_evt.is_set():
            data_i16 = await loop.run_in_executor(None, q.get)  # (frames, 1) int16
            # quick input meter (RMS in int16 units)
            rms = float(np.sqrt(np.mean((data_i16.astype(np.float32)) ** 2)))
            print(f"[mic] frames={len(data_i16):4d}  rms={rms:7.1f}", flush=True)

            mono_i16 = data_i16.reshape(-1)  # flatten to 1-D

            if sr != TARGET_SR:
                # resample in float32, then dither/clip back to int16
                f32 = mono_i16.astype(np.float32) / 32768.0
                f32 = _linear_resample(f32, sr_from=sr, sr_to=TARGET_SR)
                mono_i16 = np.clip(f32 * 32767.0, -32768, 32767).astype(np.int16)

            # CRITICAL: send **int16** buffers; STT session labels them as pcm16
            await stream_in.add_audio(mono_i16)
    finally:
        try:
            stream.stop(); stream.close()
        except Exception:
            pass
        print("[mic] stopped")


# ------------------------ Playback helper ------------------------

class Speaker:
    def __init__(self, samplerate=TARGET_SR, channels=CHANNELS, dtype=DTYPE):
        self._out = sd.OutputStream(samplerate=samplerate, channels=channels, dtype=dtype)
        self._out.start()
        print("Speaker initialized")

    def play(self, audio_np: np.ndarray):
        """Accept int16 or float32 mono arrays; convert to int16 for playback if needed."""
        if audio_np.dtype == np.float32:
            pcm = np.clip(audio_np.squeeze() * 32767.0, -32768, 32767).astype(np.int16)
        elif audio_np.dtype == np.int16:
            pcm = audio_np
        else:
            raise TypeError(f"Unsupported dtype: {audio_np.dtype}")
        self._out.write(pcm)

    def close(self):
        self._out.stop()
        self._out.close()


# ------------------------ Main ------------------------

def _supports(obj, name):
    return hasattr(obj, name) and callable(getattr(obj, name))


async def main():
    # VoicePipeline with explicit model names keeps behavior predictable.
    config = VoicePipelineConfig()  # you can tweak stt_settings.turn_detection here if needed
    pipeline = VoicePipeline(
        workflow=HelloOnStartWorkflow(agent),
        stt_model="gpt-4o-mini-transcribe",  # or "gpt-4o-transcribe"
        tts_model="gpt-4o-mini-tts",        # or "tts-1", "tts-1-hd"
        config=config,
    )

    # Create input stream (class has no ctor args today; we configure via how we feed it)
    try:
        streamed_input = StreamedAudioInput()
    except TypeError:
        streamed_input = StreamedAudioInput()  # fallback; class signature may evolve

    stop_evt = asyncio.Event()
    print("streamed input created")

    # Start mic → streamed input producer task
    mic_task = asyncio.create_task(pump_mic_to_streamed_input(streamed_input, stop_evt))
    print("mic_task created")

    print("[pipeline] starting")
    result = await pipeline.run(streamed_input)

    # Small probe: expect the first event soon (greeting or initial state)
    async def expect_first_event(result, timeout=8):
        agen = result.stream()
        try:
            evt = await asyncio.wait_for(agen.__anext__(), timeout)
            print("[first evt]", getattr(evt, "type", type(evt)))
        except asyncio.TimeoutError:
            print("[diag] No events within 8s → check API key, model names, or input format.")
            return False
        return True

    await expect_first_event(result)

    # Stream back BOTH voice (to speakers) and text (to terminal)
    speaker = Speaker()
    last_len = 0  # track how much text we've printed so far

    async def consume_events():
        print("consume_events called")
        nonlocal last_len
        async for event in result.stream():
            etype = getattr(event, "type", None)
            print(f"[evt] {etype!r}", flush=True)

            # If event has raw audio data, show its shape/dtype for sanity.
            if hasattr(event, "data"):
                try:
                    print(
                        f"[evt.data] shape={getattr(event.data,'shape',None)} "
                        f"dtype={getattr(event.data,'dtype',None)}",
                        flush=True,
                    )
                except Exception:
                    pass

            if etype == "voice_stream_event_audio":
                # event.data is an np.ndarray (int16 or float32)
                speaker.play(event.data)
            elif etype == "voice_stream_event_lifecycle":
                ev = getattr(event, "event", "")
                print(f"[lifecycle] {ev}", flush=True) 
                if getattr(event, "event", "") == "turn_started":
                    print("\n[assistant] (speaking...)")
                if getattr(event, "event", "") == "turn_ended":
                    new_text = result.total_output_text[last_len:]
                    if new_text.strip():
                        print(f"[assistant text] {new_text.strip()}")
                    last_len = len(result.total_output_text)
                if getattr(event, "event", "") == "session_ended":
                    print("[pipeline] session ended")

    consumer_task = asyncio.create_task(consume_events())

    # Graceful shutdown on Ctrl+C
    loop = asyncio.get_running_loop()
    stop_future = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_future.set_result, None)

    await stop_future
    stop_evt.set()

    # Signal end-of-stream to STT (the SDK looks for a None buffer)
    try:
        streamed_input.queue.put_nowait(None)  # type: ignore[arg-type]
    except Exception:
        pass

    # Let everything wind down
    await asyncio.sleep(0.2)
    consumer_task.cancel()
    mic_task.cancel()
    speaker.close()
    print("[main] done")


if __name__ == "__main__":
    # Sanity check: make sure the API key is present
    import os
    if not os.environ.get("OPENAI_API_KEY"):
        print("[warn] OPENAI_API_KEY is not set in environment. The STT/TTS connection will fail.")
    asyncio.run(main())
