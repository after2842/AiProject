import os
import asyncio
import base64
import json
import sys

import sounddevice as sd
import websockets
from openai import OpenAI
from pydantic import BaseModel
from typing import Literal
from pydantic import Field, confloat
from openai import AsyncOpenAI
from openai.helpers import LocalAudioPlayer

URL = "wss://api.openai.com/v1/realtime?intent=transcription"
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise SystemExit("Set OPENAI_API_KEY first")
client = OpenAI(api_key=API_KEY)
client_async = AsyncOpenAI(api_key=API_KEY)
HEADERS = { "Authorization": f"Bearer {API_KEY}" }  # minimal header only

SAMPLE_RATE = 24_000
CHANNELS = 1
DTYPE = "int16"
CHUNK_MS = 40  # ~40ms

# Incoming event types (transcription intent)
EV_DELTA = "conversation.item.input_audio_transcription.delta"
EV_DONE  = "conversation.item.input_audio_transcription.completed"

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

conversation = client.conversations.create()
conv_id = conversation.id

async def get_route_decision(user_input: str):

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


async def process_llm_background(text):
    """Process LLM in background without blocking audio"""
    try:
        route_decision = await get_route_decision(text)
        print("route_decision: ", route_decision)
    except Exception as e:
        print(f"LLM processing error: {e}")

async def play_audio(text):
    async with client_async.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="coral",
        input=text,
        instructions="Speak in calm and slow manner.",
        response_format="pcm",
    ) as response:
        await LocalAudioPlayer().play(response)

async def recv_loop(ws):
    """Print streaming transcription to console."""
    sessioin_chunk = ""
    try:
        async for raw in ws:
            ev = json.loads(raw)
            typ = ev.get("type")
            if typ == EV_DELTA:
                delta = ev.get("delta", "")
                if delta:
                    #print("delta: ",delta, end="", flush=True)  # live partials
                    sessioin_chunk += delta
            elif typ == EV_DONE:
                print("session_done:", sessioin_chunk)  # finalize line

                # asyncio.create_task(process_llm_background(sessioin_chunk))
                route_decision = await get_route_decision(sessioin_chunk)
                print("route_decision: ", route_decision)                
                await play_audio(route_decision)
                sessioin_chunk = ""
            elif typ == "error":
                print(f"\n[server error] {ev.get('error')}", file=sys.stderr)
            # else:
                
                #print(ev)
                #print("something went wrong")
            # else: ignore other housekeeping events
    except websockets.ConnectionClosed:
        pass

async def mic_loop(send_json):
    """Capture mic and stream raw PCM16 chunks."""
    blocksize = int(SAMPLE_RATE * (CHUNK_MS / 1000.0))
    q = asyncio.Queue()

    loop = asyncio.get_running_loop() 
    def _cb(indata, frames, time, status):
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        try:
            loop.call_soon_threadsafe(q.put_nowait, bytes(indata))
        except asyncio.QueueFull:
            pass  # drop if we're behind

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=blocksize,
        dtype=DTYPE,
        channels=CHANNELS,
        callback=_cb,
    ):
        print("🎙️ Speak (Ctrl+C to quit)")
        while True:
            chunk = await q.get()
            await send_json({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            })


            # No manual end/commit: rely on server-side VAD

async def main():
    async with websockets.connect(URL, additional_headers=HEADERS, max_size=1<<24) as ws:
        async def send_json(obj):
            await ws.send(json.dumps(obj))
        await send_json(
{
  "type": "session.update",
  "session": {
    "type": "transcription",
    "audio": {
      "input": {
        "transcription": { "model": "gpt-4o-transcribe" },
      }
    }
  }
}

)
        tasks = [
            asyncio.create_task(recv_loop(ws)),
            asyncio.create_task(mic_loop(send_json)),
        ]

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
 




        async for raw in ws:
            ev = json.loads(raw)
            typ = ev.get("type")
            print(ev, typ)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBye!")
