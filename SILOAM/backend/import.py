#!/usr/bin/env python3
import os, time, json, requests
from decimal import Decimal
from collections import defaultdict
import boto3

# ---------- env ----------
SHOP         = "www.paige.com"              # e.g. myshop.myshopify.com
ADMIN_TOKEN  = os.environ["ADMIN_TOKEN"]          # Admin API access token
API_VERSION  = os.getenv("API_VERSION", "2025-07")
REGION       = os.getenv("AWS_REGION", "us-west-2")
DDB_TABLE    = os.getenv("DDB_TABLE", "catalog")

ADMIN_URL = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
HDRS = {"Content-Type": "application/json", "X-Shopify-Access-Token": ADMIN_TOKEN}

# ---------- helpers ----------
def gql(query, variables=None):
    r = requests.post(ADMIN_URL, headers=HDRS, json={"query": query, "variables": variables or {}}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if "errors" in j: raise RuntimeError(j["errors"])
    return j["data"]

def to_decimal(x):
    return Decimal(str(x)) if x is not None else None

def parse_metaobject_fields(metaobj):
    """Flatten Metaobject.fields[] into a dict, preserving simple values and dereferencing MediaImage if present."""
    if not metaobj: return None
    out = {}
    for f in metaobj.get("fields", []):
        key = f.get("key")
        if not key: continue
        if f.get("reference"):
            ref = f["reference"]
            # Example: MediaImage -> image { url altText }
            if "image" in ref and ref["image"]:
                out[key] = {"image": {"url": ref["image"].get("url"), "altText": ref["image"].get("altText")}}
            else:
                out[key] = {"reference": ref}
        else:
            out[key] = f.get("value")
    return out

# ---------- bulk operation (products → variants → inventory levels + accessibility metafields/metaobjects) ----------
BULK_MUT = r'''
mutation run {
  bulkOperationRunQuery(
    query: """
    {
      products {
        edges {
          node {
            __typename
            id
            handle
            title
            description
            descriptionHtml
            vendor
            productType
            tags

            images(first: 10) {
              edges { node { url altText } }
            }

            # Targeted accessibility metafields (not a connection)
            a11y_summary: metafield(namespace: "accessibility", key: "summary") { value type }
            a11y_tactile: metafield(namespace: "accessibility", key: "tactile_features") { value type }

            # Metaobject reference example (stored in a metafield)
            a11y_details: metafield(namespace: "accessibility", key: "details") {
              type
              reference {
                ... on Metaobject {
                  id
                  type
                  fields {
                    key
                    value
                    reference {
                      ... on MediaImage { image { url altText } }
                    }
                  }
                }
              }
            }

            variants(first: 100) {
              edges {
                node {
                  __typename
                  id
                  sku
                  title
                  price { amount currencyCode }

                  a11y_fit: metafield(namespace: "accessibility", key: "fit_note") { value type }

                  inventoryItem {
                    id
                    inventoryLevels(first: 50) {
                      edges {
                        node {
                          __typename
                          id
                          location { id name }
                          quantities { name quantity }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
  ) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
'''.strip()

# ---------- run bulk op ----------
op = gql(BULK_MUT)["bulkOperationRunQuery"]
if op["userErrors"]:
    raise SystemExit(f"Bulk op errors: {op['userErrors']}")
print("Started bulk:", op["bulkOperation"])

STATUS_Q = "{ currentBulkOperation { id status errorCode url } }"
while True:
    s = gql(STATUS_Q)["currentBulkOperation"]
    print(s)
    if s["status"] in ("COMPLETED", "FAILED", "CANCELED"): break
    time.sleep(3)

if s["status"] != "COMPLETED":
    raise SystemExit(f"Bulk op not completed: {s}")

# ---------- stream JSONL ----------
resp = requests.get(s["url"], stream=True, timeout=300)
resp.raise_for_status()

ddb = boto3.resource("dynamodb", region_name=REGION).Table(DDB_TABLE)
shop_pk = f"MERCHANT#{SHOP}"

buffer = []
def flush():
    if not buffer: return
    with ddb.batch_writer(overwrite_by_pkeys=["PK","SK"]) as b:
        for it in buffer:
            b.put_item(Item=it)
    buffer.clear()

# We’ll see lines for:
# - Product (from products connection)
# - ProductVariant (from variants connection)
# - InventoryLevel (from inventoryLevels connection) with __parentId = inventoryItem.id
# Collect InventoryLevels by inventoryItemId, then attach to variants we saved (we store inventoryItemId on each variant).
inv_levels_by_parent = defaultdict(list)

# Temp store variants until we finish (to attach inventory levels)
variants_pending = {}

for line in resp.iter_lines():
    if not line: continue
    obj = json.loads(line)

    # Products
    if obj.get("__typename") == "Product":
        pid = obj["id"]
        images = [{"url": e["node"]["url"], "altText": e["node"].get("altText")}
                  for e in (obj.get("images") or {}).get("edges", [])]

        # a11y metafields
        a11y = {}
        if obj.get("a11y_summary"): a11y["summary"] = obj["a11y_summary"].get("value")
        if obj.get("a11y_tactile"): a11y["tactile_features"] = obj["a11y_tactile"].get("value")
        if obj.get("a11y_details") and obj["a11y_details"].get("reference"):
            a11y["details"] = parse_metaobject_fields(obj["a11y_details"]["reference"])

        product_item = {
            "PK": shop_pk,
            "SK": f"PRODUCT#{pid}",
            "entity": "product",
            "shop_domain": SHOP,
            "product_gid": pid,
            "handle": obj.get("handle"),
            "title": obj.get("title"),
            "description": obj.get("description"),
            "descriptionHtml": obj.get("descriptionHtml"),
            "vendor": obj.get("vendor"),
            "productType": obj.get("productType"),
            "tags": obj.get("tags") or [],
            "images": images,
            "a11y": a11y or None,
            # Optional GSI for handle:
            "GSI1PK": shop_pk,
            "GSI1SK": f"HANDLE#{obj.get('handle')}" if obj.get("handle") else None
        }
        buffer.append(product_item)

        if len(buffer) >= 100:
            flush()

    # Variants
    elif obj.get("__typename") == "ProductVariant":
        vid = obj["id"]
        inv_item = (obj.get("inventoryItem") or {})
        inv_item_id = inv_item.get("id")

        variant_item = {
            "PK": shop_pk,
            "SK": f"VARIANT#{vid}",
            "entity": "variant",
            "shop_domain": SHOP,
            "product_gid": obj["__parentId"],    # parent is the Product
            "variant_gid": vid,
            "sku": obj.get("sku"),
            "title": obj.get("title"),
            "price": to_decimal(((obj.get("price") or {}).get("amount"))),
            "inventoryItemId": inv_item_id,
        }

        # variant-level a11y metafield
        if obj.get("a11y_fit"):
            variant_item.setdefault("a11y", {})["fit_note"] = obj["a11y_fit"].get("value")

        # We can't attach inventory levels until we parse their lines; store pending
        variants_pending[vid] = variant_item

    # Inventory levels (children of inventoryItem)
    elif obj.get("__typename") == "InventoryLevel":
        parent_inv_item_id = obj.get("__parentId")  # the inventoryItem.id
        loc = (obj.get("location") or {})
        qty_map = {q["name"]: q["quantity"] for q in (obj.get("quantities") or [])}
        inv_levels_by_parent[parent_inv_item_id].append({
            "location_id": loc.get("id"),
            "location": loc.get("name"),
            "available": qty_map.get("available")
        })

# Attach inventory levels to variants and flush them
for vid, v in variants_pending.items():
    levels = inv_levels_by_parent.get(v.get("inventoryItemId"))
    if levels: v["inventoryLevels"] = levels
    buffer.append(v)
    if len(buffer) >= 100:
        flush()
flush()

print(f"Bulk ingest complete. Products: written; Variants: {len(variants_pending)} with inventory levels attached where present.")
