#!/usr/bin/env python3
"""
Backfill OpenSearch VARIANT documents from a DynamoDB catalog table.
- 1 doc per variant, with product fields duplicated
- Adds num_other_variants & num_other_available_variants
- Adds other_options / other_available_options summaries for quick TTS

Env:
  AWS_REGION=us-west-2
  DDB_TABLE=catalog
  OS_HOST=search-your-domain-xyz.us-west-2.es.amazonaws.com
  OS_INDEX=catalog_search_v1
"""
import os
import time
from decimal import Decimal
from collections import defaultdict
from typing import Dict, List, Any, Tuple

import boto3
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection, helpers
from openai import OpenAI

REGION   = os.getenv("AWS_REGION", "us-west-2")
#TABLE    = os.getenv("DDB_TABLE", "catalog_tentree")
OS_HOST  = "search-siloam-v01-qoptmnyzfw527t36u56xvzhsje.us-west-2.es.amazonaws.com"      
OS_INDEX = os.getenv("OS_INDEX", "catalog_search_v01_09072025")

# OpenAI client for embeddings
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------- OpenSearch client ----------
def make_os_client():
    session = boto3.Session(region_name=REGION)
    creds = session.get_credentials().get_frozen_credentials()
    awsauth = AWS4Auth(creds.access_key, creds.secret_key, REGION, "es", session_token=creds.token)
    return OpenSearch(
        hosts=[{"host": OS_HOST, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        max_retries=3,
        retry_on_timeout=True,
        timeout=120,
    )

# ---------- Optional: create index with minimal mapping ----------
def ensure_index(os_client: OpenSearch, index: str):
    if os_client.indices.exists(index=index):
        return
    mapping = {
        "settings": {
            "index": {"number_of_shards": 2, "number_of_replicas": 2}
        },
        "mappings": {
            "properties": {

                # Product-level fields (denormalized)
                "merchant":      {"type": "keyword"},
                "product_id":    {"type": "keyword"},
                "product_title": {"type": "text"},
                #"handle":        {"type": "keyword"},
                #"vendor":        {"type": "keyword"},
                #"product_type":  {"type": "keyword"},
                #"tags":          {"type": "keyword"},
                "product_age_group": {"type": "keyword"},
                "product_gender": {"type": "keyword"},
                "product_category": {"type": "keyword"},
                "featuredImage": {"type": "keyword"},
                "product_description":   {"type": "text"},
                "product_description_SILOAM": {"type": "text"},
                "product_description_embed": {"type": "dense_vector", "dims": 1536, "index": False},
                "product_description_SILOAM_embed": {"type": "dense_vector", "dims": 1536, "index": False},
                
                # Variant-level fields
                "variant_id":    {"type": "keyword"},
                "variant_title": {"type": "text"},
                #"variant_description": {"type": "text"},
                #"options":       {"type": "object", "dynamic": True},
                "price":         {"type": "double"},
                "available":     {"type": "boolean"},
                "image_url":     {"type": "keyword"},
                
                # Sibling awareness fields
                #"num_other_variants": {"type": "integer"},
                
                # Timestamp
                "updated_at":    {"type": "date", "format": "epoch_second"}
            }
        }
    }
    os_client.indices.create(index=index, body=mapping, ignore=400, request_timeout=120)
    print(f"Created index: {index}")

# ---------- Dynamo helpers ----------
# ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)

def ddb_scan_products(ddb) -> Dict[str, Dict[str, Any]]:
    """
    Build a lookup: product_gid -> product summary fields used for denormalization.
    """
    proj = "#p,SK,entity,shop_domain,product_gid,handle,title,vendor,productType,product_category,gender,age_group,tags,priceMin,priceMax,description,descriptionHtml,featuredImage,images,selectedOptions,seo"
    ean  = {"#p":"PK"}
    out: Dict[str, Dict[str, Any]] = {}
    eks = None
    while True:
        resp = ddb.scan(ProjectionExpression=proj, ExpressionAttributeNames=ean,
                        ExclusiveStartKey=eks) if eks else ddb.scan(ProjectionExpression=proj, ExpressionAttributeNames=ean)
        for it in resp["Items"]:
            if it.get("entity") == "product":
                pid = it.get("product_gid") or str(it.get("SK",""))[8:]
                out[pid] = {
                    "SK": it.get("SK"),
                    "description": it.get("description"),
                    "descriptionHtml": it.get("descriptionHtml"),
                    "featuredImage": it.get("featuredImage"),
                    "handle": it.get("handle"),
                    "images": it.get("images"),
                    "productType": it.get("productType"),
                    "selectedOptions": it.get("selectedOptions"),
                    "seo": it.get("seo"),
                    "shop_domain": it.get("shop_domain"),
                    "tags": it.get("tags"),
                    "product_title": it.get("title"),
                    "vendor": it.get("vendor"),
                    "product_category": it.get("product_category"),
                    "gender": it.get("gender"),
                    "age_group": it.get("age_group"),
                    "description_SILOAM": it.get("description_SILOAM"),
                }
        eks = resp.get("LastEvaluatedKey")
        if not eks:
            break
    return out

def ddb_scan_variants_grouped(ddb) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group variants by product_gid so we can compute num_other_variants & option summaries.
    returns: { product_gid: [variant_item, ...] }
    """
    proj = "#p,SK,entity,shop_domain,product_gid,variant_gid,title,selectedOptions,price,availableForSale,image,handle,vendor,productType,tags, description"
    ean  = {"#p":"PK"}
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    eks = None
    while True:
        resp = ddb.scan(ProjectionExpression=proj, ExpressionAttributeNames=ean,
                        ExclusiveStartKey=eks) if eks else ddb.scan(ProjectionExpression=proj, ExpressionAttributeNames=ean)
        for it in resp["Items"]:
            if it.get("entity") == "variant":
                groups[it.get("product_gid")].append(it)
        eks = resp.get("LastEvaluatedKey")
        if not eks:
            break
    return groups

def _to_float(x):
    if isinstance(x, Decimal): return float(x)
    return x if x is None or isinstance(x, (int,float)) else None

def _options_to_map(selected_options: List[Dict[str, Any]]) -> Dict[str, str]:
    """Convert [{'name':'Color','value':'Yellow'}, ...] -> {'Color':'Yellow', ...}"""
    out = {}
    if not isinstance(selected_options, list): return out
    for o in selected_options:
        n, v = (o or {}).get("name"), (o or {}).get("value")
        if n and v:
            out[str(n)] = str(v)
    return out

def _extract_image_url(image_data) -> str:
    """Safely extract image URL from DynamoDB image data"""
    if not image_data:
        return ""
    try:
        # Handle different possible structures
        if isinstance(image_data, dict):
            if "url" in image_data:
                url_data = image_data["url"]
                if isinstance(url_data, dict) and "S" in url_data:
                    return url_data["S"]  # DynamoDB String attribute
                elif isinstance(url_data, str):
                    return url_data
        return ""
    except:
        return ""

def _generate_embedding(text: str) -> List[float]:
    """Generate embedding for text using OpenAI's text-embedding-3-small model"""
    if not text or not text.strip():
        return []
    
    try:
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=text.strip()
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Error generating embedding for text '{text[:50]}...': {e}")
        return []

def summarize_other_options(all_variants: List[Dict[str, Any]],
                            current_variant_id: str) -> int:
    """
    Build:
      - other_options: union of values for each optionName across siblings (excludes this variant's value)
      - other_available_options: same but only siblings with availableForSale=True
      - num_other_variants
      - num_other_available_variants
    """
    all_sets = defaultdict(set)
    avail_sets = defaultdict(set)
    total = 0
    total_avail = 0
    # First pass: gather sets
    for v in all_variants:
        vid = v.get("variant_gid") or str(v.get("SK",""))[8:]
        opts = _options_to_map(v.get("selectedOptions", []))
        if vid == current_variant_id:
            # We'll subtract this one's values on the second pass
            continue
        total += 1
        if v.get("availableForSale"):
            total_avail += 1
            for n, val in opts.items():
                avail_sets[n].add(val)
        for n, val in opts.items():
            all_sets[n].add(val)

    # For "other_options" we already excluded the current variant (above).
    other_options = {n: sorted(list(vals)) for n, vals in all_sets.items()}
    other_available_options = {n: sorted(list(vals)) for n, vals in avail_sets.items()}
    return total

# ---------- Build & index docs ----------
def build_docs(products: Dict[str, Dict[str, Any]],
               grouped_variants: Dict[str, List[Dict[str, Any]]]):
    """
    Yield OpenSearch bulk actions for each variant doc.
    """
    now = int(time.time())
    total_variants = sum(len(vs) for vs in grouped_variants.values())
    processed = 0
    
    for product_id, var_list in grouped_variants.items():
        prod = products.get(product_id, {})
        for v in var_list:
            variant_id = v.get("variant_gid") or str(v.get("SK",""))[8:]
            # Summaries across siblings:
            other_variants = summarize_other_options(var_list, variant_id)

            # Generate embeddings for descriptions
            product_desc = prod.get("description") or ""
            product_desc_siloam = prod.get("description_SILOAM") or ""
            
            product_desc_embed = _generate_embedding(product_desc)
            product_desc_siloam_embed = _generate_embedding(product_desc_siloam)
            
            doc = {
                "_index": OS_INDEX,
                "_id": variant_id,
                "_source": {
                    # Product-level (denormalized)
                    "merchant":      prod.get("shop_domain") or "",
                    "product_id":    product_id,
                    "product_title": prod.get("product_title") or "",
                    "product_age_group": prod.get("age_group") or "",
                    "product_gender": prod.get("gender") or "",
                    "product_category": prod.get("product_category") or "",
                    #"handle":        prod.get("handle") or "",
                    #"vendor":        prod.get("vendor") or "",
                    #"tags":          prod.get("tags") or [],
                    "featuredImage": _extract_image_url(prod.get("featuredImage")),
                    "product_description":   product_desc,
                    "product_description_SILOAM": product_desc_siloam,
                    "product_description_embed": product_desc_embed,
                    "product_description_SILOAM_embed": product_desc_siloam_embed,

                    # Variant-level
                    "variant_id":    variant_id,
                    "variant_title": v.get("title") or "",
                    #"variant_description": v.get("description") or "",
                    #"options":       v.get("selectedOptions") or {},
                    "price":         _to_float(v.get("price")) or "",
                    "available":     bool(v.get("availableForSale", False)),
                    "image_url":     _extract_image_url(v.get("image")),

                    # New: sibling awareness
                    #"num_other_variants":           other_variants,


                    "updated_at": now
                }
            }
            
            processed += 1
            if processed % 100 == 0:
                print(f"Processed {processed}/{total_variants} variants...")
            
            yield doc

def main():
    brand_list = ["goodfair", "outrage", "tentree","aloyoga", "gruntstyle","knix", "misslola", "outdoorvoices", "pangaia", "rachelriley"] #, "allbirds","adored"
    os_client = make_os_client()
    ensure_index(os_client, OS_INDEX)

    for brand in brand_list:
        TABLE = os.getenv("DDB_TABLE", f"catalog_{brand}")
        ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
        print("Loading products…")
        products = ddb_scan_products(ddb)
        print(f"Products loaded: {len(products)}")

        print("Grouping variants…")
        grouped = ddb_scan_variants_grouped(ddb)
        total_variants = sum(len(vs) for vs in grouped.values())
        print(f"Products with variants: {len(grouped)} | Variants: {total_variants}")


        print("Indexing variant docs to OpenSearch…")
        actions = build_docs(products, grouped)
        helpers.bulk(
            os_client, 
            actions, 
            chunk_size=50, 
            request_timeout=120,
            max_retries=3,
            initial_backoff=2,
            max_backoff=600
        )
        print("Backfill complete.")

if __name__ == "__main__":
    main()
