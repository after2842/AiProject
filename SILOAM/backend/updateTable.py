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

Overwrite/transform behavior is configurable per-attribute:
  --skip-keys description
      Ignore incoming keys entirely.
  --no-overwrite-keys description_SILOAM
      Use if_not_exists() so we only set when missing.
  --remap "description:description_SILOAM,old:new"
      Rename incoming keys on write (e.g., preserve original description
      and write JSON's description to description_SILOAM).

Usage example:
  python update_dynamo_from_gid_json.py \
      --table catalog_adored \
      --file category_results_adored.json \
      --region us-west-2 \
      --pk-attr-name PK \
      --sk-attr-name SK \
      --pk-literal "MERCHANT#www.adoredvintage.com" \
      --sk-prefix "PRODUCT#" \
      --remap "description:description_SILOAM"

"""

import argparse
import json
import os
from typing import Dict, Iterator, Tuple, Set
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


def parse_csv_list(val: str) -> Set[str]:
    if not val:
        return set()
    return {p.strip() for p in val.split(",") if p.strip()}


def parse_remap(val: str) -> Dict[str, str]:
    """Parse --remap 'a:b,c:d' into {'a': 'b', 'c': 'd'}"""
    mapping: Dict[str, str] = {}
    if not val:
        return mapping
    pairs = [p.strip() for p in val.split(",") if p.strip()]
    for pair in pairs:
        if ":" not in pair:
            raise ValueError(f"Bad --remap pair: {pair!r}. Expected 'src:dst'.")
        src, dst = pair.split(":", 1)
        src, dst = src.strip(), dst.strip()
        if not src or not dst:
            raise ValueError(f"Bad --remap pair: {pair!r}. Empty src/dst.")
        mapping[src] = dst
    return mapping


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


def transform_attrs(attr_map: Dict, skip_keys: Set[str], remap: Dict[str, str]) -> Dict:
    """
    Apply skip and remap rules to incoming attributes.
    - If key in skip_keys, drop it.
    - If key in remap, rename to remap[key].
    - If a destination key already exists (either from JSON or prior remap), keep the first value.
    """
    out: Dict[str, object] = {}
    # First, preserve any pre-existing dest keys from JSON itself
    for k, v in attr_map.items():
        if k in skip_keys:
            continue
        if k in remap.values():
            # Respect explicit presence of destination key in JSON
            if k not in out and v is not None:
                out[k] = v

    # Then, handle normal keys with remap
    for k, v in attr_map.items():
        if k in skip_keys or v is None:
            continue
        dest = remap.get(k, k)
        if dest in out:
            # Do not override an explicit or earlier value for the dest
            continue
        out[dest] = v
    return out


def build_update_expression(attr_map: Dict, no_overwrite_keys: Set[str]):
    """Build a SET UpdateExpression with per-key overwrite control."""
    set_parts = []
    ean = {}
    eav = {}
    idx = 0
    for k, v in attr_map.items():
        if v is None:
            continue
        nk = f"#k{idx}"
        nv = f":v{idx}"
        ean[nk] = k
        eav[nv] = v
        if k in no_overwrite_keys:
            set_parts.append(f"{nk} = if_not_exists({nk}, {nv})")
        else:
            set_parts.append(f"{nk} = {nv}")
        idx += 1

    if not set_parts:
        raise ValueError("No non-null attributes to update (after transform)")
    return "SET " + ", ".join(set_parts), ean, eav


def update_items_from_file(
    table_name: str,
    file_path: str,
    region: str,
    pk_attr_name: str,
    sk_attr_name: str,
    pk_literal: str,
    sk_prefix: str,
    skip_keys: Set[str],
    no_overwrite_keys: Set[str],
    remap: Dict[str, str],
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
            eff_attrs = transform_attrs(attrs, skip_keys, remap)
            update_expr, ue_names, ue_values = build_update_expression(eff_attrs, no_overwrite_keys)
        except ValueError as e:
            print(f"[skip] gid={gid}: {e}")
            continue

        key = {pk_attr_name: pk_literal, sk_attr_name: f"{sk_prefix}{gid}"}

        cond_expr = "attribute_exists(#pk) AND attribute_exists(#sk) AND begins_with(#sk, :skprefix)"

        ean = {**ue_names, "#pk": pk_attr_name, "#sk": sk_attr_name}
        eav = {**ue_values, ":skprefix": sk_prefix}

        if dry_run:
            print(f"[dry-run] ({idx}/{len(entries)}) Key={key}")
            print(f"  Effective attrs: {list(eff_attrs.keys())}")
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
    ap.add_argument("--skip-keys", default="", help="Comma-separated list of keys to ignore entirely (e.g., foo,bar)")
    ap.add_argument("--no-overwrite-keys", default="", help="Comma-separated list of keys to protect from overwrites; uses if_not_exists()")
    ap.add_argument("--remap", default="", help='Comma-separated list of "src:dst" pairs to rename incoming keys')
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    file_path = args.file or guess_default_file(args.table)

    remap = parse_remap(args.remap)
    skip_keys = parse_csv_list(args.skip_keys)
    no_overwrite_keys = parse_csv_list(args.no_overwrite_keys)

    update_items_from_file(
        table_name=args.table,
        file_path=file_path,
        region=args.region,
        pk_attr_name=args.pk_attr_name,
        sk_attr_name=args.sk_attr_name,
        pk_literal=args.pk_literal,
        sk_prefix=args.sk_prefix,
        skip_keys=skip_keys,
        no_overwrite_keys=no_overwrite_keys,
        remap=remap,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
