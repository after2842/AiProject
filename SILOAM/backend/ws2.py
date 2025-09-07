import os
import json
import asyncio
import base64
from typing import Optional
import contextlib
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Literal
from pydantic import Field, confloat
from openai import OpenAI
from openai import AsyncOpenAI
# ---------- CONFIG ----------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise SystemExit("Set OPENAI_API_KEY")

OAI_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

# If your clients also send binary PCM (not base64 JSON), set this to True to encode on the server:
ALLOW_BINARY_PCM_FROM_CLIENT = True  # client may send raw PCM16 (24k mono) as binary frames

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"ok": True, "ws": "/ws"}

async def _open_openai_ws() -> websockets.WebSocketClientProtocol:
    """
    Open a WebSocket to OpenAI Realtime (transcription intent),
    and immediately configure the session with your STT model.
    """
    ws = await websockets.connect(
        OAI_URL,
        additional_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        max_size=1 << 24,
    )

    # MUST: pick the transcription model at session start
    # https://platform.openai.com/docs/api-reference/realtime-client-events/session/update
    session_update = {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "transcription": {"model": "gpt-4o-transcribe"}
                }
            }
        },
    }
    await ws.send(json.dumps(session_update))
    return ws

async def stream_tts(text: str, client_ws: WebSocket):
    audio_size = 0 
    await client_ws.send_text(json.dumps({"type": "tts.start"}))
    try:
        async with AsyncOpenAI(api_key=OPENAI_API_KEY).audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts", voice="coral", input=text, response_format="pcm", instructions="Speak in a calm and slow manner."
        ) as resp:
            #nt("resp: ", resp)
            async for chunk in resp.iter_bytes():
                #print(f"Sending audio chunk: {len(chunk)} bytes")
                audio_size += len(chunk)
                await client_ws.send_bytes(chunk)
    finally:
        print(f"🟢 TTS: Sent {audio_size} bytes of audio")
        audio_size = 0
        await client_ws.send_text(json.dumps({"type": "tts.end2", "audio_size": audio_size}))


async def _pump_openai_to_client(openai_ws: websockets.WebSocketClientProtocol,
                                 client_ws: WebSocket):
    await stream_tts("The previous article on Audio Worklet detailed the basic concepts and usage. Since its launch in Chrome 66 there have been many requests for more examples of how it can be used in actual applications. The Audio Worklet unlocks the full potential of WebAudio, but taking advantage of it can be challenging because it requires understanding concurrent programming wrapped with several JS APIs. Even for developers who are familiar with WebAudio, integrating the Audio Worklet with other APIs (e.g. WebAssembly) can be difficult.", client_ws)
    return


@app.websocket("/ws2")
async def ws_bridge(client_ws: WebSocket):
    """
    WebSocket bridge:
      Browser/SDK  <───WS───>  FastAPI  <───WS───>  OpenAI Realtime (transcription)

    Client payloads we accept:
      - Binary raw PCM16 (24 kHz mono) frames → forwarded as input_audio_buffer.append (base64)
      - JSON {"type":"input_audio_buffer.append","audio":"<base64>"}
      - JSON {"type":"input_audio_buffer.commit"} / {"type":"input_audio_buffer.clear"}

    Server → client:
      - Pass-through of OpenAI transcription events:
          conversation.item.input_audio_transcription.delta / .completed
      - session.created / error (for visibility)
    """
    print("🟢 CONNECTION: New WebSocket client connected")
    await client_ws.accept()
    print("✅ CONNECTION: WebSocket handshake completed")

    openai_ws = await _open_openai_ws()

    # conv_id = conversation.id

    # Two pumps running concurrently
    # to_oai = asyncio.create_task(_pump_client_to_openai(client_ws, openai_ws))
    from_oai = asyncio.create_task(_pump_openai_to_client(openai_ws, client_ws))

    try:
        # done, pending = await asyncio.wait(
        #     {from_oai}, return_when=asyncio.FIRST_EXCEPTION
        # )
        while True:
            await asyncio.gather(from_oai)
            await asyncio.sleep(10)
        # surface exceptions if any
        # for t in done:
        #     e = t.exception()
        #     if e:
        #         raise e
    except WebSocketDisconnect:
        print("🔴 CLIENT DISCONNECTED: WebSocketDisconnect exception - client closed connection")
        pass

    except Exception as e:
        print(f"🔴 SERVER ERROR: {type(e).__name__}: {e}")
        # Try to inform client; ignore if it's already gone
        try:
            await client_ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            print("📤 Sent error message to client")
        except Exception as send_error:
            print(f"❌ Failed to send error message to client: {send_error}")
            pass
    finally:
        print("🧹 CLEANUP: Starting WebSocket cleanup...")
        # Cleanup
        for t in (from_oai):
            if not t.done():
                print("⏹️  Cancelling task")
                t.cancel()
        try:
            await openai_ws.close()
            print("✅ Successfully closed openai_ws")
        except Exception as e:
            print(f"❌ Failed to close openai_ws: {e}")
        
        try:
            await client_ws.close()
            print("✅ Successfully closed client_ws")
        except Exception as e:
            print(f"❌ Failed to close client_ws: {e}")
        print("🏁 CLEANUP: WebSocket cleanup completed")