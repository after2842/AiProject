"""
Realtime STT → GPT‑4o → TTS (no Agents SDK)
-------------------------------------------------
• Captures mic at 24 kHz mono PCM16 and streams to the **Realtime** WebSocket API.
• Uses **server‑side VAD** (turn detection) so you don't need to press keys.
• On each detected user turn, creates a model response with **text + audio modalities**.
• Prints streamed text deltas and plays streamed **PCM16** audio as they arrive.

Requirements
------------
python -m pip install websockets sounddevice numpy

Environment
-----------
Set your key:  export OPENAI_API_KEY=sk-...

Notes
-----
• We configure server VAD via a `session.update` message using `turn_detection: {type: "server_vad"}`.
• Audio input/output format is **pcm16 @ 24000 Hz** to match model defaults.
• This uses the Realtime WebSocket endpoint (part of the Responses API family) directly.
"""

import os
import asyncio
import json
import base64
import signal
import queue
from typing import Optional

import numpy as np
import sounddevice as sd
import websockets

API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL   = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
WS_URL  = f"wss://api.openai.com/v1/realtime?model={MODEL}"

# ---- Audio constants ----
SR        = 24_000                 # sample rate expected by realtime STT
CHANNELS  = 1
DTYPE     = "int16"
BLOCKSIZE = int(SR * 0.02)         # 20 ms chunks → 480 samples

# ---- Simple PCM16 speaker ----
class Speaker:
    def __init__(self, samplerate: int = SR):
        self._out = sd.OutputStream(samplerate=samplerate, channels=1, dtype=DTYPE)
        self._out.start()
        print("[speaker] ready")

    def play_pcm16(self, pcm_bytes: bytes):
        if not pcm_bytes:
            return
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        self._out.write(pcm)

    def close(self):
        try:
            self._out.stop(); self._out.close()
        except Exception:
            pass

# ---- Mic → async queue (PCM16 @ 24k) ----
async def mic_producer(q: "queue.Queue[bytes]", stop_evt: asyncio.Event):
    def _cb(indata, frames, time, status):
        if status:
            print("[mic][status]", status)
        q.put(indata.copy().tobytes())  # raw PCM16 bytes

    try:
        stream = sd.InputStream(samplerate=SR, channels=1, dtype=DTYPE, blocksize=BLOCKSIZE, callback=_cb)
        stream.start()
        print(f"[mic] opened @ {SR} Hz PCM16")
        loop = asyncio.get_running_loop()
        while not stop_evt.is_set():
            try:
                # block in a thread so we don't busy-wait
                chunk = await loop.run_in_executor(None, q.get)
                # small sleep to batch 20ms frames smoothly
                await asyncio.sleep(0)
            except Exception:
                break
    finally:
        try:
            stream.stop(); stream.close()
        except Exception:
            pass
        print("[mic] closed")

# ---- WebSocket client ----
async def run_session():
    if not API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")

    # Prepare audio IO
    speaker = Speaker()
    stop_evt = asyncio.Event()
    mic_q: "queue.Queue[bytes]" = queue.Queue(maxsize=64)

    # Connect WS
    async with websockets.connect(
        WS_URL,
        additional_headers=[
            ("Authorization", f"Bearer {API_KEY}"),
            ("OpenAI-Beta", "realtime=v1"),
        ],
        subprotocols=["realtime"],
    ) as ws:
        print("[ws] connected")

        # Configure session: server‑side VAD + input audio format
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "turn_detection": {"type": "server_vad"},
                # "input_audio_format": "pcm16",
                # "input_audio_sample_rate_hz": SR,
            },
        }))

        # Start mic task that feeds audio to WS
        async def mic_sender():
            print("[mic] sender started")
            loop = asyncio.get_running_loop()
            while not stop_evt.is_set():
                try:
                    pcm = await loop.run_in_executor(None, mic_q.get)
                except Exception:
                    break
                # Encode to base64 and send append event
                b64 = base64.b64encode(pcm).decode("ascii")
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": b64,
                }))
                # Yield to allow receiving
                await asyncio.sleep(0)

        # Auto‑create a response after server VAD ends the turn
        # We do this when we see the buffer get committed.
        async def maybe_create_response():
            await ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                    "instructions": "Be concise.",
                    "audio": {"voice": "alloy", "format": "pcm16"},
                },
            }))

        # Receiver: handle streaming text + audio, and trigger response on VAD commit
        async def receiver():
            print("[ws] receiver started")
            text_buf = ""
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    print("[ws] non‑JSON frame")
                    continue

                t = msg.get("type")
                if t == "response.output_text.delta":
                    delta = msg.get("delta", "")
                    text_buf += delta
                    print(delta, end="", flush=True)
                elif t == "response.output_text.done":
                    print()  # newline after final text
                elif t == "response.audio.delta":
                    # base64 PCM16 chunk
                    b64 = msg.get("delta", "")
                    speaker.play_pcm16(base64.b64decode(b64))
                elif t == "response.completed":
                    text_buf = ""
                elif t == "input_audio_buffer.committed":
                    # Server VAD decided a turn ended → ask for a response
                    await maybe_create_response()
                elif t == "error":
                    print("\n[ws][error]", msg)
                # else: you can log other events for debugging

        # Task orchestration
        mic_task = asyncio.create_task(mic_producer(mic_q, stop_evt))
        send_task = asyncio.create_task(mic_sender())
        recv_task = asyncio.create_task(receiver())

        # Graceful shutdown on Ctrl+C
        loop = asyncio.get_running_loop()
        stop_fut = loop.create_future()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_fut.set_result, None)

        await stop_fut
        stop_evt.set()

        # Finalize input on server (optional: commit any trailing audio)
        try:
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        except Exception:
            pass

        # Allow tasks to wind down
        await asyncio.sleep(0.2)
        for t in (mic_task, send_task, recv_task):
            t.cancel()
        speaker.close()
        print("[ws] closed")

if __name__ == "__main__":
    try:
        asyncio.run(run_session())
    except KeyboardInterrupt:
        pass
