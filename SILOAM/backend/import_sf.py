#!/usr/bin/env python3
import os, sys, json, time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3, botocore
import requests

SHOP = "www.gotyu-underwear.com"#"global.shop.smtown.com"
if not SHOP:
    print("Set SHOPIFY_SHOP_DOMAIN (e.g., myshop.myshopify.com)", file=sys.stderr)
    sys.exit(2)

API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2025-07")
REGION = os.getenv("AWS_REGION", "us-west-2")
TABLE = os.getenv("DDB_TABLE", "catalog_gotyu-underwear")

SF_URL = f"https://{SHOP}/api/{API_VERSION}/graphql.json"
HDRS = {
    "Content-Type": "application/json",
    # Optional: can reduce 430 rejections when you proxy server-side
    "Shopify-Storefront-Buyer-IP": "203.0.113.10",
    # If the shop requires a token, uncomment and set it:
    # "X-Shopify-Storefront-Access-Token": os.getenv("SHOPIFY_STOREFRONT_TOKEN", ""),
}

PAGE_QUERY = """
query Page($first:Int!, $after:String) {
  products(first:$first, after:$after, sortKey:UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        handle
        title
        vendor
        productType
        tags
        description
        descriptionHtml
        seo { title description }
        featuredImage { url altText }
        images(first: 6) { edges { node { url altText } } }
        variants(first: 40) {
          edges {
            node {
              id
              title
              sku
              availableForSale
              price { amount currencyCode }
              image { url altText }
              selectedOptions { name value }
            }
          }
        }
      }
    }
  }
}
""".strip()

def gql(query: str, variables: dict) -> dict:

    r = requests.post(SF_URL, headers=HDRS, json={"query": query, "variables": variables}, timeout=30)
    if r.status_code in (429, 430):
        raise SystemExit(f"Storefront blocked or rate-limited (HTTP {r.status_code}). "
                         f"Add X-Shopify-Storefront-Access-Token or slow down.")
    r.raise_for_status()
    j = r.json()
    if "errors" in j:
        raise RuntimeError(j["errors"])
    return j["data"]

def to_decimal(s: Optional[str]) -> Optional[Decimal]:
    if s is None: return None
    return Decimal(str(s))

def ensure_table():
    ddb = boto3.client("dynamodb", region_name=REGION)
    try:
        ddb.describe_table(TableName=TABLE)
        return
    except ddb.exceptions.ResourceNotFoundException:
        pass
    print(f"Creating table {TABLE} …")
    ddb.create_table(
        TableName=TABLE,
        AttributeDefinitions=[
            {"AttributeName":"PK","AttributeType":"S"},
            {"AttributeName":"SK","AttributeType":"S"},
            {"AttributeName":"GSI1_var_search","AttributeType":"S"},
            {"AttributeName":"GSI1SK","AttributeType":"S"},
        ],
        KeySchema=[
            {"AttributeName":"PK","KeyType":"HASH"},
            {"AttributeName":"SK","KeyType":"RANGE"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName":"GSI1",
            "KeySchema":[
                {"AttributeName":"GSI1_var_search","KeyType":"HASH"},
                {"AttributeName":"GSI1SK","KeyType":"RANGE"},
            ],
            "Projection":{"ProjectionType":"ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=TABLE)
    print("Table ready.")

def flatten(edges, key="node"):
    return [e.get(key) for e in (edges or []) if e.get(key)]

def main():
    ensure_table()
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)

    after = None
    total_products = total_variants = 0
    pk = f"MERCHANT#{SHOP}"
    i:int = 1
    while True:
        time.sleep(1)
        try:
            print(f"Page {i}")
            data = gql(PAGE_QUERY, {"first": 25, "after": after})
            conn = data["products"]
            for edge in conn["edges"]:
                p = edge["node"]
                prod_gid = p["id"]
                product_item = {
                    "PK": pk,
                    "SK": f"PRODUCT#{prod_gid}",
                    "entity": "product",
                    "shop_domain": SHOP,
                    "product_gid": prod_gid,
                    "handle": p.get("handle"),
                    "title": p.get("title"),
                    "vendor": p.get("vendor"),
                    "productType": p.get("productType"),
                    "tags": p.get("tags") or [],
                    "description": p.get("description"),
                    "descriptionHtml": p.get("descriptionHtml"),
                    "seo": p.get("seo"),
                    "featuredImage": p.get("featuredImage") or {},
                    "images": flatten((p.get("images") or {}).get("edges", [])),
                    "GSI1_var_search": prod_gid,
                    "GSI1SK": f"HANDLE#{p.get('handle')}" if p.get("handle") else None,
                    "selectedOptions": p.get("selectedOptions") or [],
                }

                # Write product
                table.put_item(Item=product_item)
                total_products += 1

                # Variants
                for v in flatten((p.get("variants") or {}).get("edges", [])):
                    price = (v.get("price") or {}).get("amount")
                    variant_item = {
                        "PK": pk,
                        "SK": f"PRODUCT#{prod_gid}#VARIANT#{v['id']}",
                        "entity": "variant",
                        "shop_domain": SHOP,
                        "product_gid": prod_gid,
                        "variant_gid": v["id"],
                        "title": v.get("title"),
                        "sku": v.get("sku"),
                        "availableForSale": bool(v.get("availableForSale")),
                        "price": to_decimal(price),
                        "image": v.get("image") or {},
                        "GSI1_var_search": prod_gid,
                    }
                    table.put_item(Item=variant_item)
                    total_variants += 1
            i += 1

            if not conn["pageInfo"]["hasNextPage"]:
                break
            after = conn["pageInfo"]["endCursor"]
        except Exception as e:
            print(f"Error: {e}")
        

    print(f"Done: products={total_products}, variants={total_variants}")

if __name__ == "__main__":
    try:
        main()
    except botocore.exceptions.ClientError as e:
        print(f"AWS error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
