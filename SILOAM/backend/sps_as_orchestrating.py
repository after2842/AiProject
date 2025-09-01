import os
import asyncio
import base64
import json
import sys

import sounddevice as sd
import websockets

URL = "wss://api.openai.com/v1/realtime?intent=transcription"
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise SystemExit("Set OPENAI_API_KEY first")

HEADERS = { "Authorization": f"Bearer {API_KEY}" }  # minimal header only

SAMPLE_RATE = 24_000
CHANNELS = 1
DTYPE = "int16"
CHUNK_MS = 40  # ~40ms

# Incoming event types (transcription intent)
EV_DELTA = "conversation.item.input_audio_transcription.delta"
EV_DONE  = "conversation.item.input_audio_transcription.completed"

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
                    print(delta, end="", flush=True)  # live partials
                    sessioin_chunk += delta
            elif typ == EV_DONE:
                print(sessioin_chunk)  # finalize line
                sessioin_chunk = ""
            elif typ == "error":
                print(f"\n[server error] {ev.get('error')}", file=sys.stderr)
            else:
                print(ev)
                print("something went wrong")
            # else: ignore other housekeeping events
    except websockets.ConnectionClosed:
        pass

async def mic_loop(send_json):
    """Capture mic and stream raw PCM16 chunks."""
    blocksize = int(SAMPLE_RATE * (CHUNK_MS / 1000.0))
    q = asyncio.Queue(maxsize=10)

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
