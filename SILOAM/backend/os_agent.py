from __future__ import annotations
import os
import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal
import boto3
from openai import OpenAI
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection
from pydantic import BaseModel, Field, confloat
from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    RunContextWrapper,
    Runner,
    function_tool,
    RunConfig,
    SQLiteSession,
)
from agents.agent import StopAtTools  # for stop-on-tool behavior  # noqa: E402

osquery_generator = OpenAI()

class RouteDecision(BaseModel):
    category: Literal["find_product", "add_to_cart", "support", "end_conversation", "other"] = Field(
        description="Pick the single best category for the user's request."
    )
    confidence: confloat(ge=0, le=1) =  Field(description="Confidence 0..1 for the category decision")
    skip_opensearch_query: bool = Field(description="Skip the opensearch query if user question can be answered from the previous search results.")
    opensearch_prompt: str = Field(description="The natural language prompt for the opensearch query agent.")
    rationale_for_category: str = Field(description="Short why for debugging")
    rationale_for_skip_opensearch_query: str = Field(description="Short why for debugging")
    rationale_for_opensearch_prompt: str = Field(description="Short why for debugging")

orchestrating_agent = Agent(
    name="Orchestrating Agent",
    instructions=(
        "You are a shopping assistant that classifies the user's query into one of : find_product, add_to_cart, support, other, end_conversation."
        "You have to decide if you need to skip the opensearch query if user question can be answered from the previous search results."
        "IF we need to make a opensearch query, make a short prompt for the agent that will be used to generate the opensearch query. Else, return an empty string."
        "Return ONLY the JSON object matching the schema."
    ),
    output_type=RouteDecision,  # <-- forces structured outputs instead of plain text
    model="gpt-5-mini-2025-08-07",
)


# --- (3) Execution tool (pure Python; hits your OpenSearch cluster) ---
# Replace this with your real OpenSearch client.
class OpenSearchClient:
    def search(self, index: str, body: dict) -> dict:
        session = boto3.Session(region_name="us-west-2")
        creds = session.get_credentials().get_frozen_credentials()
        auth = AWS4Auth(creds.access_key, creds.secret_key, "us-west-2", "es", session_token=creds.token)
        
        client = OpenSearch(
            hosts=[{"host": os.getenv("OS_HOST"), "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )
        return client.search(index=index, body=body)

@dataclass
class UserContext:
    os_client: Any
    default_index: str = "catalog_search_v01"

async def _execute_search_handler(ctx: UserContext, args_json: str) -> str:
    """It executes the OpenSearch search query and returns the result as a JSON string."""
    try:
        print(f"🔍 Handler received args_json: {args_json}")
        args = json.loads(args_json)
        print(f"🔍 Parsed args: {args}")
        
        body = args.get("body", {})
        print(f"🔍 Extracted body: {body}")
        
        # Use default index and size
        index = "catalog_search_v01"
        request = dict(body)
        
        # Ensure reasonable size limit
        if "size" not in request:
            request["size"] = 24
        
        print(f"🔍 Final request: {request}")
        res = ctx.os_client.search(index=index, body=request)
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Handler error: {e}")
        return json.dumps({"error": f"Search failed: {str(e)}"})





# --- (3) The Agent: force the first tool call, stop after executor tool ---
opensearch_summarize_agent = Agent[UserContext](
    name="OpenSearch Query Agent",
    instructions=(
        "You are a helpful assistant that summarizes the OpenSearch search results."
        "Pretend you are a shopping assistant that explains about each products in the search results."
        "You may be given the OpenSearch search results as a JSON string. You may need to filter the results based on the user's request. Explain about each product, up to 10 products"
    ),
    model="gpt-5-nano-2025-08-07",

)


# --- (5) Example run ---
async def main() -> None:
    ctx = UserContext(os_client=OpenSearchClient())

    

    session = SQLiteSession("os_agent_history")
    print("🚀 Now testing full opensearch_agent...")
    try:
        while True:
            user_utterance = input("Enter your search query: ")
            user_intent = await Runner.run(orchestrating_agent, input=user_utterance, session=session)
            print("user intent.output: ", user_intent.final_output) 
            decision: RouteDecision = user_intent.final_output

            print("user intent: ", decision.category)
            print("skip_opensearch_query: ", decision.skip_opensearch_query)
            print("opensearch_prompt: ", decision.opensearch_prompt)

            if decision.category == "find_product":
                opensearch_result = "answer can be found in previous search results"

                if not decision.skip_opensearch_query:
                    query_result = osquery_generator.responses.create(
                    prompt={
                        "id": "pmpt_68a82aa6b0e08195b79039133452be4409ad5744a299975d",
                        "version": "1"
                    },
                    input=decision.opensearch_prompt,
                    model="gpt-5-nano-2025-08-07",
                    max_output_tokens=10240,
                    truncation="auto",
                    )
                    print("FINAL OUTPUT:\n", query_result.output_text)
                    print("---")

                    opensearch_result = await _execute_search_handler(ctx, query_result.output_text)
                    print("---")
                    print("Opensearch result: ", opensearch_result)
                    print("---")

                summarize_result = await Runner.run(
                    starting_agent=opensearch_summarize_agent,
                    session=session,
                    input=opensearch_result,
                    context=ctx,
                    max_turns=3,
                )

                print("✅ Opensearch result: ", summarize_result.final_output)

            if decision.category == "add_to_cart":
                print("Add to cart")

            if decision.category == "support":
                print("Support")

            if decision.category == "other":
                print("Other")

            if decision.category == "end_conversation":
                print("End conversation\n\n")

                items = await session.get_items()   # returns a list of input items
                print(f"{len(items)} items in session {session.session_id}")
                for i, it in enumerate(items, 1):
                    print(f"\n--- item #{i} ---")
                    print(json.dumps(it, indent=2, ensure_ascii=False))

                break

    except Exception as e:
        print(f"❌ Full agent failed: {e}")
        #print(f"session: {str(session)}")
        if hasattr(e, '__dict__'):
            print(f"❌ Error details: {e.__dict__}")

    # The final output is execute_opensearch_search's JSON string
    #print("FINAL OUTPUT:\n", result.final_output)

if __name__ == "__main__":
    asyncio.run(main())
