#!/usr/bin/env python3
"""
Simple OpenSearch Query Examples
Query your product catalog index with different search patterns
"""

import os
import json
from typing import Dict, List, Any

import boto3
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")
OS_HOST = "search-siloam-v01-qoptmnyzfw527t36u56xvzhsje.us-west-2.es.amazonaws.com"
OS_INDEX = "catalog_search_v1"

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

def search_products(client: OpenSearch, query: Dict[str, Any], size: int = 10) -> Dict[str, Any]:
    """Execute search query and return results"""
    try:
        response = client.search(
            index=OS_INDEX,
            body=query,
            size=size
        )
        return response
    except Exception as e:
        print(f"Search error: {e}")
        return None

def print_results(response: Dict[str, Any], title: str):
    """Pretty print search results"""
    print(f"\n{'='*50}")
    print(f"🔍 {title}")
    print(f"{'='*50}")
    
    if not response or 'hits' not in response:
        print("No results found")
        return
    
    total = response['hits']['total']['value']
    print(f"Total results: {total}")
    print(f"Showing: {len(response['hits']['hits'])}")
    print("-" * 50)
    
    for i, hit in enumerate(response['hits']['hits'], 1):
        source = hit['_source']
        score = hit['_score']
        
        print(f"{i}. Score: {score:.2f}")
        print(f"   Product: {source.get('product_title', 'N/A')}")
        print(f"   Vendor: {source.get('vendor', 'N/A')}")
        print(f"   Price: ${source.get('price', 'N/A')}")
        print(f"   Available: {source.get('available', 'N/A')}")
        print(f"   Tags: {source.get('tags', [])}")
        print()

def main():
    """Run various search examples"""
    print("🚀 OpenSearch Query Examples")
    print(f"Index: {OS_INDEX}")
    print(f"Host: {OS_HOST}")
    
    # Create client
    client = make_os_client()
    
    # Example 1: Simple text search
    print("\n1️⃣ Simple Text Search")
    text_query = {
        "query": {
            "match": {
                "product_title": "shirt"
            }
        }
    }
    results = search_products(client, text_query, size=5)
    print_results(results, "Products with 'shirt' in title")
    
    # Example 2: Filter by vendor
    print("\n2️⃣ Filter by Vendor")
    vendor_query = {
        "query": {
            "bool": {
                "must": [
                    {"match": {"vendor": "Nike"}}
                ]
            }
        }
    }
    results = search_products(client, vendor_query, size=5)
    print_results(results, "Nike products")
    
    # Example 3: Price range search
    print("\n3️⃣ Price Range Search")
    price_query = {
        "query": {
            "bool": {
                "must": [
                    {"range": {"price": {"gte": 50, "lte": 100}}}
                ]
            }
        },
        "sort": [{"price": {"order": "asc"}}]
    }
    results = search_products(client, price_query, size=5)
    print_results(results, "Products between $50-$100")
    
    # Example 4: Available products only
    print("\n4️⃣ Available Products Only")
    available_query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"available": True}}
                ]
            }
        }
    }
    results = search_products(client, available_query, size=5)
    print_results(results, "Available products")
    
    # Example 5: Multi-field search
    print("\n5️⃣ Multi-Field Search")
    multi_query = {
        "query": {
            "multi_match": {
                "query": "running",
                "fields": ["product_title", "tags", "product_type"]
            }
        }
    }
    results = search_products(client, multi_query, size=5)
    print_results(results, "Products related to 'running'")
    
    # Example 6: Aggregation by vendor
    print("\n6️⃣ Vendor Aggregation")
    agg_query = {
        "size": 0,  # No documents, just aggregations
        "aggs": {
            "vendors": {
                "terms": {
                    "field": "vendor",
                    "size": 10
                }
            }
        }
    }
    results = search_products(client, agg_query)
    if results and 'aggregations' in results:
        print("\n📊 Top Vendors:")
        vendors = results['aggregations']['vendors']['buckets']
        for vendor in vendors[:5]:
            print(f"   {vendor['key']}: {vendor['doc_count']} products")
    
    # Example 7: Complex boolean query
    print("\n7️⃣ Complex Boolean Query")
    complex_query = {
        "query": {
            "bool": {
                "must": [
                    {"match": {"product_type": "shoes"}}
                ],
                "should": [
                    {"match": {"tags": "running"}},
                    {"match": {"tags": "athletic"}}
                ],
                "must_not": [
                    {"term": {"available": False}}
                ],
                "filter": [
                    {"range": {"price": {"gte": 20}}}
                ]
            }
        }
    }
    results = search_products(client, complex_query, size=5)
    print_results(results, "Shoes that are running/athletic, available, and >=$20")

if __name__ == "__main__":
    main() 