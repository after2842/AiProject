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

REGION   = os.getenv("AWS_REGION", "us-west-2")
TABLE    = os.getenv("DDB_TABLE", "catalog_5x")
OS_HOST  = os.environ["OS_HOST"]              # e.g. search-...es.amazonaws.com
OS_INDEX = os.getenv("OS_INDEX", "catalog_search_v1")

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
    )

# ---------- Optional: create index with minimal mapping ----------
def ensure_index(os_client: OpenSearch, index: str):
    if os_client.indices.exists(index):
        return
    mapping = {
        "settings": {
            "index": {"number_of_shards": 2, "number_of_replicas": 1}
        },
        "mappings": {
            "properties": {
                "merchant":      {"type":"keyword"},
                "product_id":    {"type":"keyword"},
                "variant_id":    {"type":"keyword"},
                "product_title": {"type":"text"},
                "handle":        {"type":"keyword"},
                "vendor":        {"type":"keyword"},
                "product_type":  {"type":"keyword"},
                "tags":          {"type":"keyword"},
                "title":         {"type":"text"},
                "options": {
                    "properties": {"name":{"type":"keyword"}, "value":{"type":"keyword"}}
                },
                "price":         {"type":"double"},
                "available":     {"type":"boolean"},
                "image_url":     {"type":"keyword"},
                "alt_text_all":  {"type":"text"},
                "min_price":     {"type":"double"},
                "max_price":     {"type":"double"},
                "num_other_variants":            {"type":"integer"},
                "num_other_available_variants":  {"type":"integer"},
                # dynamic object; safe because option names are few (e.g., Size, Color)
                "other_options":                {"type":"object", "dynamic": True},
                "other_available_options":      {"type":"object", "dynamic": True},
                "updated_at":    {"type":"date", "format":"epoch_second"},
                # If you add semantic search later, add: "embedding": {"type":"knn_vector", "dimension": 768}
            }
        }
    }
    os_client.indices.create(index, body=mapping)
    print(f"Created index: {index}")

# ---------- Dynamo helpers ----------
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)

def ddb_scan_products() -> Dict[str, Dict[str, Any]]:
    """
    Build a lookup: product_gid -> product summary fields used for denormalization.
    """
    proj = "#p,SK,entity,shop_domain,product_gid,handle,title,vendor,productType,tags,priceMin,priceMax,description,descriptionHtml,featuredImage,images,selectedOptions,seo"
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
                    "title": it.get("title"),
                    "vendor": it.get("vendor"),
                }
        eks = resp.get("LastEvaluatedKey")
        if not eks:
            break
    return out

def ddb_scan_variants_grouped() -> Dict[str, List[Dict[str, Any]]]:
    """
    Group variants by product_gid so we can compute num_other_variants & option summaries.
    returns: { product_gid: [variant_item, ...] }
    """
    proj = "#p,SK,entity,shop_domain,product_gid,variant_gid,title,selectedOptions,price,availableForSale,image,a11y,handle,vendor,productType,tags"
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

def summarize_other_options(all_variants: List[Dict[str, Any]],
                            current_variant_id: str) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], int, int]:
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
    return other_options, other_available_options, total, total_avail

# ---------- Build & index docs ----------
def build_docs(products: Dict[str, Dict[str, Any]],
               grouped_variants: Dict[str, List[Dict[str, Any]]]):
    """
    Yield OpenSearch bulk actions for each variant doc.
    """
    now = int(time.time())
    for product_id, var_list in grouped_variants.items():
        prod = products.get(product_id, {})
        for v in var_list:
            variant_id = v.get("variant_gid") or str(v.get("SK",""))[8:]
            # Summaries across siblings:
            other_opts, other_avail_opts, n_other, n_other_avail = summarize_other_options(var_list, variant_id)

            doc = {
                "_index": OS_INDEX,
                "_id": variant_id,
                "_source": {
                    # Product-level (denormalized)
                    "merchant":      prod.get("shop_domain") or v.get("shop_domain"),
                    "product_id":    product_id,
                    "product_title": prod.get("product_title") or v.get("product_title") or v.get("title"),
                    "handle":        prod.get("handle") or v.get("handle"),
                    "vendor":        prod.get("vendor") or v.get("vendor"),
                    "product_type":  prod.get("product_type") or v.get("productType"),
                    "tags":          prod.get("tags") or v.get("tags") or [],


                    # Variant-level
                    "variant_id":    variant_id,
                    "title":         v.get("title"),
                    "options":       v.get("selectedOptions") or [],
                    "price":         _to_float(v.get("price")),
                    "available":     bool(v.get("availableForSale", True)),
                    "image_url":     ((v.get("image") or {}) or {}).get("url"),
                    "alt_text_all":  ((v.get("a11y") or {}) or {}).get("fit_note"),

                    # New: sibling awareness
                    "num_other_variants":           n_other,
                    "num_other_available_variants": n_other_avail,
                    "other_options":                other_opts,
                    "other_available_options":      other_avail_opts,

                    "updated_at":   now
                }
            }
            yield doc

def main():
    print("Loading products…")
    products = ddb_scan_products()
    print(f"Products loaded: {len(products)}")

    print("Grouping variants…")
    grouped = ddb_scan_variants_grouped()
    total_variants = sum(len(vs) for vs in grouped.values())
    print(f"Products with variants: {len(grouped)} | Variants: {total_variants}")

    os_client = make_os_client()
    ensure_index(os_client, OS_INDEX)

    print("Indexing variant docs to OpenSearch…")
    actions = build_docs(products, grouped)
    helpers.bulk(os_client, actions, chunk_size=1000, request_timeout=120)
    print("Backfill complete.")

if __name__ == "__main__":
    main()
