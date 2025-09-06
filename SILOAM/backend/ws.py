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

async def _pump_client_to_openai(client_ws: WebSocket,
                                 openai_ws: websockets.WebSocketClientProtocol):
    """
    Forward audio-chunk messages coming from your client to OpenAI.
    Accepts two styles:
      1) JSON: {"type":"input_audio_buffer.append","audio":"<base64 pcm>"}
      2) Binary: raw PCM16 bytes (we base64-encode + wrap for you)
    Also forwards commit/clear if your client sends them.
    """
    while True:
        msg = await client_ws.receive()

        # Binary payload from client
        if msg["type"] == "websocket.receive" and msg.get("bytes") is not None:
            #print("client sent binary")
            if not ALLOW_BINARY_PCM_FROM_CLIENT:
                # ignore or you can send back an error JSON
                continue
            raw = msg["bytes"]
            # Wrap to OpenAI's append event with base64 audio (server-side VAD will handle turn)
            evt = {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(raw).decode("ascii"),
            }
            await openai_ws.send(json.dumps(evt))
            continue

        # Text (JSON) payload from client
        if msg["type"] == "websocket.receive" and msg.get("text"):
            print("client sent text")
            try:
                obj = json.loads(msg["text"])
            except Exception:
                # ignore invalid text
                continue

            mtype = obj.get("type", "")

            if mtype == "input_audio_buffer.append":
                # Already base64 from client; forward as-is
                await openai_ws.send(json.dumps(obj))
                continue

            # Forward commit/clear if you drive turns from client side
            if mtype in ("input_audio_buffer.commit", "input_audio_buffer.clear"):
                await openai_ws.send(json.dumps(obj))
                continue

            # Your app may have other messages; ignore or handle here
            # e.g., {"type":"debug"} etc.
            continue

        # Client disconnect or other frames
        if msg["type"] in ("websocket.disconnect", "websocket.close"):
            raise WebSocketDisconnect()


async def stream_tts(text: str, client_ws: WebSocket):
    try:
        async with AsyncOpenAI(api_key=OPENAI_API_KEY).audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts", voice="coral", input=text, response_format="pcm"
        ) as resp:
            print("resp: ", resp)
            async for chunk in resp.iter_bytes():
                print(f"Sending audio chunk: {len(chunk)} bytes")
                await client_ws.send_bytes(chunk)
    finally:
        await client_ws.send_text(json.dumps({"type": "tts.end"}))

class RouteDecision(BaseModel):
    category: Literal["find_product", "add_to_cart", "support", "end_conversation", "other"] = Field(
        description="Pick the single best category for the user's request."
    )
    confidence: confloat(ge=0, le=1) =  Field(description="Confidence 0..1 for the category decision")
    # skip_opensearch_query: bool = Field(description="Skip the opensearch query if user question can be answered from the previous search results.")
    # opensearch_keywords: list[str] = Field(description="The keywords for the opensearch query agent. Must include the product type. Leggings, Yoga Pants, Shorts, Goosedown etc.")
    rationale_for_category: str = Field(description="Short why for debugging")
    # rationale_for_skip_opensearch_query: str = Field(description="Short why for debugging")
    # rationale_for_opensearch_prompt: str = Field(description="Short why for debugging")

client = OpenAI(api_key=OPENAI_API_KEY)
conversation = client.conversations.create()


async def get_route_decision(user_input: str, conv_id: str):

    response = client.responses.create(
        model="gpt-4.1-nano-2025-04-14",
        input=[
            {"role": "system",
            "content": "You are a helpful assistant. Assist the user with their request." + "User's reuquest may be incomplete, so wait for the user to complete the request."
            },
            {
                "role": "user",
                "content": user_input,
            },
        ],
        conversation=conv_id,
        #text_format=RouteDecision,
    )
    return response.output_text

async def _pump_openai_to_client(openai_ws: websockets.WebSocketClientProtocol,
                                 client_ws: WebSocket, conv_id: str):
    """
    Forward selected OpenAI events back to your client.
    We pass through:
      - conversation.item.input_audio_transcription.delta
      - conversation.item.input_audio_transcription.completed
      - session.created / error (useful for debugging)
    You can also just forward *all* JSON you receive if you prefer.
    """
    async for raw in openai_ws:
        # OpenAI WS messages are JSON strings (and occasionally binary if you request TTS/audio)
        if isinstance(raw, (bytes, bytearray)):
            print("raw❤️", raw)
            # For transcription-only sessions we don't expect binary from OpenAI;
            # ignore or route elsewhere if you enable audio out.

            #continue

        try:
 
            event = json.loads(raw)
            type = event.get("type")
            if type == "conversation.item.input_audio_transcription.delta":
                delta = event.get("delta", "")
                if delta:
                    #print("delta: ",delta, end="", flush=True)  # live partials
                    print(f"delta: {delta}\n")
            elif type == "conversation.item.input_audio_transcription.completed":
                print(f"User said: {event.get('transcript', 'No transcript')}")
                answer = await get_route_decision(event.get('transcript', 'No transcript'), conv_id)
                print("Model answered: ", answer)
                await stream_tts(answer, client_ws)
                #await client_ws.send_text(json.dumps(route_decision))
            else:
                continue
                # print("unknown event", event)
        except Exception as e:
            # debug: forward opaque message
            print("error", e)
            await client_ws.send_text(raw)
            continue

        etype = event.get("type", "")

        # Pass through useful events
        if etype in (
            "session.created",
            "error",
            "conversation.item.input_audio_transcription.delta",
            "conversation.item.input_audio_transcription.completed",
        ):
            await client_ws.send_text(json.dumps(event))
            continue

        # Optional: forward everything for debugging
        # await client_ws.send_text(json.dumps(ev))

@app.websocket("/ws")
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
    await client_ws.accept()

    openai_ws = await _open_openai_ws()

    conv_id = conversation.id

    # Two pumps running concurrently
    to_oai = asyncio.create_task(_pump_client_to_openai(client_ws, openai_ws))
    from_oai = asyncio.create_task(_pump_openai_to_client(openai_ws, client_ws, conv_id))

    try:
        done, pending = await asyncio.wait(
            {to_oai, from_oai}, return_when=asyncio.FIRST_EXCEPTION
        )
        # surface exceptions if any
        for t in done:
            e = t.exception()
            if e:
                raise e
    except WebSocketDisconnect:
        pass
    except Exception as e:
        # Try to inform client; ignore if it's already gone
        try:
            await client_ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
    finally:
        # Cleanup
        for t in (to_oai, from_oai):
            if not t.done():
                t.cancel()
        with contextlib.suppress(Exception):
            await openai_ws.close()
        with contextlib.suppress(Exception):
            await client_ws.close()
