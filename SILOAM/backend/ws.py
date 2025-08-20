# server.py
# Requirements:
#   pip install fastapi uvicorn "httpx>=0.24" webrtcvad
# Env:
#   export ELEVEN_API_KEY=YOUR_KEY

import os
import webrtcvad
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import httpx
import openai

# -------- Config --------
ELEVEN_API_KEY = "sk_9239cb215c9a6b316962c1602071d352c8023e7f0e55697c"
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"

SR = 16000                 # 16 kHz
FRAME_MS = 20              # 20 ms frames
FRAME_BYTES = int(SR * FRAME_MS / 1000) * 2   # 320 samples * 2 bytes = 640
PADDING_MS = 300           # VAD hysteresis window (start/end padding)
VAD_MODE = 3               # 0..3 (higher = stricter)

# -------- App --------
app = FastAPI()

@app.get("/health")
def health():
    return JSONResponse({"ok": True})

async def transcribe_pcm_16k_mono(pcm_bytes: bytes) -> dict:
    """Send raw 16k mono 16-bit PCM to ElevenLabs Scribe v1."""
    print("transcribe_pcm_16k_mono❤️")
    headers = {"xi-api-key": ELEVEN_API_KEY}
    files = {
        # (filename, bytes, mimetype) — raw PCM is fine as octet-stream
        "file": ("audio.pcm", pcm_bytes, "application/octet-stream"),
    }
    data = {
        "model_id": "scribe_v1",
        "file_format": "pcm_s16le_16",  # raw 16kHz mono 16-bit little-endian PCM
        # Optional:
        # "language_code": "en",
        # "timestamps_granularity": "word",
        # "diarize": "false",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(STT_URL, headers=headers, data=data, files=files)
        r.raise_for_status()
        return r.json()

class VadSegmenter:
    """
    Feed exact-size frames (20 ms @ 16kHz, s16le). When an utterance ends,
    returns the full PCM segment (bytes). Otherwise returns None.
    """
    def __init__(self, sr=SR, frame_ms=FRAME_MS, pad_ms=PADDING_MS, mode=VAD_MODE):
        self.vad = webrtcvad.Vad(mode)
        self.frame_bytes = int(sr * frame_ms / 1000) * 2
        self.pad_frames = int(pad_ms / frame_ms)  # e.g., 300/20 = 15
        self.ring = deque(maxlen=self.pad_frames)
        self.triggered = False
        self.voiced = bytearray()

    def push(self, frame: bytes):
        assert len(frame) == self.frame_bytes
        is_speech = self.vad.is_speech(frame, SR)

        if not self.triggered:
            self.ring.append((frame, is_speech))
            # Enter TRIGGERED when >90% of the window is voiced
            if self.ring.maxlen and sum(1 for _, s in self.ring if s) > 0.9 * self.ring.maxlen:
                self.triggered = True
                for f, _ in self.ring:  # leading padding
                    self.voiced.extend(f)
                self.ring.clear()
        else:
            self.voiced.extend(frame)
            self.ring.append((frame, is_speech))
            # Exit when >90% of the window is unvoiced
            if self.ring.maxlen and sum(1 for _, s in self.ring if not s) > 0.9 * self.ring.maxlen:
                seg = bytes(self.voiced)
                # reset
                self.voiced.clear()
                self.ring.clear()
                self.triggered = False
                return seg
        return None

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    seg = VadSegmenter()

    try:
        # Optional greeting
        await ws.send_json({"type": "hello", "message": "WS connected. Send 20ms Int16 PCM frames."})

        while True:
            msg = await ws.receive()
            if "bytes" in msg and msg["bytes"] is not None:
                frame = msg["bytes"]

                # Expect exact 640-byte frames; skip anything else
                if len(frame) != FRAME_BYTES:
                    # You could buffer/re-align here if needed
                    continue

                utterance = seg.push(frame)
                if utterance:
                    # Got one utterance → send to ElevenLabs STT
                    if not ELEVEN_API_KEY:
                        await ws.send_json({"type": "error", "error": "Missing ELEVEN_API_KEY"})
                        continue
                    try:
                        stt = await transcribe_pcm_16k_mono(utterance)
                        text = stt.get("text", "")
                        print("text❤️", text)
                        await ws.send_json({"type": "final_transcript", "text": text, "provider": "elevenlabs", "raw": stt})
                    except httpx.HTTPError as e:
                        await ws.send_json({"type": "error", "error": f"STT request failed: {e}"})

            elif "text" in msg and msg["text"] is not None:
                # Handle control messages if you want (e.g., start_turn/cancel)
                # For now, just echo
                await ws.send_json({"type": "info", "echo": msg["text"]})

    except WebSocketDisconnect:
        # If mid-utterance when client disconnects, you could flush it:
        if seg.triggered and seg.voiced:
            if ELEVEN_API_KEY:
                try:
                    stt = await transcribe_pcm_16k_mono(bytes(seg.voiced))
                    await ws.close(code=1000)
                except Exception:
                    pass
        # Normal close
        return


def send_text_to_openai(text: str):
    openai.api_key = OPENAI_API_KEY
    response = openai.ChatCompletion.create(
        model="gpt-5-nano",
        messages=[{"role": "user", "content": text}],
    )
    return response.choices[0].message.content