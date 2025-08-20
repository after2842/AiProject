from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Dict, Any, Tuple
from enum import Enum, auto
from datetime import datetime
import json
import os
import boto3
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection

# OpenSearch configuration
OS_HOST = "search-siloam-v01-qoptmnyzfw527t36u56xvzhsje.us-west-2.es.amazonaws.com"
OS_INDEX = "catalog_search_v1"
REGION = "us-west-2"

def make_os_client():
    """Create OpenSearch client with AWS authentication"""
    session = boto3.Session(region_name=REGION)
    creds = session.get_credentials().get_frozen_credentials()
    awsauth = AWS4Auth(creds.access_key, creds.secret_key, REGION, "es", session_token=creds.token)
    
    return OpenSearch(
        hosts=[{"host": OS_HOST, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )

# --------------------------
# User context (shared state)
# --------------------------

@dataclass
class UserContext:
    uid: str
    name: str
    is_pro_user: bool = False
    recent_queries: List[str] = field(default_factory=list)
    last_interaction: Optional[datetime] = None
    # Put whatever your sub-agents need here:
    # cart_id, locale, currency, etc.

    async def fetch_purchases(self) -> List[Dict[str, Any]]:
        # TODO: replace with your DB call
        return []

    async def append_query(self, q: str) -> None:
        self.recent_queries.append(q)
        self.last_interaction = datetime.utcnow()


# --------------------------
# Intents & routing contract
# --------------------------

class Intent(Enum):
    ECOM_STORE_SEARCH = auto()      # query internal catalog (OpenSearch, SQL, etc.)
    WEB_SEARCH = auto()             # search public internet
    GENERAL_QA = auto()             # general Q&A, not tied to catalog
    ACCOUNT_HELP = auto()           # e.g., orders, returns, membership
    SMALL_TALK = auto()             # greetings, chit-chat
    FALLBACK = auto()

class Agent(Protocol):
    async def handle(self, user_input: str, context: UserContext) -> Dict[str, Any]:
        ...


# --------------------------
# Sub-agents (examples)
# --------------------------

from agents import Agent, ModelSettings, function_tool
import json

@function_tool
def search_products(query: str, vendor: str = None, product_type: str = None, 
                   price_min: float = None, price_max: float = None, 
                   available_only: bool = False, size: int = 24) -> str:
    """Search the product catalog using OpenSearch with specified filters."""
    
    # Build OpenSearch query
    search_body = {
        "query": {
            "bool": {
                "must": [],
                "filter": [],
                "should": []
            }
        },
        "size": min(size, 50),
        "track_total_hits": True
    }
    
    # Add text search
    if query:
        search_body["query"]["bool"]["must"].append({
            "multi_match": {
                "query": query,
                "fields": ["product_title^3", "product_description^2", "tags"],
                "type": "best_fields"
            }
        })
    
    # Add filters
    if vendor:
        search_body["query"]["bool"]["filter"].append({
            "term": {"vendor": vendor}
        })
        
    if product_type:
        search_body["query"]["bool"]["filter"].append({
            "term": {"product_type": product_type}
        })
        
    if price_min or price_max:
        price_filter = {"range": {"price": {}}}
        if price_min:
            price_filter["range"]["price"]["gte"] = price_min
        if price_max:
            price_filter["range"]["price"]["lte"] = price_max
        search_body["query"]["bool"]["filter"].append(price_filter)
        
    if available_only:
        search_body["query"]["bool"]["filter"].append({
            "term": {"available": True}
        })
    
    # Execute search with real OpenSearch client
    try:
        os_client = make_os_client()
        result = os_client.search(index=OS_INDEX, body=search_body)
        
        # Format results for display
        hits = result["hits"]["hits"]
        formatted_results = []
        
        for hit in hits[:5]:  # Show first 5 results
            source = hit["_source"]
            formatted_results.append({
                "title": source.get("product_title", "N/A"),
                "vendor": source.get("vendor", "N/A"),
                "price": source.get("price", "N/A"),
                "available": source.get("available", "N/A"),
                "product_type": source.get("product_type", "N/A")
            })
        
        return json.dumps({
            "query_used": search_body,
            "total_results": result["hits"]["total"]["value"],
            "results": formatted_results
        }, indent=2)
        
    except Exception as e:
        return f"Search error: {str(e)}"

@function_tool
def get_product_categories() -> str:
    """Get available product categories/types in the catalog."""
    categories = ["shoes", "shirts", "pants", "hats", "jackets", "accessories"]
    return f"Available categories: {', '.join(categories)}"

@function_tool
def get_available_vendors() -> str:
    """Get available brands/vendors in the catalog."""
    vendors = ["Nike", "Adidas", "Puma", "Under Armour", "Allbirds", "Tentree"]
    return f"Available brands: {', '.join(vendors)}"

class EcomSearchAgent:
    """
    Uses OpenAI Agent with function tools to handle product searches.
    """
    def __init__(self, index: str = "catalog_search_v1"):
        self.index = index
        
        # Create the agent with search tools
        self.agent = Agent(
            name="Product Search Agent",
            instructions="""You are a helpful product search assistant. 
            When users ask about products, use the search_products tool to find relevant items.
            Always use the search_products function for product queries.
            Be helpful and provide clear information about what you found.
            If users ask about categories or brands, use the appropriate tools.
            """,
            model="o3-mini",
            tools=[search_products, get_product_categories, get_available_vendors],
        )

    async def handle(self, user_input: str, context: UserContext) -> Dict[str, Any]:
        """Handle user input using the OpenAI agent with function tools."""
        
        try:
            # Run the agent with the user input
            response = await self.agent.run(user_input)
            
            # Parse the response to extract search results
            if "search_products" in str(response):
                # Extract the search results from the response
                # You might need to parse this based on your actual response format
                return {
                    "type": "catalog_results",
                    "index": self.index,
                    "agent_response": str(response),
                    "user_input": user_input,
                    "context": {
                        "user_name": context.name,
                        "is_pro_user": context.is_pro_user,
                        "recent_queries": context.recent_queries[-3:] if context.recent_queries else []
                    }
                }
            else:
                # General response from agent
                return {
                    "type": "agent_response",
                    "response": str(response),
                    "user_input": user_input
                }
                
        except Exception as e:
            print(f"Agent error: {e}")
            # Fallback to simple search
            return {
                "type": "fallback_search",
                "error": str(e),
                "user_input": user_input
            }


class WebSearchAgent:
    """Public internet search (SerpAPI, Tavily, Bing, etc.)."""
    async def handle(self, user_input: str, context: UserContext) -> Dict[str, Any]:
        # TODO: call your web search tool
        results = [{"title": "Example", "url": "https://example.com", "snippet": "…"}]
        return {"type": "web_results", "results": results}


class GeneralQAAgent:
    """General Q&A / small talk."""
    async def handle(self, user_input: str, context: UserContext) -> Dict[str, Any]:
        # TODO: call your LLM for a normal completion
        return {"type": "answer", "text": f"Hi {context.name}, here’s a helpful answer to: {user_input}"}


class AccountAgent:
    """Account/order/helpdesk actions that may call context helpers."""
    async def handle(self, user_input: str, context: UserContext) -> Dict[str, Any]:
        purchases = await context.fetch_purchases()
        # TODO: detect intents like "Where's my order?", "Return items" etc.
        return {"type": "account_summary", "recent_purchases": purchases}


# --------------------------
# Intent classifier
# --------------------------

class IntentClassifier:
    """
    Simple rule-first classifier. In prod, use a small LLM with response_format=json
    and a strict schema to output only the Intent label + confidence.
    """
    ECOM_HINTS = ("find", "buy", "price", "in stock", "size", "color",
                  "recommend", "search", "show me", "filter", "sort")
    WEB_HINTS = ("news", "google", "on the internet", "latest", "recent")
    ACCOUNT_HINTS = ("order", "purchase", "history", "return", "refund", "membership", "pro plan")

    async def classify(self, user_input: str, context: UserContext) -> Tuple[Intent, float]:
        q = user_input.lower()

        # Soft rules (good enough for v1; swap with LLM JSON classifier later)
        if any(k in q for k in self.ACCOUNT_HINTS):
            return (Intent.ACCOUNT_HELP, 0.75)
        if any(k in q for k in self.WEB_HINTS):
            return (Intent.WEB_SEARCH, 0.70)
        if any(k in q for k in self.ECOM_HINTS):
            return (Intent.ECOM_STORE_SEARCH, 0.80)
        if any(k in q for k in ("hello", "hi", "hey", "how are you")):
            return (Intent.SMALL_TALK, 0.60)
        # Use conversation recency as a weak prior
        if context.recent_queries:
            return (Intent.ECOM_STORE_SEARCH, 0.51)

        return (Intent.GENERAL_QA, 0.55)


# --------------------------
# Orchestrator
# --------------------------

class OrchestratorAgent:
    """
    Decides which action to take and forwards the task + context to the right sub-agent.
    """
    def __init__(self,
                 classifier: IntentClassifier,
                 ecom_agent: Agent,
                 web_agent: Agent,
                 qa_agent: Agent,
                 account_agent: Agent):
        self.classifier = classifier
        self.routes: Dict[Intent, Agent] = {
            Intent.ECOM_STORE_SEARCH: ecom_agent,
            Intent.WEB_SEARCH: web_agent,
            Intent.GENERAL_QA: qa_agent,
            Intent.SMALL_TALK: qa_agent,
            Intent.ACCOUNT_HELP: account_agent,
        }

    async def run(self, user_input: str, context: UserContext) -> Dict[str, Any]:
        # Record query in context
        await context.append_query(user_input)

        # 1) Decide intent
        intent, confidence = await self.classifier.classify(user_input, context)

        # 2) Handoff to the appropriate agent with the SAME context
        agent = self.routes.get(intent, self.routes[Intent.GENERAL_QA])
        payload = await agent.handle(user_input, context)

        # 3) Return normalized envelope (good for logging/telemetry/UI)
        return {
            "intent": intent.name,
            "confidence": confidence,
            "payload": payload,
            "user_context_snapshot": {
                "uid": context.uid,
                "name": context.name,
                "is_pro_user": context.is_pro_user,
                "recent_queries": context.recent_queries[-5:],  # small snapshot
                "last_interaction": context.last_interaction.isoformat() if context.last_interaction else None,
            }
        }


# --------------------------
# Example wiring
# --------------------------

# In your app startup:
classifier = IntentClassifier()
ecom_agent = EcomSearchAgent(index="catalog_search_v1")  # Now uses OpenAI Agent!
web_agent = WebSearchAgent()
qa_agent = GeneralQAAgent()
account_agent = AccountAgent()

orchestrator = OrchestratorAgent(
    classifier=classifier,
    ecom_agent=ecom_agent,
    web_agent=web_agent,
    qa_agent=qa_agent,
    account_agent=account_agent
)

# Usage in your request handler (FastAPI/Quart/etc.):
# ctx = UserContext(uid="u123", name="Sam", is_pro_user=True)
# result = await orchestrator.run("show me Nike running shoes under $100", ctx)
# return JSONResponse(result)

# Example of what the agent can now handle:
# "find red Nike shoes" → Uses search_products(query="red shoes", vendor="Nike")
# "show me available categories" → Uses get_product_categories()
# "what brands do you have?" → Uses get_available_vendors()
# "find running shoes under $50" → Uses search_products(query="running shoes", price_max=50)
