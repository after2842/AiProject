import asyncio
from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, Runner
from openai import OpenAI

async def main():
    agent = Agent(
        name="Joker",
        instructions="You are a helpful assistant. Generate a long story about a joker. It shoul be a long story with a lot of details.",
    )

    result = Runner.run_streamed(agent, input="Please tell me 5 jokes.")
    async for event in result.stream_events():
        if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
            print(f"EVENT.data: {event.data}")
            #print(event.data.delta, end="", flush=True)


if __name__ == "__main__":
    asyncio.run(main())