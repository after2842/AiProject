from __future__ import annotations
import os
import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal
import boto3
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection
from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    RunContextWrapper,
    Runner,
    function_tool,
)
from agents.agent import StopAtTools  # for stop-on-tool behavior  # noqa: E402
OS_QUERY_INSTRUCTIONS = """
ROLE
You write OpenSearch Search API request bodies (JSON only) to find products in the `products` index.

OUTPUT CONTRACT
- Return a single JSON object with the query wrapped in a 'body' field.
- Format: {"body": {your_opensearch_query_here}}
- No prose, no markdown, no comments, no extra keys outside the body schema.
- Prefer relevance sorting by default; only add "sort" if the user asks.

CATALOG FIELD GUIDE (what each key means & how to use it)
# Product-level (denormalized)
- merchant (keyword): The storefront/company. Use exact filters (term/terms).
- product_id (keyword): Stable product identifier. Use term.
- product_title (text): Main product name. PRIMARY text source for search.
- handle (keyword): URL slug/handle. Use term if user provides a link/slug.
- vendor (keyword): Brand/manufacturer. Use term/terms.
- product_type (keyword): Merchant-defined taxonomy. DO NOT RELY ON THIS for recall/filter (inconsistent).
- tags (keyword): Labels including promotions/discounts (“sale”, “clearance”, “50off”, “promo”, etc.). Use term/terms; for fuzzy promo intent, use wildcard/prefix on tags.
- featuredImage (keyword): Primary image id/url. Use exists if you need items with images.
- product_description (text): Long text; OPTIONAL, may be missing.
- descriptionHtml (text): HTML description; treat like text; OPTIONAL.

# Variant-level
- variant_id (keyword): Stable variant identifier. Use term.
- variant_title (text): Often size/color/options (e.g., “XL / Kid 3 months”); may NOT describe the product concept. Use as a weak signal; rely on product_title for product semantics.
- variant_description (text): Optional; if missing, fall back to titles.
- options (object, dynamic): Merchant option map (e.g., options.Size, options.Color). If you know a key exists, filter with term on options.<Key>. Do not invent keys.
- price (double): Numeric price. Use range for lte/gte filters and for price sorting.
- available (boolean): In-stock flag. Use term {available: true} to show only in-stock.
- image_url (keyword): Variant image. Use exists to require images.
- num_other_variants (integer): Count of siblings; optional for ranking/filters.

# Timestamp
- updated_at (date, epoch_second): Use for “newest” sorting or time windows (range).

QUERYING RULES
1) Primary text recall:
   - Use multi_match across: ["product_title^4","product_description^2","variant_title^1.5","variant_description^1.2"].
   - If descriptions are missing, FALL BACK to ["product_title^4","variant_title^1.5"] only.
2) Do NOT rely on product_type for recall or filtering unless the user explicitly asks for it.
3) Variant title caveat:
   - If variant_title looks like options (e.g., “XL, Kid 3months”), rely on product_title for the product concept and keep variant_title as a weak/secondary signal.
4) Promotions / discounts:
   - tags contain promo info. If user asks for “on sale/discount/clearance/promo/deal/markdown”, add a tags filter:
     - Prefer terms when exact known tags exist (e.g., ["sale","clearance","discount","promo"]).
     - Otherwise allow wildcard/prefix on tags (e.g., {"wildcard":{"tags":"sale*"}} or {"wildcard":{"tags":"*off"}}).
5) Exact constraints go in bool.filter (fast, non-scoring):
   - merchant, vendor, product_id, handle, tags, available, options.*, price range.
6) Use bool.must (or should with minimum_should_match) for semantic text (match/multi_match).
7) Ranges are only for numeric/date fields (price, updated_at).
8) Sorting:
   - “newest”: [{"updated_at":"desc"}]
   - “cheapest/lowest price”: [{"price":"asc"}]
   - Otherwise omit sort → relevance.
9) Return fields (_source):
   - Prefer a compact set: product_id, product_title, vendor, price, available, image_url, handle, tags, variant_id, variant_title, updated_at, merchant.
10) track_total_hits:
   - Use true when counts/facets matter.
11) Existence checks:
   - Require images → {"exists":{"field":"image_url"}}
   - Require a featured image → {"exists":{"field":"featuredImage"}}
12) Options filtering:
   - Only use known keys under options.<Key> with exact term filters; do not guess keys that aren’t present in data.

DEFAULT CONSTRUCTION
- Wrap everything in a bool:
  - must: one multi_match for the user’s intent (fallback fields if descriptions are missing).
  - filter: exact constraints (availability, vendor/merchant, price ranges, tags/promo, options).
- Add aggs when user asks for facets or stats (e.g., vendor terms, price stats).
- Use size (e.g., 24 or 50) appropriate to the request; include track_total_hits when needed.

EXAMPLES

# A) “adidas running shoes under $120, in stock, newest first”
{
  "query": {
    "bool": {
      "must": [
        {
          "multi_match": {
            "query": "adidas running shoes",
            "fields": ["product_title^4","product_description^2","variant_title^1.5","variant_description^1.2"]
          }
        }
      ],
      "filter": [
        {"term": {"available": true}},
        {"term": {"vendor": "adidas"}},
        {"range": {"price": {"lte": 120}}}
      ]
    }
  },
  "sort": [{"updated_at": "desc"}],
  "_source": {
    "includes": ["product_id","product_title","vendor","price","available","image_url","handle","tags","variant_id","variant_title","updated_at","merchant"]
  },
  "size": 24,
  "track_total_hits": true
}

# B) “hoodies on sale with images”
{
  "query": {
    "bool": {
      "must": [
        {
          "multi_match": {
            "query": "hoodie",
            "fields": ["product_title^4","product_description^2","variant_title^1.5","variant_description^1.2"]
          }
        }
      ],
      "filter": [
        {"exists": {"field": "image_url"}},
        {
          "bool": {
            "should": [
              {"terms": {"tags": ["sale","clearance","discount","promo"]}},
              {"wildcard": {"tags": "sale*"}},
              {"wildcard": {"tags": "*off"}}
            ],
            "minimum_should_match": 1
          }
        }
      ]
    }
  },
  "size": 50,
  "track_total_hits": true
}

# C) “baby clothing, size 3 months”
{
  "query": {
    "bool": {
      "must": [
        {
          "multi_match": {
            "query": "baby clothing",
            "fields": ["product_title^4","product_description^2","variant_title^1.5","variant_description^1.2"]
          }
        }
      ],
      "should": [
        {"match": {"variant_title": "3 months"}}
      ],
      "minimum_should_match": 0
    }
  },
  "size": 24,
  "track_total_hits": true
}

NOTES
- Use only documented fields. Do not invent fields. Do not rely on product_type unless explicitly requested.
- If descriptions are missing, fall back to titles (product_title, variant_title).
- Keep text queries in must/should and constraints in filter.

CRITICAL: Wrap your entire query in a "body" field like this:
{
  "body": {
    "query": {...},
    "filter": {...},
    "_source": {...}
  }
}

"""




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

async def _execute_search_handler(ctx: RunContextWrapper[UserContext], args_json: str) -> str:
    """Handler for execute_opensearch_search tool"""
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
        res = ctx.context.os_client.search(index=index, body=request)
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Handler error: {e}")
        return json.dumps({"error": f"Search failed: {str(e)}"})

execute_opensearch_search = FunctionTool(
    name="execute_opensearch_search",
    description="Execute an OpenSearch search and return the JSON result string.",
    params_json_schema={
        "type": "object",
        "properties": {
            "body": {"type": "object", "description": "The OpenSearch search query"}
        },
        "required": ["body"]
    },
    on_invoke_tool=_execute_search_handler,
)

@function_tool
def record_input_arguments(ctx: RunContextWrapper[UserContext], args_json: str) -> str:
    """Record the input arguments to a file"""
    print(f"🔍 Recording input arguments: {args_json}")
    with open("input_arguments.json", "w") as f:
        f.write(args_json)
    return "Input arguments recorded to input_arguments.json"

# --- (2) Generate query agent ---
generate_query_agent = Agent[UserContext](
    name="Generate OpenSearch Query Agent",
    instructions=OS_QUERY_INSTRUCTIONS,
    model="gpt-5-nano-2025-08-07",
    model_settings=ModelSettings(
        openai_api_key=os.getenv("OPENAI_API_KEY")  # Explicitly pass API key
    )
)

# --- (3) The Agent: force the first tool call, stop after executor tool ---
opensearch_agent = Agent[UserContext](
    name="OpenSearch Query Agent",
    instructions=(
        "You MUST follow these steps in EXACT order. DO NOT USE THE TOOLS OUTSIDE OF THESE STEPS:\n"
        "Step 1) ALWAYS call generate_query_agent first with the user's request. This is MANDATORY and cannot be skipped.\n"
        "Step 2) Record the result of generate_query_agent. use record_input_arguments tool to do this. \n"
        "Step 3) Pass the result of record_input_arguments to execute_opensearch_search. \n"
    ),
    model="gpt-5-2025-08-07",
    tools=[generate_query_agent.as_tool(
        tool_name="generate_opensearch_query",
        tool_description="Generate a valid OpenSearch query body for the user's request.",
    ), 
    execute_opensearch_search,
    record_input_arguments
    ],
    model_settings=ModelSettings(
        tool_choice="none",
        openai_api_key=os.getenv("OPENAI_API_KEY")  # Explicitly pass API key
    ),
    tool_use_behavior=StopAtTools(stop_at_tool_names=["execute_opensearch_search"]),
)


# --- (5) Example run ---
async def main() -> None:
    ctx = UserContext(os_client=OpenSearchClient())

    user_utterance = input("Enter your search query: ")
    
    # # Debug: Let's see what the generate_query_agent produces first
    # print("🔍 Testing generate_query_agent directly...")
    # try:
    #     query_result = await Runner.run(
    #         starting_agent=generate_query_agent,
    #         input=user_utterance,
    #         context=ctx,
    #         max_turns=2,
    #     )
    #     print("✅ Query agent output:  ", query_result.final_output)
    #     print("---")
    # except Exception as e:
    #     print(f"❌ Query agent failed: {e}")
    #     return
    
    print("🚀 Now testing full opensearch_agent...")
    try:
        result = await Runner.run(
            starting_agent=opensearch_agent,
            input=user_utterance,
            context=ctx,  # typed context is available inside tools
            max_turns=3,  # enough for: build -> execute
        )
        print("✅ Full agent succeeded!")
        print("FINAL OUTPUT:\n", result.final_output)
    except Exception as e:
        print(f"❌ Full agent failed: {e}")
        print(f"❌ Error type: {type(e)}")
        if hasattr(e, '__dict__'):
            print(f"❌ Error details: {e.__dict__}")

    # The final output is execute_opensearch_search's JSON string
    #print("FINAL OUTPUT:\n", result.final_output)

if __name__ == "__main__":
    asyncio.run(main())
