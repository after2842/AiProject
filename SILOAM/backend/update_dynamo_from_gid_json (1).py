#!/usr/bin/env python3
"""
Update DynamoDB items with additional attributes for *product* rows, where the
table schema is a composite key like:

  PK = "MERCHANT#<shop_domain>"
  SK = "PRODUCT#<gid>"            (variants would be "PRODUCT#<gid>#VARIANT#...")

This script parses input files of the form:
"gid://shopify/Product/369..." { ...json... } "gid://shopify/Product/666..." { ... }

and runs UpdateItem to "add columns" (set attributes) only on the product items.

Key mapping is fully configurable via flags:

  --pk-attr-name PK
  --sk-attr-name SK
  --pk-literal "MERCHANT#www.adoredvintage.com"
  --sk-prefix "PRODUCT#"

The SK will be constructed as: f"{sk_prefix}{gid}" (no variant suffix).
This cleanly targets the base product row.

ConditionExpression protects updates:
  attribute_exists(#pk) AND attribute_exists(#sk)
  AND begins_with(#sk, :skprefix)

Usage:
  python update_dynamo_from_gid_json.py \
      --table catalog_adored \
      --file category_results_adored.json \
      --region us-west-2 \
      --pk-attr-name PK \
      --sk-attr-name SK \
      --pk-literal "MERCHANT#www.adoredvintage.com" \
      --sk-prefix "PRODUCT#"

"""

import argparse
import json
import os
from typing import Dict, Iterator, Tuple
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


def iter_gid_objects(raw: str) -> Iterator[Tuple[str, Dict]]:
    """Yield (gid, obj) from a concatenated string of `"gid"... {json} "gid"... {json}`."""
    i = 0
    n = len(raw)
    while True:
        start_q = raw.find('"gid://', i)
        if start_q == -1:
            return
        j = start_q + 1
        while j < n and raw[j] != '"':
            j += 1
        if j >= n:
            return
        gid = raw[start_q + 1 : j]

        k = j + 1
        while k < n and raw[k].isspace():
            k += 1
        if k >= n or raw[k] != '{':
            i = j + 1
            continue

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
                        obj_str = raw[k : pos + 1]
                        try:
                            data = json.loads(obj_str, parse_float=Decimal)
                        except json.JSONDecodeError as e:
                            snippet = obj_str[:200].replace("\n", " ")
                            raise ValueError(f"JSON parse error near gid={gid}: {e} | snippet={snippet!r}")
                        yield gid, data
                        i = pos + 1
                        break
            pos += 1
        else:
            raise ValueError(f"Unmatched braces for gid={gid}")


def build_update_expression(attr_map: Dict):
    """Build a SET UpdateExpression and attribute maps from attr_map."""
    set_parts = []
    ean = {}
    eav = {}
    for idx, (k, v) in enumerate(attr_map.items()):
        if v is None:
            continue
        nk = f"#k{idx}"
        nv = f":v{idx}"
        ean[nk] = k
        eav[nv] = v
        set_parts.append(f"{nk} = {nv}")
    if not set_parts:
        raise ValueError("No non-null attributes to update")
    return "SET " + ", ".join(set_parts), ean, eav


def update_items_from_file(
    table_name: str,
    file_path: str,
    region: str,
    pk_attr_name: str,
    sk_attr_name: str,
    pk_literal: str,
    sk_prefix: str,
    dry_run: bool = False,
):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    raw = open(file_path, "r", encoding="utf-8").read()
    entries = list(iter_gid_objects(raw))
    print(f"[parse] Found {len(entries)} gid entries in {os.path.basename(file_path)}")

    session = boto3.session.Session(region_name=region)
    ddb = session.resource("dynamodb")
    table = ddb.Table(table_name)

    updated = 0
    failed = 0

    for idx, (gid, attrs) in enumerate(entries, 1):
        try:
            update_expr, ue_names, ue_values = build_update_expression(attrs)
        except ValueError as e:
            print(f"[skip] gid={gid}: {e}")
            continue

        key = {pk_attr_name: pk_literal, sk_attr_name: f"{sk_prefix}{gid}"}

        cond_expr = "attribute_exists(#pk) AND attribute_exists(#sk) AND begins_with(#sk, :skprefix)"

        ean = {**ue_names, "#pk": pk_attr_name, "#sk": sk_attr_name}
        eav = {**ue_values, ":skprefix": sk_prefix}

        if dry_run:
            print(f"[dry-run] ({idx}/{len(entries)}) Key={key}")
            print(f"  UpdateExpression: {update_expr}")
            print(f"  Condition: {cond_expr}")
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
            failed += 1
            code = ce.response.get("Error", {}).get("Code")
            msg = ce.response.get("Error", {}).get("Message")
            print(f"[error] gid={gid} {code}: {msg}")

    print("\n[summary]")
    print(f"  updated: {updated}")
    print(f"  failed:  {failed}")


def guess_default_file(table_name: str) -> str:
    return f"category_results_{table_name}.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--file", help="Path to input file; default category_results_{table}.json")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    ap.add_argument("--pk-attr-name", default="PK")
    ap.add_argument("--sk-attr-name", default="SK")
    ap.add_argument("--pk-literal", required=True, help='Literal value for PK, e.g., "MERCHANT#www.adoredvintage.com"')
    ap.add_argument("--sk-prefix", default="PRODUCT#", help='Prefix for SK, default "PRODUCT#" -> "PRODUCT#<gid>"')
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    file_path = args.file or guess_default_file(args.table)

    update_items_from_file(
        table_name=args.table,
        file_path=file_path,
        region=args.region,
        pk_attr_name=args.pk_attr_name,
        sk_attr_name=args.sk_attr_name,
        pk_literal=args.pk_literal,
        sk_prefix=args.sk_prefix,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
