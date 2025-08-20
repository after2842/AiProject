#!/usr/bin/env python3
"""
Simple OpenSearch Query - Basic Usage
"""

import boto3
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection

# Configuration
OS_HOST = "search-siloam-v01-qoptmnyzfw527t36u56xvzhsje.us-west-2.es.amazonaws.com"
OS_INDEX = "catalog_search_v01"

def search_products(search_term: str, size: int = 10):
    """Simple search function"""
    
    # Create OpenSearch client
    session = boto3.Session(region_name="us-west-2")
    creds = session.get_credentials().get_frozen_credentials()
    auth = AWS4Auth(creds.access_key, creds.secret_key, "us-west-2", "es", session_token=creds.token)
    
    client = OpenSearch(
        hosts=[{"host": OS_HOST, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )
    
    # Build query
    query = {
  "query": {

          "multi_match": {
            "query": "crop",
            "fields": ["product_title", "product_description", "variant_title", "variant_description", "descriptionHtml"]
            
        

    }
  },
  "sort": [{ "price": "asc" }],
  "_source": ["merchant","product_description","product_title","handle","variant_description","variant_title","price","available","image_url"]
}

    
    # Execute search
    try:
        response = client.search(index=OS_INDEX, body=query, size=size)
        
        print(f"🔍 Search results for: '{search_term}'")
        print(f"Total found: {response['hits']['total']['value']}")
        print("-" * 50)
        
        for i, hit in enumerate(response['hits']['hits'], 1):
            source = hit['_source']
            print(f"{i}. {source.get('product_title', 'N/A')}")
            print(f"   Merchant: {source.get('merchant', 'N/A')}")
            print(f"   Price: ${source.get('price', 'N/A')}")
            print(f"   Available: {source.get('available', 'N/A')}")
            print(f"   Description: {source.get('product_description', 'N/A')}")
            print(f"   Variant Title: {source.get('variant_title', 'N/A')}")
            print(f"   Product Type: {source.get('product_type', 'N/A')}")
            print(f"   Vendor: {source.get('vendor', 'N/A')}")
            print(f"   Price: ${source.get('price', 'N/A')}")
            print(f"   Available: {source.get('available', 'N/A')}")
            print(f"   Image: {source.get('image_url', 'N/A')}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # Example usage
    search_products("Show me crop t-shirts under $35 that are in stock.", size=10)
    print("\n" + "="*50 + "\n")
