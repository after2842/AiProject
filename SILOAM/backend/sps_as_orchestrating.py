import os, json, asyncio, websockets, uuid

API_KEY = os.environ["OPENAI_API_KEY"]
MODEL = "gpt-4o-realtime-preview-2025-06-03"
WS = f"wss://api.openai.com/v1/realtime?model={MODEL}"
HDRS = {"Authorization": f"Bearer {API_KEY}", "OpenAI-Beta": "realtime=v1"}

ROUTE_TOOL = {
  "type": "function",
  "name": "route_intent",
  "description": "Classify user intent and dispatch an internal agent.",
  "strict": True,
  "parameters": {
    "type": "object",
    "properties": {
      "intent": {
        "type": "string", 
        "enum": ["general","search_new_products","filter_current_products","sort_current_products", "add_to_cart", "show_profile"],
        "description": "The intent of the user's request. If the user is asking for finding new products, use 'search_new_products'." +
        "If the user is asking to filter the current products, use 'filter_current_products'. If the user is asking to sort the current products, use 'sort_current_products'. If the user is asking to add a product to the cart, use 'add_to_cart'."+ 
        "If the user is asking to show the profile, use 'show_profile'. If the user is asking to show the cart, use 'show_cart'."
      },
      "search_new_products_details": {
        "type": "string",
        "description": "IF user intent is 'search_new_products', explain the details of the new products the user is looking for."
      }
    },
    "required": ["intent"],
    "additionalProperties": False
  }
}

async def speak(ws, text):
    await ws.send(json.dumps({"type":"response.create","response":{
        "modalities":["audio","text"], "instructions": text
    }}))

async def run_background_agent(intent, payload):
    # ← replace with your own agent call(s)
    await asyncio.sleep(1.0)  # simulate work
    return {"status":"ok","intent":intent,"data":{"echo":payload}}

async def main():
    async with websockets.connect(WS, extra_headers=HDRS) as ws:
        # 1) Configure session: tools + VAD
        await ws.send(json.dumps({
        "type":"session.update",
        "session":{
            "voice":"alloy",
            "turn_detection":{"type":"server_vad"},
            "input_audio_transcription":{"model":"whisper-1"},
            "tools":[ROUTE_TOOL],
            "instructions":(
                "For each user utterance: call route_intent with best intent+payload. "
                "After tool output arrives, answer the user's question based on the tool output."
            )
        }}))

        # Track pending tool calls -> asyncio task + call_id
        pending = {} #receives the function arguments from the model (accumulates the function arguments deltas)

        async def rx():
            async for raw in ws:
                evt = json.loads(raw)
                t = evt.get("type","")

                # Tool call args stream
                if t == "response.function_call.arguments.delta":
                    cid = evt["call_id"]
                    pending.setdefault(cid, {"args": ""})
                    pending[cid]["args"] += evt["delta"]

                # Tool call completed: kick off background agent
                elif t == "response.function_call.completed":
                    cid = evt["call_id"]
                    name = evt["name"]
                    args = json.loads(pending[cid]["args"] or "{}")

                    if name == "route_intent":
                        # start your agent in the background
                        task = asyncio.create_task(run_background_agent(args["intent"], args["search_new_products_details"]))
                        pending[cid]["task"] = task
                        # immediate acknowledgement to the user
                        await speak(ws, f"Got it—working on {args['intent']}. You can keep talking. Let me find the products for you!")

                # When the model finishes speaking/typing a turn (optional to observe)
                elif t == "response.completed":
                    pass

        async def tx():
            # Poll background tasks and flush results back to the model
            while True:
                done = [cid for cid,info in pending.items()
                        if "task" in info and info["task"].done()]
                for cid in done:
                    result = pending[cid]["task"].result()
                    # 2) Post tool OUTPUT tied to the original call_id
                    await ws.send(json.dumps({
                        "type":"conversation.item.create",
                        "item":{
                            "type":"function_call_output",
                            "call_id": cid,
                            "output": json.dumps(result)
                        }
                    }))
                    # 3) Ask model to continue with the new info
                    await ws.send(json.dumps({"type":"response.create","response":{
                        "modalities":["audio","text"],
                        "instructions":"explain about the products that you found."
                    }}))
                    del pending[cid]
                await asyncio.sleep(0.05)

        await asyncio.gather(rx(), tx())

if __name__ == "__main__":
    asyncio.run(main())
