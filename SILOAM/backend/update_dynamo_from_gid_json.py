#!/usr/bin/env python3
"""
Update DynamoDB items (entity='product') with new attributes from a custom JSON file.

Input file format example (not valid JSON as a whole; it's a sequence of entries):
"gid://shopify/Product/3695228584013"{
    "product_category": "OTHERS",
    "gender": "UNISEX",
    "age_group": "ALL_AGES",
    "description": "...",
    "confidence_description": 0.99,
    "confidence_image": 0.99
}"gid://shopify/Product/6662779043917"{
    "product_category": "ACCESSORIES_BELT",
    "...": "..."
}

We parse each `"gid" { ... }` pair and apply a conditional UpdateItem:
- Only update if the item exists AND entity == 'product'.
- Add/set attributes from the JSON object ("adding columns" in DynamoDB terms).

Usage:
  python update_dynamo_from_gid_json.py \
      --table YourDynamoTable \
      --file category_results_YourDynamoTable.json \
      --region us-west-2 \
      --key-attr gid \
      [--sort-key sk --sort-value PRODUCT] \
      [--dry-run]

Requires:
  pip install boto3
  AWS credentials in your environment or config files.

"""

import argparse
import json
import os
import sys
from typing import Dict, Iterator, Tuple
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


def iter_gid_objects(raw: str) -> Iterator[Tuple[str, Dict]]:
    """
    Robustly iterate over pairs of:
      "gid://shopify/Product/123" { ...json... }
    in a long concatenated string.

    This handles arbitrary whitespace and JSON strings with escaped quotes.
    """
    i = 0
    n = len(raw)
    while True:
        # Find the next opening quote that looks like a gid
        start_q = raw.find('"gid://', i)
        if start_q == -1:
            return

        # Find closing quote
        j = start_q + 1
        while j < n and raw[j] != '"':
            j += 1
        if j >= n:
            # Malformed end
            return

        gid = raw[start_q + 1 : j]  # drop surrounding quotes

        # Skip whitespace to the next '{'
        k = j + 1
        while k < n and raw[k].isspace():
            k += 1
        if k >= n or raw[k] != '{':
            # Not followed by an object; move past this quote and continue
            i = j + 1
            continue

        # Parse a balanced JSON object starting at k
        depth = 0
        pos = k
        in_str = False
        escape = False
        while pos < n:
            ch = raw[pos]
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        # End of object
                        obj_str = raw[k : pos + 1]
                        try:
                            # Parse floats as Decimal so DynamoDB accepts numbers
                            data = json.loads(obj_str, parse_float=Decimal)
                        except json.JSONDecodeError as e:
                            snippet = obj_str[:200].replace("\n", " ")
                            raise ValueError(f"Failed to parse JSON object near gid={gid}: {e} | snippet={snippet!r}")
                        yield gid, data
                        i = pos + 1
                        break
            pos += 1
        else:
            raise ValueError(f"Unmatched braces for gid={gid}")


def build_update_expression(attr_map: Dict) -> Tuple[str, Dict, Dict]:
    """
    Build a DynamoDB UpdateExpression + ExpressionAttributeNames/Values
    that SETs all keys in attr_map.
    """
    if not attr_map:
        raise ValueError("attr_map is empty; nothing to update")

    set_clauses = []
    ean = {}  # ExpressionAttributeNames
    eav = {}  # ExpressionAttributeValues

    for idx, (k, v) in enumerate(attr_map.items()):
        # Skip nulls just in case
        if v is None:
            continue
        name_token = f"#k{idx}"
        value_token = f":v{idx}"
        ean[name_token] = k
        eav[value_token] = v
        set_clauses.append(f"{name_token} = {value_token}")

    if not set_clauses:
        raise ValueError("All values were None; nothing to update")

    update_expr = "SET " + ", ".join(set_clauses)
    return update_expr, ean, eav


def update_items_from_file(
    table_name: str,
    file_path: str,
    region: str,
    key_attr: str = "gid",
    sort_key: str = None,
    sort_value: str = None,
    dry_run: bool = False,
) -> None:
    """
    Parse the input file and UpdateItem for each gid with conditional checks.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()

    entries = list(iter_gid_objects(raw))
    print(f"[parse] Found {len(entries)} gid entries in {os.path.basename(file_path)}")

    session = boto3.session.Session(region_name=region)
    ddb = session.resource("dynamodb")
    table = ddb.Table(table_name)

    updated = 0
    skipped_conditional = 0
    failed = 0

    for idx, (gid, attr_map) in enumerate(entries, start=1):
        key = {key_attr: gid}
        if sort_key and sort_value is not None:
            key[sort_key] = sort_value

        try:
            update_expr, update_ean, update_eav = build_update_expression(attr_map)
        except ValueError as e:
            print(f"[skip] gid={gid} ({e})")
            skipped_conditional += 1
            continue

        # Explicit condition: item exists AND entity == 'product'
        cond_expr = "attribute_exists(#pk) AND #entity = :product"

        # Merge names/values from UpdateExpression with those required by the condition
        ean = {**update_ean, "#pk": key_attr, "#entity": "entity"}
        eav = {**update_eav, ":product": "product"}

        if dry_run:
            print(f"[dry-run] ({idx}/{len(entries)}) gid={gid} -> UpdateExpression:\n  {update_expr}")
            print(f"           ConditionExpression:\n  {cond_expr}")
            continue

        try:
            table.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ConditionExpression=cond_expr,
                ExpressionAttributeNames=ean,
                ExpressionAttributeValues=eav,
                ReturnValues="UPDATED_NEW",
            )
            updated += 1
            if updated % 25 == 0:
                print(f"[progress] updated {updated}/{len(entries)}")
        except ClientError as ce:
            code = ce.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                skipped_conditional += 1
                print(f"[skip] gid={gid} (conditional check failed: not entity='product' or item missing)")
            else:
                failed += 1
                print(f"[error] gid={gid} {code}: {ce}")

    print("\n[summary]")
    print(f"  parsed:   {len(entries)}")
    print(f"  updated:  {updated}")
    print(f"  skipped (condition): {skipped_conditional}")
    print(f"  failed:   {failed}")


def guess_default_file(table_name: str) -> str:
    return f"category_results_{table_name}.json"


def main():
    parser = argparse.ArgumentParser(description="Update DynamoDB items from category_results_{table}.json")
    parser.add_argument("--table", required=True, help="DynamoDB table name")
    parser.add_argument("--file", help="Path to the input file (default: category_results_{table}.json)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"), help="AWS region")
    parser.add_argument("--key-attr", default="gid", help="Partition key attribute name (default: gid)")
    parser.add_argument("--sort-key", default=None, help="(Optional) Sort key attribute name")
    parser.add_argument("--sort-value", default=None, help="(Optional) Sort key value (string)")
    parser.add_argument("--dry-run", action="store_true", help="Print planned updates without writing to DynamoDB")

    args = parser.parse_args()

    file_path = args.file or guess_default_file(args.table)

    update_items_from_file(
        table_name=args.table,
        file_path=file_path,
        region=args.region,
        key_attr=args.key_attr,
        sort_key=args.sort_key,
        sort_value=args.sort_value,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
