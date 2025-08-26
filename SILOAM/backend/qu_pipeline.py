from openai import OpenAI
import os
import time
from decimal import Decimal
from collections import defaultdict
from typing import Dict, List, Any, Tuple
import boto3
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection, helpers
import json
import csv
from pydantic import BaseModel, Field, confloat
from typing import Any, Literal

client = OpenAI()
category_dict = {
"TOPS_TSHIRT": "Apparel > Tops > T-Shirts",
"TOPS_LONGSLEEVE_TSHIRT": "Apparel > Tops > Long-Sleeve T-Shirts",
"TOPS_POLO": "Apparel > Tops > Polos",
"TOPS_BUTTON_DOWN_SHIRT": "Apparel > Tops > Button-Down Shirts",
"TOPS_DRESS_SHIRT": "Apparel > Tops > Dress Shirts",
"TOPS_BLOUSE": "Apparel > Tops > Blouses",
"TOPS_TANK": "Apparel > Tops > Tanks",
"TOPS_CAMISOLE": "Apparel > Tops > Camisoles",
"TOPS_BODYSUIT": "Apparel > Tops > Bodysuits",
"TOPS_SWEATSHIRT": "Apparel > Tops > Sweatshirts",
"TOPS_HOODIE": "Apparel > Tops > Hoodies",
"TOPS_SWEATER": "Apparel > Tops > Sweaters",
"TOPS_CARDIGAN": "Apparel > Tops > Cardigans",
"TOPS_TURTLENECK": "Apparel > Tops > Turtlenecks",
"TOPS_TUNIC": "Apparel > Tops > Tunics",
"TOPS_CROP_TOP": "Apparel > Tops > Crop Tops",
"TOPS_VEST": "Apparel > Tops > Vests",
"TOPS_JERSEY": "Apparel > Tops > Jerseys",
"TOPS_RUGBY": "Apparel > Tops > Rugby Shirts",
"TOPS_PEPLUM": "Apparel > Tops > Peplum Tops",
"TOPS_HENLEY": "Apparel > Tops > Henleys",
"TOPS_BASE_LAYER_TOP": "Apparel > Tops > Base-Layer Tops",
"BOTTOMS_JEANS_SKINNY": "Apparel > Bottoms > Jeans - Skinny",
"BOTTOMS_JEANS_STRAIGHT": "Apparel > Bottoms > Jeans - Straight",
"BOTTOMS_JEANS_RELAXED": "Apparel > Bottoms > Jeans - Relaxed/Loose",
"BOTTOMS_JEANS_BOOTCUT": "Apparel > Bottoms > Jeans - Bootcut",
"BOTTOMS_JEANS_WIDE": "Apparel > Bottoms > Jeans - Wide/Baggy",
"BOTTOMS_CHINOS": "Apparel > Bottoms > Chinos",
"BOTTOMS_TROUSERS_DRESS": "Apparel > Bottoms > Dress Trousers",
"BOTTOMS_CARGO_PANTS": "Apparel > Bottoms > Cargo Pants",
"BOTTOMS_JOGGERS": "Apparel > Bottoms > Joggers/Track Pants",
"BOTTOMS_SWEATPANTS": "Apparel > Bottoms > Sweatpants",
"BOTTOMS_LEGGINGS": "Apparel > Bottoms > Leggings",
"BOTTOMS_YOGA_PANTS": "Apparel > Bottoms > Yoga Pants",
"BOTTOMS_SHORTS_CASUAL": "Apparel > Bottoms > Shorts - Casual",
"BOTTOMS_SHORTS_ATHLETIC": "Apparel > Bottoms > Shorts - Athletic",
"BOTTOMS_SHORTS_DRESS": "Apparel > Bottoms > Shorts - Dress",
"BOTTOMS_SKIRT_MINI": "Apparel > Bottoms > Skirts - Mini",
"BOTTOMS_SKIRT_MIDI": "Apparel > Bottoms > Skirts - Midi",
"BOTTOMS_SKIRT_MAXI": "Apparel > Bottoms > Skirts - Maxi",
"BOTTOMS_SKIRT_PENCIL": "Apparel > Bottoms > Skirts - Pencil",
"BOTTOMS_SKIRT_A_LINE": "Apparel > Bottoms > Skirts - A-Line",
"BOTTOMS_OVERALLS": "Apparel > Bottoms > Overalls/Dungarees",
"BOTTOMS_SARONG": "Apparel > Bottoms > Sarongs/Wraps",
"DRESSES_CASUAL": "Apparel > Dresses & One-Pieces > Casual Dresses",
"DRESSES_DAY": "Apparel > Dresses & One-Pieces > Day Dresses",
"DRESSES_COCKTAIL": "Apparel > Dresses & One-Pieces > Cocktail Dresses",
"DRESSES_EVENING": "Apparel > Dresses & One-Pieces > Evening Dresses",
"DRESSES_GOWN": "Apparel > Dresses & One-Pieces > Gowns",
"DRESSES_BODYCON": "Apparel > Dresses & One-Pieces > Bodycon Dresses",
"DRESSES_SHIRT_DRESS": "Apparel > Dresses & One-Pieces > Shirt Dresses",
"DRESSES_WRAP": "Apparel > Dresses & One-Pieces > Wrap Dresses",
"DRESSES_SUN": "Apparel > Dresses & One-Pieces > Sun Dresses",
"DRESSES_JUMPSUIT": "Apparel > Dresses & One-Pieces > Jumpsuits",
"DRESSES_ROMPER": "Apparel > Dresses & One-Pieces > Rompers/Playsuits",
"DRESSES_SUIT_SET": "Apparel > Dresses & One-Pieces > Two-Piece Sets",
"OUTERWEAR_DENIM_JACKET": "Apparel > Outerwear > Denim Jackets",
"OUTERWEAR_LEATHER_JACKET": "Apparel > Outerwear > Leather Jackets",
"OUTERWEAR_BOMBER_JACKET": "Apparel > Outerwear > Bomber Jackets",
"OUTERWEAR_WINDBREAKER": "Apparel > Outerwear > Windbreakers",
"OUTERWEAR_PUFFER_JACKET": "Apparel > Outerwear > Down/Puffer Jackets",
"OUTERWEAR_TRENCH_COAT": "Apparel > Outerwear > Trench Coats",
"OUTERWEAR_WOOL_COAT": "Apparel > Outerwear > Wool Coats",
"OUTERWEAR_PEA_COAT": "Apparel > Outerwear > Pea Coats",
"OUTERWEAR_PARKA": "Apparel > Outerwear > Parkas",
"OUTERWEAR_RAINCOAT": "Apparel > Outerwear > Raincoats",
"OUTERWEAR_BLAZER": "Apparel > Outerwear > Blazers",
"OUTERWEAR_VEST": "Apparel > Outerwear > Vests/Gilets",
"OUTERWEAR_CAPE_PONCHO": "Apparel > Outerwear > Capes & Ponchos",
"OUTERWEAR_FLEECE": "Apparel > Outerwear > Fleece Jackets",
"OUTERWEAR_SHACKET": "Apparel > Outerwear > Shirt Jackets/Shackets",
"ACTIVE_LEGGINGS": "Apparel > Activewear > Leggings/Tights",
"ACTIVE_SPORTS_BRA": "Apparel > Activewear > Sports Bras",
"ACTIVE_TRAINING_TOP": "Apparel > Activewear > Training Tops",
"ACTIVE_TRAINING_SHORTS": "Apparel > Activewear > Training Shorts",
"ACTIVE_TRACK_PANTS": "Apparel > Activewear > Track Pants",
"ACTIVE_RUNNING_JACKET": "Apparel > Activewear > Running Jackets",
"ACTIVE_RASH_GUARD": "Apparel > Activewear > Rash Guards",
"ACTIVE_CYCLING_JERSEY": "Apparel > Activewear > Cycling Jerseys",
"ACTIVE_BASE_LAYER": "Apparel > Activewear > Base Layers",
"ACTIVE_TENNIS_GOLF_DRESS": "Apparel > Activewear > Tennis/Golf Dresses",
"UNDERWEAR_BRA": "Apparel > Underwear & Hosiery > Bras",
"UNDERWEAR_BRALETTE": "Apparel > Underwear & Hosiery > Bralettes",
"UNDERWEAR_PANTIES_BRIEF": "Apparel > Underwear & Hosiery > Panties - Brief",
"UNDERWEAR_PANTIES_THONG": "Apparel > Underwear & Hosiery > Panties - Thong",
"UNDERWEAR_PANTIES_BIKINI": "Apparel > Underwear & Hosiery > Panties - Bikini",
"UNDERWEAR_BOXERS": "Apparel > Underwear & Hosiery > Boxers",
"UNDERWEAR_BOXER_BRIEFS": "Apparel > Underwear & Hosiery > Boxer Briefs",
"UNDERWEAR_BRIEFS": "Apparel > Underwear & Hosiery > Briefs",
"UNDERWEAR_LONG_JOHNS": "Apparel > Underwear & Hosiery > Long Johns",
"UNDERWEAR_THERMAL_TOP": "Apparel > Underwear & Hosiery > Thermal Tops",
"UNDERWEAR_UNDERSHIRT": "Apparel > Underwear & Hosiery > Undershirts",
"UNDERWEAR_SHAPEWEAR_TOP": "Apparel > Underwear & Hosiery > Shapewear - Tops",
"UNDERWEAR_SHAPEWEAR_BOTTOM": "Apparel > Underwear & Hosiery > Shapewear - Bottoms",
"HOSIERY_TIGHTS": "Apparel > Underwear & Hosiery > Tights",
"HOSIERY_STOCKINGS": "Apparel > Underwear & Hosiery > Stockings",
"HOSIERY_PANTYHOSE": "Apparel > Underwear & Hosiery > Pantyhose",
"SOCKS_CASUAL": "Apparel > Underwear & Hosiery > Socks - Casual",
"SOCKS_SPORTS": "Apparel > Underwear & Hosiery > Socks - Athletic",
"SOCKS_DRESS": "Apparel > Underwear & Hosiery > Socks - Dress",
"SOCKS_NO_SHOW": "Apparel > Underwear & Hosiery > Socks - No-Show/Invisible",
"SLEEP_PAJAMA_TOP": "Apparel > Sleep & Lounge > Pajama Tops",
"SLEEP_PAJAMA_BOTTOM": "Apparel > Sleep & Lounge > Pajama Bottoms",
"SLEEP_PAJAMA_SET": "Apparel > Sleep & Lounge > Pajama Sets",
"SLEEP_NIGHTGOWN": "Apparel > Sleep & Lounge > Nightgowns",
"SLEEP_ROBE": "Apparel > Sleep & Lounge > Robes",
"SLEEP_LOUNGE_TOP": "Apparel > Sleep & Lounge > Lounge Tops",
"SLEEP_LOUNGE_PANTS": "Apparel > Sleep & Lounge > Lounge Pants",
"SLEEP_SWEATSUIT_SET": "Apparel > Sleep & Lounge > Sweatsuit Sets",
"SWIM_TRUNKS": "Apparel > Swimwear > Swim Trunks",
"SWIM_BRIEFS": "Apparel > Swimwear > Swim Briefs",
"SWIM_BOARD_SHORTS": "Apparel > Swimwear > Board Shorts",
"SWIM_BIKINI_TOP": "Apparel > Swimwear > Bikini Tops",
"SWIM_BIKINI_BOTTOM": "Apparel > Swimwear > Bikini Bottoms",
"SWIM_ONE_PIECE": "Apparel > Swimwear > One-Piece Swimsuits",
"SWIM_COVER_UP": "Apparel > Swimwear > Cover-Ups",
"SWIM_RASH_GUARD": "Apparel > Swimwear > Rash Guards",
"FOOTWEAR_SNEAKERS": "Apparel > Footwear > Sneakers",
"FOOTWEAR_RUNNING_SHOES": "Apparel > Footwear > Running Shoes",
"FOOTWEAR_HIKING_BOOTS": "Apparel > Footwear > Hiking Boots",
"FOOTWEAR_BOOTS_ANKLE": "Apparel > Footwear > Boots - Ankle",
"FOOTWEAR_BOOTS_KNEE": "Apparel > Footwear > Boots - Knee-High",
"FOOTWEAR_BOOTS_CHELSEA": "Apparel > Footwear > Boots - Chelsea",
"FOOTWEAR_OXFORDS": "Apparel > Footwear > Oxfords",
"FOOTWEAR_DERBIES": "Apparel > Footwear > Derbies",
"FOOTWEAR_LOAFERS": "Apparel > Footwear > Loafers",
"FOOTWEAR_DRESS_SHOES": "Apparel > Footwear > Dress Shoes",
"FOOTWEAR_HEELS_PUMPS": "Apparel > Footwear > Heels - Pumps",
"FOOTWEAR_HEELS_SANDALS": "Apparel > Footwear > Heels - Sandals",
"FOOTWEAR_SANDALS": "Apparel > Footwear > Sandals",
"FOOTWEAR_FLIP_FLOPS": "Apparel > Footwear > Flip-Flops",
"FOOTWEAR_FLATS_BALLET": "Apparel > Footwear > Flats - Ballet",
"FOOTWEAR_MULES": "Apparel > Footwear > Mules",
"FOOTWEAR_CLOGS": "Apparel > Footwear > Clogs",
"FOOTWEAR_ESPADRILLES": "Apparel > Footwear > Espadrilles",
"FOOTWEAR_SLIPPERS": "Apparel > Footwear > Slippers",
"FOOTWEAR_WEDGES": "Apparel > Footwear > Wedges",
"ACCESSORIES_BASEBALL_CAP": "Apparel > Accessories > Hats - Baseball Caps",
"ACCESSORIES_BEANIE": "Apparel > Accessories > Hats - Beanies",
"ACCESSORIES_BUCKET_HAT": "Apparel > Accessories > Hats - Bucket",
"ACCESSORIES_WIDE_BRIM_HAT": "Apparel > Accessories > Hats - Wide Brim/Floppy",
"ACCESSORIES_VISOR": "Apparel > Accessories > Hats - Visors",
"ACCESSORIES_SCARF": "Apparel > Accessories > Scarves/Wraps",
"ACCESSORIES_GLOVES": "Apparel > Accessories > Gloves",
"ACCESSORIES_MITTENS": "Apparel > Accessories > Mittens",
"ACCESSORIES_BELT": "Apparel > Accessories > Belts",
"ACCESSORIES_TIE": "Apparel > Accessories > Ties",
"ACCESSORIES_BOW_TIE": "Apparel > Accessories > Bow Ties",
"ACCESSORIES_POCKET_SQUARE": "Apparel > Accessories > Pocket Squares",
"ACCESSORIES_SUSPENDERS": "Apparel > Accessories > Suspenders",
"ACCESSORIES_EARMUFFS": "Apparel > Accessories > Earmuffs",
"ACCESSORIES_SUNGLASSES": "Apparel > Accessories > Sunglasses",
"ACCESSORIES_EYEGLASS_FRAMES": "Apparel > Accessories > Eyeglass Frames",
"ACCESSORIES_WATCH": "Apparel > Accessories > Watches",
"ACCESSORIES_NECKLACE": "Apparel > Accessories > Jewelry - Necklaces",
"ACCESSORIES_BRACELET": "Apparel > Accessories > Jewelry - Bracelets",
"ACCESSORIES_EARRINGS": "Apparel > Accessories > Jewelry - Earrings",
"ACCESSORIES_RING": "Apparel > Accessories > Jewelry - Rings",
"ACCESSORIES_ANKLET": "Apparel > Accessories > Jewelry - Anklets",
"ACCESSORIES_BROOCH": "Apparel > Accessories > Brooches/Pins",
"ACCESSORIES_HAIR_ACCESSORY": "Apparel > Accessories > Hair Accessories",
"ACCESSORIES_WALLET": "Apparel > Accessories > Wallets",
"ACCESSORIES_CARD_HOLDER": "Apparel > Accessories > Card Holders",
"ACCESSORIES_KEYCHAIN": "Apparel > Accessories > Keychains",
"ACCESSORIES_HANDKERCHIEF": "Apparel > Accessories > Handkerchiefs",
"ACCESSORIES_FACE_MASK": "Apparel > Accessories > Face Masks",
"BAGS_TOTE": "Apparel > Bags & Luggage > Tote Bags",
"BAGS_SHOULDER": "Apparel > Bags & Luggage > Shoulder Bags",
"BAGS_CROSSBODY": "Apparel > Bags & Luggage > Crossbody Bags",
"BAGS_BACKPACK": "Apparel > Bags & Luggage > Backpacks",
"BAGS_DUFFEL": "Apparel > Bags & Luggage > Duffel Bags",
"BAGS_SATCHEL": "Apparel > Bags & Luggage > Satchels",
"BAGS_BELT_BAG": "Apparel > Bags & Luggage > Belt Bags/Fanny Packs",
"BAGS_BRIEFCASE": "Apparel > Bags & Luggage > Briefcases",
"BAGS_LAPTOP": "Apparel > Bags & Luggage > Laptop Bags",
"LUGGAGE_CARRY_ON": "Apparel > Bags & Luggage > Luggage - Carry-On",
"LUGGAGE_CHECKED": "Apparel > Bags & Luggage > Luggage - Checked",
"LUGGAGE_GARMENT_BAG": "Apparel > Bags & Luggage > Garment Bags",
"SPECIAL_MATERNITY_TOP": "Apparel > Specialty > Maternity Tops",
"SPECIAL_MATERNITY_BOTTOM": "Apparel > Specialty > Maternity Bottoms",
"SPECIAL_MATERNITY_DRESS": "Apparel > Specialty > Maternity Dresses",
"SPECIAL_ADAPTIVE_TOP": "Apparel > Specialty > Adaptive Tops",
"SPECIAL_ADAPTIVE_BOTTOM": "Apparel > Specialty > Adaptive Bottoms",
"SPECIAL_UNDER_SCRUB_TOP": "Apparel > Specialty > Medical Scrubs - Tops",
"SPECIAL_UNDER_SCRUB_PANTS": "Apparel > Specialty > Medical Scrubs - Pants",
"SPECIAL_WORK_OVERALLS": "Apparel > Specialty > Workwear Overalls/Coveralls",
"SPECIAL_APRON": "Apparel > Specialty > Aprons",
"SPECIAL_COSTUME": "Apparel > Specialty > Costumes",
"SPECIAL_FORMAL_SUIT_JACKET": "Apparel > Specialty > Suit Jackets/Blazers",
"SPECIAL_FORMAL_SUIT_PANTS": "Apparel > Specialty > Suit Pants",
"SPECIAL_FORMAL_SUIT_SKIRT": "Apparel > Specialty > Suit Skirts",
"SPECIAL_FORMAL_VEST": "Apparel > Specialty > Suit Vests",
"TRADITIONAL_KIMONO": "Apparel > Traditional & Cultural > Kimono",
"TRADITIONAL_HANBOK": "Apparel > Traditional & Cultural > Hanbok",
"TRADITIONAL_SARI": "Apparel > Traditional & Cultural > Sari",
"TRADITIONAL_QIPAO_CHEONGSAM": "Apparel > Traditional & Cultural > Qipao/Cheongsam",
"TRADITIONAL_KURTA": "Apparel > Traditional & Cultural > Kurta",
"TRADITIONAL_DIRNDL": "Apparel > Traditional & Cultural > Dirndl",
"TRADITIONAL_KILT": "Apparel > Traditional & Cultural > Kilt"
}   

category_list = list(category_dict.keys())

REGION   = os.getenv("AWS_REGION", "us-west-2")
OS_HOST  = "search-siloam-v01-qoptmnyzfw527t36u56xvzhsje.us-west-2.es.amazonaws.com"      
OS_INDEX = os.getenv("OS_INDEX", "catalog_search_v02")

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
def ddb_scan_products(ddb) -> Dict[str, Dict[str, Any]]:
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
                    "product_title": it.get("title"),
                    "vendor": it.get("vendor"),
                }
        eks = resp.get("LastEvaluatedKey")
        if not eks:
            break
    return out

class RouteDecision(BaseModel):
    product_category: Literal['TOPS_TSHIRT', 'TOPS_LONGSLEEVE_TSHIRT', 'TOPS_POLO', 'TOPS_BUTTON_DOWN_SHIRT', 'TOPS_DRESS_SHIRT', 'TOPS_BLOUSE', 'TOPS_TANK', 'TOPS_CAMISOLE', 'TOPS_BODYSUIT', 'TOPS_SWEATSHIRT', 'TOPS_HOODIE', 'TOPS_SWEATER', 'TOPS_CARDIGAN', 'TOPS_TURTLENECK', 'TOPS_TUNIC', 'TOPS_CROP_TOP', 'TOPS_VEST', 'TOPS_JERSEY', 'TOPS_RUGBY', 'TOPS_PEPLUM', 'TOPS_HENLEY', 'TOPS_BASE_LAYER_TOP', 'BOTTOMS_JEANS_SKINNY', 'BOTTOMS_JEANS_STRAIGHT', 'BOTTOMS_JEANS_RELAXED', 'BOTTOMS_JEANS_BOOTCUT', 'BOTTOMS_JEANS_WIDE', 'BOTTOMS_CHINOS', 'BOTTOMS_TROUSERS_DRESS', 'BOTTOMS_CARGO_PANTS', 'BOTTOMS_JOGGERS', 'BOTTOMS_SWEATPANTS', 'BOTTOMS_LEGGINGS', 'BOTTOMS_YOGA_PANTS', 'BOTTOMS_SHORTS_CASUAL', 'BOTTOMS_SHORTS_ATHLETIC', 'BOTTOMS_SHORTS_DRESS', 'BOTTOMS_SKIRT_MINI', 'BOTTOMS_SKIRT_MIDI', 'BOTTOMS_SKIRT_MAXI', 'BOTTOMS_SKIRT_PENCIL', 'BOTTOMS_SKIRT_A_LINE', 'BOTTOMS_OVERALLS', 'BOTTOMS_SARONG', 'DRESSES_CASUAL', 'DRESSES_DAY', 'DRESSES_COCKTAIL', 'DRESSES_EVENING', 'DRESSES_GOWN', 'DRESSES_BODYCON', 'DRESSES_SHIRT_DRESS', 'DRESSES_WRAP', 'DRESSES_SUN', 'DRESSES_JUMPSUIT', 'DRESSES_ROMPER', 'DRESSES_SUIT_SET', 'OUTERWEAR_DENIM_JACKET', 'OUTERWEAR_LEATHER_JACKET', 'OUTERWEAR_BOMBER_JACKET', 'OUTERWEAR_WINDBREAKER', 'OUTERWEAR_PUFFER_JACKET', 'OUTERWEAR_TRENCH_COAT', 'OUTERWEAR_WOOL_COAT', 'OUTERWEAR_PEA_COAT', 'OUTERWEAR_PARKA', 'OUTERWEAR_RAINCOAT', 'OUTERWEAR_BLAZER', 'OUTERWEAR_VEST', 'OUTERWEAR_CAPE_PONCHO', 'OUTERWEAR_FLEECE', 'OUTERWEAR_SHACKET', 'ACTIVE_LEGGINGS', 'ACTIVE_SPORTS_BRA', 'ACTIVE_TRAINING_TOP', 'ACTIVE_TRAINING_SHORTS', 'ACTIVE_TRACK_PANTS', 'ACTIVE_RUNNING_JACKET', 'ACTIVE_RASH_GUARD', 'ACTIVE_CYCLING_JERSEY', 'ACTIVE_BASE_LAYER', 'ACTIVE_TENNIS_GOLF_DRESS', 'UNDERWEAR_BRA', 'UNDERWEAR_BRALETTE', 'UNDERWEAR_PANTIES_BRIEF', 'UNDERWEAR_PANTIES_THONG', 'UNDERWEAR_PANTIES_BIKINI', 'UNDERWEAR_BOXERS', 'UNDERWEAR_BOXER_BRIEFS', 'UNDERWEAR_BRIEFS', 'UNDERWEAR_LONG_JOHNS', 'UNDERWEAR_THERMAL_TOP', 'UNDERWEAR_UNDERSHIRT', 'UNDERWEAR_SHAPEWEAR_TOP', 'UNDERWEAR_SHAPEWEAR_BOTTOM', 'HOSIERY_TIGHTS', 'HOSIERY_STOCKINGS', 'HOSIERY_PANTYHOSE', 'SOCKS_CASUAL', 'SOCKS_SPORTS', 'SOCKS_DRESS', 'SOCKS_NO_SHOW', 'SLEEP_PAJAMA_TOP', 'SLEEP_PAJAMA_BOTTOM', 'SLEEP_PAJAMA_SET', 'SLEEP_NIGHTGOWN', 'SLEEP_ROBE', 'SLEEP_LOUNGE_TOP', 'SLEEP_LOUNGE_PANTS', 'SLEEP_SWEATSUIT_SET', 'SWIM_TRUNKS', 'SWIM_BRIEFS', 'SWIM_BOARD_SHORTS', 'SWIM_BIKINI_TOP', 'SWIM_BIKINI_BOTTOM', 'SWIM_ONE_PIECE', 'SWIM_COVER_UP', 'SWIM_RASH_GUARD', 'FOOTWEAR_SNEAKERS', 'FOOTWEAR_RUNNING_SHOES', 'FOOTWEAR_HIKING_BOOTS', 'FOOTWEAR_BOOTS_ANKLE', 'FOOTWEAR_BOOTS_KNEE', 'FOOTWEAR_BOOTS_CHELSEA', 'FOOTWEAR_OXFORDS', 'FOOTWEAR_DERBIES', 'FOOTWEAR_LOAFERS', 'FOOTWEAR_DRESS_SHOES', 'FOOTWEAR_HEELS_PUMPS', 'FOOTWEAR_HEELS_SANDALS', 'FOOTWEAR_SANDALS', 'FOOTWEAR_FLIP_FLOPS', 'FOOTWEAR_FLATS_BALLET', 'FOOTWEAR_MULES', 'FOOTWEAR_CLOGS', 'FOOTWEAR_ESPADRILLES', 'FOOTWEAR_SLIPPERS', 'FOOTWEAR_WEDGES', 'ACCESSORIES_BASEBALL_CAP', 'ACCESSORIES_BEANIE', 'ACCESSORIES_BUCKET_HAT', 'ACCESSORIES_WIDE_BRIM_HAT', 'ACCESSORIES_VISOR', 'ACCESSORIES_SCARF', 'ACCESSORIES_GLOVES', 'ACCESSORIES_MITTENS', 'ACCESSORIES_BELT', 'ACCESSORIES_TIE', 'ACCESSORIES_BOW_TIE', 'ACCESSORIES_POCKET_SQUARE', 'ACCESSORIES_SUSPENDERS', 'ACCESSORIES_EARMUFFS', 'ACCESSORIES_SUNGLASSES', 'ACCESSORIES_EYEGLASS_FRAMES', 'ACCESSORIES_WATCH', 'ACCESSORIES_NECKLACE', 'ACCESSORIES_BRACELET', 'ACCESSORIES_EARRINGS', 'ACCESSORIES_RING', 'ACCESSORIES_ANKLET', 'ACCESSORIES_BROOCH', 'ACCESSORIES_HAIR_ACCESSORY', 'ACCESSORIES_WALLET', 'ACCESSORIES_CARD_HOLDER', 'ACCESSORIES_KEYCHAIN', 'ACCESSORIES_HANDKERCHIEF', 'ACCESSORIES_FACE_MASK', 'BAGS_TOTE', 'BAGS_SHOULDER', 'BAGS_CROSSBODY', 'BAGS_BACKPACK', 'BAGS_DUFFEL', 'BAGS_SATCHEL', 'BAGS_BELT_BAG', 'BAGS_BRIEFCASE', 'BAGS_LAPTOP', 'LUGGAGE_CARRY_ON', 'LUGGAGE_CHECKED', 'LUGGAGE_GARMENT_BAG', 'SPECIAL_MATERNITY_TOP', 'SPECIAL_MATERNITY_BOTTOM', 'SPECIAL_MATERNITY_DRESS', 'SPECIAL_ADAPTIVE_TOP', 'SPECIAL_ADAPTIVE_BOTTOM', 'SPECIAL_UNDER_SCRUB_TOP', 'SPECIAL_UNDER_SCRUB_PANTS', 'SPECIAL_WORK_OVERALLS', 'SPECIAL_APRON', 'SPECIAL_COSTUME', 'SPECIAL_FORMAL_SUIT_JACKET', 'SPECIAL_FORMAL_SUIT_PANTS', 'SPECIAL_FORMAL_SUIT_SKIRT', 'SPECIAL_FORMAL_VEST', 'TRADITIONAL_KIMONO', 'TRADITIONAL_HANBOK', 'TRADITIONAL_SARI', 'TRADITIONAL_QIPAO_CHEONGSAM', 'TRADITIONAL_KURTA', 'TRADITIONAL_DIRNDL', 'TRADITIONAL_KILT', 'OTHERS'] = Field(
        description="Pick the single best category for product from the available categories"
    )
    gender: Literal['MALE', 'FEMALE', 'UNISEX'] = Field(description="Pick the gender for the product from the available genders")
    age_group: Literal['CHILD', 'ADULT', 'BABY', 'ALL_AGES'] = Field(description="Pick the age group for the product from the available age groups")
    description: str = Field(description="The product description based on the image and the product description. DO NOT make up any information.")
    
    confidence_description: confloat(ge=0, le=1) =  Field(description="Confidence 0..1 for the product category decision based on the product description")
    confidence_image: confloat(ge=0, le=1) =  Field(description="Confidence 0..1 for the product category decision based on the product image")

def categorize_product(row: dict) -> str:
    # Parse the featuredImage if it's a string, otherwise use as-is
    featured_image = row['featuredImage']
    if isinstance(featured_image, str):
        featured_image = json.loads(featured_image)
    
    # Debug: print the structure
    print("featured_image:", featured_image)
    print("featured_image type:", type(featured_image))
    if 'url' in featured_image:
        print("url type:", type(featured_image['url']))
        print("url value:", featured_image['url'])
    
    # Safely extract the image URL
    if isinstance(featured_image.get('url'), dict) and 'S' in featured_image['url']:
        image_url = featured_image['url']['S']
    elif isinstance(featured_image.get('url'), str):
        image_url = featured_image['url']
    else:
        image_url = "No image URL found"
    
    print("imageurl", image_url)
    
    response = client.responses.parse(
        model="gpt-5-2025-08-07",
        input=[
            {
                "role": "user",
                "content": [
                    { "type": "input_text", "text": f"Pick the product category, gender, and age group from the following product description: {row}\n\n Also, analyze the image and use the image to help you make the decision." },
                    {
                        "type": "input_image",
                        "image_url": image_url
                    }
                ]
            }
        ],
        text_format=RouteDecision,
        timeout=120  # 2 minute timeout for image processing
    )
    return response.output_parsed

#"goodfair",
count:int = 0
os_client = make_os_client()
brand_list = [ "outrage", "tentree", "allbirds","adored","aloyoga", "gruntstyle","knix", "misslola", "outdoorvoices", "pangaia", "rachelriley"]
for brand in brand_list:
    TABLE = os.getenv("DDB_TABLE", f"catalog_{brand}")
    ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    print("Loading products…")
    products = ddb_scan_products(ddb)
    print(f"Products loaded: {len(products)}")
    for key, product in products.items():
        try:
            print("key", key)
            print("--------------------------------")
            res = categorize_product(product)
            print("Category:", res.product_category)
            print("Gender:", res.gender)
            print("Age Group:", res.age_group)
            print("Description:", res.description)
            print("Confidence (Description):", res.confidence_description)
            print("Confidence (Image):", res.confidence_image)
            print("--------------------------------")
            count += 1

            with open(f"category_results_{brand}.json", "a") as f:
                f.write(json.dumps(key))
                f.write(json.dumps(res.model_dump(), indent=4))
            print("--------------------------------")
            print("--------------------------------")
            print("--------------------------------")
            print(f"Processed {count} products")

            print(f"Error processing product {key}: {e}")
            print("--------------------------------")
            print("--------------------------------")
            continue
        except Exception as e:
            print(f"Error processing product {key}: {e}")
            print("--------------------------------")
            print("--------------------------------")
            continue



