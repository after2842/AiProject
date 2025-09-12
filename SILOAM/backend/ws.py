import os
import json
import asyncio
import base64
from typing import Optional
import contextlib
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Literal, List
from pydantic import Field, confloat
from openai import OpenAI
from openai import AsyncOpenAI
import boto3
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection
# ---------- CONFIG ----------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise SystemExit("Set OPENAI_API_KEY")

OAI_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

# If your clients also send binary PCM (not base64 JSON), set this to True to encode on the server:
ALLOW_BINARY_PCM_FROM_CLIENT = True  # client may send raw PCM16 (24k mono) as binary frames

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
LOW_LEVEL_CATEGORY_MAP = {
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
HIGH_LEVEL_CATEGORY_MAP = {
    "TOPS": {
        "label": "Apparel > Tops",
        "prefixes": ["TOPS_"]
    },
    "BOTTOMS": {
        "label": "Apparel > Bottoms",
        "prefixes": ["BOTTOMS_"]
    },
    "DRESSES_ONEPIECES": {
        "label": "Apparel > Dresses & One-Pieces",
        "prefixes": ["DRESSES_"]
    },
    "OUTERWEAR": {
        "label": "Apparel > Outerwear",
        "prefixes": ["OUTERWEAR_"]
    },
    "ACTIVEWEAR": {
        "label": "Apparel > Activewear",
        "prefixes": ["ACTIVE_"]
    },
    "UNDERWEAR_HOSIERY_SOCKS": {
        "label": "Apparel > Underwear, Hosiery & Socks",
        "prefixes": ["UNDERWEAR_", "HOSIERY_", "SOCKS_"]
    },
    "SLEEP_LOUNGE": {
        "label": "Apparel > Sleep & Lounge",
        "prefixes": ["SLEEP_"]
    },
    "SWIMWEAR": {
        "label": "Apparel > Swimwear",
        "prefixes": ["SWIM_"]
    },
    "FOOTWEAR": {
        "label": "Apparel > Footwear",
        "prefixes": ["FOOTWEAR_"]
    },
    "ACCESSORIES_JEWELRY": {
        "label": "Apparel > Accessories & Jewelry",
        "prefixes": ["ACCESSORIES_"]
    },
    "BAGS_LUGGAGE": {
        "label": "Apparel > Bags & Luggage",
        "prefixes": ["BAGS_", "LUGGAGE_"]
    },
    "SPECIALTY": {
        "label": "Apparel > Specialty & Formalwear",
        "prefixes": ["SPECIAL_"]
    },
    "TRADITIONAL_CULTURAL": {
        "label": "Apparel > Traditional & Cultural",
        "prefixes": ["TRADITIONAL_"]
    }
}
session = boto3.Session(region_name="us-west-2")
creds = session.get_credentials().get_frozen_credentials()
auth = AWS4Auth(creds.access_key, creds.secret_key, "us-west-2", "es", session_token=creds.token)

os_client = OpenSearch(
    hosts=[{"host": os.getenv("OS_HOST"), "port": 443}],
    http_auth=auth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
)


@app.get("/")
def root():
    return {"ok": True, "ws": "/ws"}

async def _open_openai_ws() -> websockets.WebSocketClientProtocol:
    """
    Open a WebSocket to OpenAI Realtime (transcription intent),
    and immediately configure the session with your STT model.
    """
    ws = await websockets.connect(
        OAI_URL,
        additional_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        max_size=1 << 24,
    )

    # MUST: pick the transcription model at session start
    # https://platform.openai.com/docs/api-reference/realtime-client-events/session/update
    session_update = {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "transcription": {"model": "gpt-4o-transcribe"}
                }
            }
        },
    }
    await ws.send(json.dumps(session_update))
    return ws

async def _pump_client_to_openai(client_ws: WebSocket,
                                 openai_ws: websockets.WebSocketClientProtocol):
    """
    Forward audio-chunk messages coming from your client to OpenAI.
    Accepts two styles:
      1) JSON: {"type":"input_audio_buffer.append","audio":"<base64 pcm>"}
      2) Binary: raw PCM16 bytes (we base64-encode + wrap for you)
    Also forwards commit/clear if your client sends them.
    """
    while True:
        msg = await client_ws.receive()

        # Binary payload from client
        if msg["type"] == "websocket.receive" and msg.get("bytes") is not None:
            #print("client sent binary")
            if not ALLOW_BINARY_PCM_FROM_CLIENT:
                # ignore or you can send back an error JSON
                continue
            raw = msg["bytes"]
            # Wrap to OpenAI's append event with base64 audio (server-side VAD will handle turn)
            evt = {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(raw).decode("ascii"),
            }
            await openai_ws.send(json.dumps(evt))
            continue

        # Text (JSON) payload from client
        if msg["type"] == "websocket.receive" and msg.get("text"):
            print("client sent text")
            try:
                obj = json.loads(msg["text"])
            except Exception:
                # ignore invalid text
                continue

            mtype = obj.get("type", "")

            if mtype == "input_audio_buffer.append":
                # Already base64 from client; forward as-is
                await openai_ws.send(json.dumps(obj))
                continue

            # Forward commit/clear if you drive turns from client side
            if mtype in ("input_audio_buffer.commit", "input_audio_buffer.clear"):
                await openai_ws.send(json.dumps(obj))
                continue

            # Your app may have other messages; ignore or handle here
            # e.g., {"type":"debug"} etc.
            continue

        # Client disconnect or other frames
        if msg["type"] in ("websocket.disconnect", "websocket.close"):
            raise WebSocketDisconnect()


async def stream_tts(text: str, client_ws: WebSocket):
    await client_ws.send_text(json.dumps({"type": "tts.start"}))
    try:
        async with AsyncOpenAI(api_key=OPENAI_API_KEY).audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts", voice="coral", input=text, response_format="pcm"
        ) as resp:
            print("resp: ", resp)
            async for chunk in resp.iter_bytes():
                #print(f"Sending audio chunk: {len(chunk)} bytes")
                await client_ws.send_bytes(chunk)
    finally:
        await client_ws.send_text(json.dumps({"type": "tts.end"}))

class RouteDecision(BaseModel):
    request_type: Literal["find_product", "add_to_cart","go_to_cart", "go_to_profile", "other"] = Field(
        description="Pick the single best category for the user's request."
    )
    # high_level_category: Literal["TOPS", "BOTTOMS", "DRESSES_ONEPIECES", "OUTERWEAR", "ACTIVEWEAR", "UNDERWEAR_HOSIERY_SOCKS", "SLEEP_LOUNGE", "SWIMWEAR", "FOOTWEAR", "ACCESSORIES_JEWELRY", "BAGS_LUGGAGE", "SPECIALTY", "TRADITIONAL_CULTURAL"] = Field(
    #     description="Pick the single best high level category for the user's request."
    # )
    low_level_category: List[Literal["TOPS_TSHIRT", "TOPS_LONGSLEEVE_TSHIRT", "TOPS_POLO", "TOPS_BUTTON_DOWN_SHIRT", "TOPS_DRESS_SHIRT", "TOPS_BLOUSE", "TOPS_TANK", "TOPS_CAMISOLE", "TOPS_BODYSUIT", "TOPS_SWEATSHIRT", "TOPS_HOODIE", "TOPS_SWEATER", "TOPS_CARDIGAN", "TOPS_TURTLENECK", "TOPS_TUNIC", "TOPS_CROP_TOP", "TOPS_VEST", "TOPS_JERSEY", "TOPS_RUGBY", "TOPS_PEPLUM", "TOPS_HENLEY", "TOPS_BASE_LAYER_TOP", "BOTTOMS_JEANS_SKINNY", "BOTTOMS_JEANS_STRAIGHT", "BOTTOMS_JEANS_RELAXED", "BOTTOMS_JEANS_BOOTCUT", "BOTTOMS_JEANS_WIDE", "BOTTOMS_CHINOS", "BOTTOMS_TROUSERS_DRESS", "BOTTOMS_CARGO_PANTS", "BOTTOMS_JOGGERS", "BOTTOMS_SWEATPANTS", "BOTTOMS_LEGGINGS", "BOTTOMS_YOGA_PANTS", "BOTTOMS_SHORTS_CASUAL", "BOTTOMS_SHORTS_ATHLETIC", "BOTTOMS_SHORTS_DRESS", "BOTTOMS_SKIRT_MINI", "BOTTOMS_SKIRT_MIDI", "BOTTOMS_SKIRT_MAXI", "BOTTOMS_SKIRT_PENCIL", "BOTTOMS_SKIRT_A_LINE", "BOTTOMS_OVERALLS", "BOTTOMS_SARONG", "DRESSES_CASUAL", "DRESSES_DAY", "DRESSES_COCKTAIL", "DRESSES_EVENING", "DRESSES_GOWN", "DRESSES_BODYCON", "DRESSES_SHIRT_DRESS", "DRESSES_WRAP", "DRESSES_SUN", "DRESSES_JUMPSUIT", "DRESSES_ROMPER", "DRESSES_SUIT_SET", "OUTERWEAR_DENIM_JACKET", "OUTERWEAR_LEATHER_JACKET", "OUTERWEAR_BOMBER_JACKET", "OUTERWEAR_WINDBREAKER", "OUTERWEAR_PUFFER_JACKET", "OUTERWEAR_TRENCH_COAT", "OUTERWEAR_WOOL_COAT", "OUTERWEAR_PEA_COAT", "OUTERWEAR_PARKA", "OUTERWEAR_RAINCOAT", "OUTERWEAR_BLAZER", "OUTERWEAR_VEST", "OUTERWEAR_CAPE_PONCHO", "OUTERWEAR_FLEECE", "OUTERWEAR_SHACKET", "ACTIVE_LEGGINGS", "ACTIVE_SPORTS_BRA", "ACTIVE_TRAINING_TOP", "ACTIVE_TRAINING_SHORTS", "ACTIVE_TRACK_PANTS", "ACTIVE_RUNNING_JACKET", "ACTIVE_RASH_GUARD", "ACTIVE_CYCLING_JERSEY", "ACTIVE_BASE_LAYER", "ACTIVE_TENNIS_GOLF_DRESS", "UNDERWEAR_BRA", "UNDERWEAR_BRALETTE", "UNDERWEAR_PANTIES_BRIEF", "UNDERWEAR_PANTIES_THONG", "UNDERWEAR_PANTIES_BIKINI", "UNDERWEAR_BOXERS", "UNDERWEAR_BOXER_BRIEFS", "UNDERWEAR_BRIEFS", "UNDERWEAR_LONG_JOHNS", "UNDERWEAR_THERMAL_TOP", "UNDERWEAR_UNDERSHIRT", "UNDERWEAR_SHAPEWEAR_TOP", "UNDERWEAR_SHAPEWEAR_BOTTOM", "HOSIERY_TIGHTS", "HOSIERY_STOCKINGS", "HOSIERY_PANTYHOSE", "SOCKS_CASUAL", "SOCKS_SPORTS", "SOCKS_DRESS", "SOCKS_NO_SHOW", "SLEEP_PAJAMA_TOP", "SLEEP_PAJAMA_BOTTOM", "SLEEP_PAJAMA_SET", "SLEEP_NIGHTGOWN", "SLEEP_ROBE", "SLEEP_LOUNGE_TOP", "SLEEP_LOUNGE_PANTS", "SLEEP_SWEATSUIT_SET", "SWIM_TRUNKS", "SWIM_BRIEFS", "SWIM_BOARD_SHORTS", "SWIM_BIKINI_TOP", "SWIM_BIKINI_BOTTOM", "SWIM_ONE_PIECE", "SWIM_COVER_UP", "SWIM_RASH_GUARD", "FOOTWEAR_SNEAKERS", "FOOTWEAR_RUNNING_SHOES", "FOOTWEAR_HIKING_BOOTS", "FOOTWEAR_BOOTS_ANKLE", "FOOTWEAR_BOOTS_KNEE", "FOOTWEAR_BOOTS_CHELSEA", "FOOTWEAR_OXFORDS", "FOOTWEAR_DERBIES", "FOOTWEAR_LOAFERS", "FOOTWEAR_DRESS_SHOES", "FOOTWEAR_HEELS_PUMPS", "FOOTWEAR_HEELS_SANDALS", "FOOTWEAR_SANDALS", "FOOTWEAR_FLIP_FLOPS", "FOOTWEAR_FLATS_BALLET", "FOOTWEAR_MULES", "FOOTWEAR_CLOGS", "FOOTWEAR_ESPADRILLES", "FOOTWEAR_SLIPPERS", "FOOTWEAR_WEDGES", "ACCESSORIES_BASEBALL_CAP", "ACCESSORIES_BEANIE", "ACCESSORIES_BUCKET_HAT", "ACCESSORIES_WIDE_BRIM_HAT", "ACCESSORIES_VISOR", "ACCESSORIES_SCARF", "ACCESSORIES_GLOVES", "ACCESSORIES_MITTENS", "ACCESSORIES_BELT", "ACCESSORIES_TIE", "ACCESSORIES_BOW_TIE", "ACCESSORIES_POCKET_SQUARE", "ACCESSORIES_SUSPENDERS", "ACCESSORIES_EARMUFFS", "ACCESSORIES_SUNGLASSES", "ACCESSORIES_EYEGLASS_FRAMES", "ACCESSORIES_WATCH", "ACCESSORIES_NECKLACE", "ACCESSORIES_BRACELET", "ACCESSORIES_EARRINGS", "ACCESSORIES_RING", "ACCESSORIES_ANKLET", "ACCESSORIES_BROOCH", "ACCESSORIES_HAIR_ACCESSORY", "ACCESSORIES_WALLET", "ACCESSORIES_CARD_HOLDER", "ACCESSORIES_KEYCHAIN", "ACCESSORIES_HANDKERCHIEF", "ACCESSORIES_FACE_MASK", "BAGS_TOTE", "BAGS_SHOULDER", "BAGS_CROSSBODY", "BAGS_BACKPACK", "BAGS_DUFFEL", "BAGS_SATCHEL", "BAGS_BELT_BAG", "BAGS_BRIEFCASE", "BAGS_LAPTOP", "LUGGAGE_CARRY_ON", "LUGGAGE_CHECKED", "LUGGAGE_GARMENT_BAG", "SPECIAL_MATERNITY_TOP", "SPECIAL_MATERNITY_BOTTOM", "SPECIAL_MATERNITY_DRESS", "SPECIAL_ADAPTIVE_TOP", "SPECIAL_ADAPTIVE_BOTTOM", "SPECIAL_UNDER_SCRUB_TOP", "SPECIAL_UNDER_SCRUB_PANTS", "SPECIAL_WORK_OVERALLS", "SPECIAL_APRON", "SPECIAL_COSTUME", "SPECIAL_FORMAL_SUIT_JACKET", "SPECIAL_FORMAL_SUIT_PANTS", "SPECIAL_FORMAL_SUIT_SKIRT", "SPECIAL_FORMAL_VEST", "TRADITIONAL_KIMONO", "TRADITIONAL_HANBOK", "TRADITIONAL_SARI", "TRADITIONAL_QIPAO_CHEONGSAM", "TRADITIONAL_KURTA", "TRADITIONAL_DIRNDL", "TRADITIONAL_KILT"]] = Field(
        description="Pick relevent lowlevel categories for the user's request."
    )
    search_product_phrase: str = Field(description="Make a phrase that reflect the user's product search request.")


client = OpenAI(api_key=OPENAI_API_KEY)
conversation = client.conversations.create()

LOW_LEVEL_CATEGORY_LIST = list(LOW_LEVEL_CATEGORY_MAP.keys())
HIGH_LEVEL_CATEGORY_LIST = list(HIGH_LEVEL_CATEGORY_MAP.keys())
async def get_route_decision(user_input: str, conv_id: str):

    response = client.responses.parse( #maybe we can offload some works to other model? -> pick product cat/gend/age ... fast -> if too broad -> ask question OR execute search
        model="gpt-4.1-mini-2025-04-14",
        input=[
            {"role": "system",
            "content": "You are a helpful apparel shopping assistant. Assist the user with their request." 
            +"User's reuquest may have incomplete information, so wait for the user to complete the request."
            +"Pick the single best request_type for the user's request. Consider the user's request may be continuing from the previous request."
            +f"IF user's request is find_product, pick relevent categories from {LOW_LEVEL_CATEGORY_LIST}"
            +f"IF user's request is find_product, make a phrase that reflect the user's product search request."
            },
            {
                "role": "user",
                "content": user_input,
            },
        ],
        conversation=conv_id,
        text_format=RouteDecision,
    )
    return response.output_parsed




class ProductCategoryDecision(BaseModel):
    high_level_category: Literal["TOPS", "BOTTOMS", "DRESSES_ONEPIECES", "OUTERWEAR", "ACTIVEWEAR", "UNDERWEAR_HOSIERY_SOCKS", "SLEEP_LOUNGE", "SWIMWEAR", "FOOTWEAR", "ACCESSORIES_JEWELRY", "BAGS_LUGGAGE", "SPECIALTY", "TRADITIONAL_CULTURAL"] = Field(
        description="Pick the single best high level category for the user's request."
    )
    low_level_category: List[Literal["TOPS_TSHIRT", "TOPS_LONGSLEEVE_TSHIRT", "TOPS_POLO", "TOPS_BUTTON_DOWN_SHIRT", "TOPS_DRESS_SHIRT", "TOPS_BLOUSE", "TOPS_TANK", "TOPS_CAMISOLE", "TOPS_BODYSUIT", "TOPS_SWEATSHIRT", "TOPS_HOODIE", "TOPS_SWEATER", "TOPS_CARDIGAN", "TOPS_TURTLENECK", "TOPS_TUNIC", "TOPS_CROP_TOP", "TOPS_VEST", "TOPS_JERSEY", "TOPS_RUGBY", "TOPS_PEPLUM", "TOPS_HENLEY", "TOPS_BASE_LAYER_TOP", "BOTTOMS_JEANS_SKINNY", "BOTTOMS_JEANS_STRAIGHT", "BOTTOMS_JEANS_RELAXED", "BOTTOMS_JEANS_BOOTCUT", "BOTTOMS_JEANS_WIDE", "BOTTOMS_CHINOS", "BOTTOMS_TROUSERS_DRESS", "BOTTOMS_CARGO_PANTS", "BOTTOMS_JOGGERS", "BOTTOMS_SWEATPANTS", "BOTTOMS_LEGGINGS", "BOTTOMS_YOGA_PANTS", "BOTTOMS_SHORTS_CASUAL", "BOTTOMS_SHORTS_ATHLETIC", "BOTTOMS_SHORTS_DRESS", "BOTTOMS_SKIRT_MINI", "BOTTOMS_SKIRT_MIDI", "BOTTOMS_SKIRT_MAXI", "BOTTOMS_SKIRT_PENCIL", "BOTTOMS_SKIRT_A_LINE", "BOTTOMS_OVERALLS", "BOTTOMS_SARONG", "DRESSES_CASUAL", "DRESSES_DAY", "DRESSES_COCKTAIL", "DRESSES_EVENING", "DRESSES_GOWN", "DRESSES_BODYCON", "DRESSES_SHIRT_DRESS", "DRESSES_WRAP", "DRESSES_SUN", "DRESSES_JUMPSUIT", "DRESSES_ROMPER", "DRESSES_SUIT_SET", "OUTERWEAR_DENIM_JACKET", "OUTERWEAR_LEATHER_JACKET", "OUTERWEAR_BOMBER_JACKET", "OUTERWEAR_WINDBREAKER", "OUTERWEAR_PUFFER_JACKET", "OUTERWEAR_TRENCH_COAT", "OUTERWEAR_WOOL_COAT", "OUTERWEAR_PEA_COAT", "OUTERWEAR_PARKA", "OUTERWEAR_RAINCOAT", "OUTERWEAR_BLAZER", "OUTERWEAR_VEST", "OUTERWEAR_CAPE_PONCHO", "OUTERWEAR_FLEECE", "OUTERWEAR_SHACKET", "ACTIVE_LEGGINGS", "ACTIVE_SPORTS_BRA", "ACTIVE_TRAINING_TOP", "ACTIVE_TRAINING_SHORTS", "ACTIVE_TRACK_PANTS", "ACTIVE_RUNNING_JACKET", "ACTIVE_RASH_GUARD", "ACTIVE_CYCLING_JERSEY", "ACTIVE_BASE_LAYER", "ACTIVE_TENNIS_GOLF_DRESS", "UNDERWEAR_BRA", "UNDERWEAR_BRALETTE", "UNDERWEAR_PANTIES_BRIEF", "UNDERWEAR_PANTIES_THONG", "UNDERWEAR_PANTIES_BIKINI", "UNDERWEAR_BOXERS", "UNDERWEAR_BOXER_BRIEFS", "UNDERWEAR_BRIEFS", "UNDERWEAR_LONG_JOHNS", "UNDERWEAR_THERMAL_TOP", "UNDERWEAR_UNDERSHIRT", "UNDERWEAR_SHAPEWEAR_TOP", "UNDERWEAR_SHAPEWEAR_BOTTOM", "HOSIERY_TIGHTS", "HOSIERY_STOCKINGS", "HOSIERY_PANTYHOSE", "SOCKS_CASUAL", "SOCKS_SPORTS", "SOCKS_DRESS", "SOCKS_NO_SHOW", "SLEEP_PAJAMA_TOP", "SLEEP_PAJAMA_BOTTOM", "SLEEP_PAJAMA_SET", "SLEEP_NIGHTGOWN", "SLEEP_ROBE", "SLEEP_LOUNGE_TOP", "SLEEP_LOUNGE_PANTS", "SLEEP_SWEATSUIT_SET", "SWIM_TRUNKS", "SWIM_BRIEFS", "SWIM_BOARD_SHORTS", "SWIM_BIKINI_TOP", "SWIM_BIKINI_BOTTOM", "SWIM_ONE_PIECE", "SWIM_COVER_UP", "SWIM_RASH_GUARD", "FOOTWEAR_SNEAKERS", "FOOTWEAR_RUNNING_SHOES", "FOOTWEAR_HIKING_BOOTS", "FOOTWEAR_BOOTS_ANKLE", "FOOTWEAR_BOOTS_KNEE", "FOOTWEAR_BOOTS_CHELSEA", "FOOTWEAR_OXFORDS", "FOOTWEAR_DERBIES", "FOOTWEAR_LOAFERS", "FOOTWEAR_DRESS_SHOES", "FOOTWEAR_HEELS_PUMPS", "FOOTWEAR_HEELS_SANDALS", "FOOTWEAR_SANDALS", "FOOTWEAR_FLIP_FLOPS", "FOOTWEAR_FLATS_BALLET", "FOOTWEAR_MULES", "FOOTWEAR_CLOGS", "FOOTWEAR_ESPADRILLES", "FOOTWEAR_SLIPPERS", "FOOTWEAR_WEDGES", "ACCESSORIES_BASEBALL_CAP", "ACCESSORIES_BEANIE", "ACCESSORIES_BUCKET_HAT", "ACCESSORIES_WIDE_BRIM_HAT", "ACCESSORIES_VISOR", "ACCESSORIES_SCARF", "ACCESSORIES_GLOVES", "ACCESSORIES_MITTENS", "ACCESSORIES_BELT", "ACCESSORIES_TIE", "ACCESSORIES_BOW_TIE", "ACCESSORIES_POCKET_SQUARE", "ACCESSORIES_SUSPENDERS", "ACCESSORIES_EARMUFFS", "ACCESSORIES_SUNGLASSES", "ACCESSORIES_EYEGLASS_FRAMES", "ACCESSORIES_WATCH", "ACCESSORIES_NECKLACE", "ACCESSORIES_BRACELET", "ACCESSORIES_EARRINGS", "ACCESSORIES_RING", "ACCESSORIES_ANKLET", "ACCESSORIES_BROOCH", "ACCESSORIES_HAIR_ACCESSORY", "ACCESSORIES_WALLET", "ACCESSORIES_CARD_HOLDER", "ACCESSORIES_KEYCHAIN", "ACCESSORIES_HANDKERCHIEF", "ACCESSORIES_FACE_MASK", "BAGS_TOTE", "BAGS_SHOULDER", "BAGS_CROSSBODY", "BAGS_BACKPACK", "BAGS_DUFFEL", "BAGS_SATCHEL", "BAGS_BELT_BAG", "BAGS_BRIEFCASE", "BAGS_LAPTOP", "LUGGAGE_CARRY_ON", "LUGGAGE_CHECKED", "LUGGAGE_GARMENT_BAG", "SPECIAL_MATERNITY_TOP", "SPECIAL_MATERNITY_BOTTOM", "SPECIAL_MATERNITY_DRESS", "SPECIAL_ADAPTIVE_TOP", "SPECIAL_ADAPTIVE_BOTTOM", "SPECIAL_UNDER_SCRUB_TOP", "SPECIAL_UNDER_SCRUB_PANTS", "SPECIAL_WORK_OVERALLS", "SPECIAL_APRON", "SPECIAL_COSTUME", "SPECIAL_FORMAL_SUIT_JACKET", "SPECIAL_FORMAL_SUIT_PANTS", "SPECIAL_FORMAL_SUIT_SKIRT", "SPECIAL_FORMAL_VEST", "TRADITIONAL_KIMONO", "TRADITIONAL_HANBOK", "TRADITIONAL_SARI", "TRADITIONAL_QIPAO_CHEONGSAM", "TRADITIONAL_KURTA", "TRADITIONAL_DIRNDL", "TRADITIONAL_KILT"]] = Field(
        description="Pick three most relevent low level category for the user's request."
    )
    # too_broad: bool = Field(description="Is user's request too general to pick three categories?")
    # confidence: confloat(ge=0, le=1) = Field(description="Confidence 0..1 for the broad decision")

def execute_opensearch(categories: List[str], user_input: str):
    # Generate embedding for semantic search
    try:
        embedding_response = client.embeddings.create(
            model="text-embedding-3-small",
            input=user_input
        )
        query_embedding = embedding_response.data[0].embedding
    except Exception as e:
        print(f"Error generating embedding: {e}")
        query_embedding = []

    # Build hybrid search query
    query_parts = []
    
    # 1. Text-based search (keyword matching)
    text_query = {
        "multi_match": {
            "query": user_input,
            "fields": [
                "product_title^3",           # Boost product title
                "variant_title^2",           # Boost variant title  
                "product_description^1.5",   # Moderate boost for descriptions
                "product_description_SILOAM^2" # Higher boost for SILOAM descriptions
            ],
            "type": "best_fields",           # Use best matching field
            "fuzziness": "AUTO",             # Handle typos
            "operator": "or"                 # More forgiving matching
        }
    }
    query_parts.append(text_query)
    
    # 2. Semantic search (if embedding available)
    if query_embedding:
        semantic_queries = []
        
        # Search in both description embeddings
        if len(query_embedding) == 1536:  # Validate embedding dimensions
            semantic_queries.extend([
                {
                    "script_score": {
                        "query": {"match_all": {}},
                        "script": {
                            "source": "cosineSimilarity(params.query_vector, 'product_description_embed') + 1.0",
                            "params": {"query_vector": query_embedding}
                        }
                    }
                },
                {
                    "script_score": {
                        "query": {"match_all": {}},
                        "script": {
                            "source": "cosineSimilarity(params.query_vector, 'product_description_SILOAM_embed') + 1.0", 
                            "params": {"query_vector": query_embedding}
                        }
                    }
                }
            ])
        
        query_parts.extend(semantic_queries)

    q = {
        "query": {
            "bool": {
                "should": query_parts,  # Any of these can match
                "filter": [
                    {"terms": {"product_category": categories}},  # Required category match
                    {"term": {"available": True}}  # Only available products
                ],
                "minimum_should_match": 1  # At least one should clause must match
            }
        },
        "sort": [
            {"_score": {"order": "desc"}},  # Relevance first
            {"price": {"order": "asc"}}     # Then by price
        ],
        "size": 10,
    }
    print("q: ", q)
    os_response = os_client.search(index="catalog_search_v01_09072025", body=q)
    print("os_response: ", os_response)
    response = client.responses.create(
        model="gpt-5-nano-2025-08-07",
        input=[
            {"role": "system",
            "content": "You are a helpful apparel shopping assistant. Explain the opensearch results in a way that is friendly to the user." 

            },
            {
                "role": "user",
                "content": str(os_response),
            },
        ],

    )
    return response.output_text

async def _pump_openai_to_client(openai_ws: websockets.WebSocketClientProtocol,
                                 client_ws: WebSocket, conv_id: str):
    """
    Forward selected OpenAI events back to your client.
    We pass through:
      - conversation.item.input_audio_transcription.delta
      - conversation.item.input_audio_transcription.completed
      - session.created / error (useful for debugging)
    You can also just forward *all* JSON you receive if you prefer.
    """
    while True:
        async for raw in openai_ws:
            # OpenAI WS messages are JSON strings (and occasionally binary if you request TTS/audio)
            if isinstance(raw, (bytes, bytearray)):
                print("raw❤️", raw)
                # For transcription-only sessions we don't expect binary from OpenAI;
                # ignore or route elsewhere if you enable audio out.

                #continue

            try:
    
                event = json.loads(raw)
                type = event.get("type")
                if type == "conversation.item.input_audio_transcription.delta":
                    delta = event.get("delta", "")
                    if delta:
                        #print("delta: ",delta, end="", flush=True)  # live partials
                        print(f"delta: {delta}\n")
                elif type == "conversation.item.input_audio_transcription.completed":
                    print(f"User said: {event.get('transcript', 'No transcript')}")
                    answer = await get_route_decision(event.get('transcript', 'No transcript'), conv_id)
                    print("Route decision Model answered: ", answer)
                    if answer.request_type == "find_product":
                        product_category_decision = execute_opensearch(answer.low_level_category, answer.search_product_phrase)
                        print("Product category decision: ", product_category_decision)
                    
                        await stream_tts(product_category_decision, client_ws)
                        await client_ws.send_text(json.dumps(product_category_decision))
                else:
                    continue
                    # print("unknown event", event)
            except Exception as e:
                # debug: forward opaque message
                print("error", e)
                await client_ws.send_text(raw)
                continue

            etype = event.get("type", "")

            # Pass through useful events
            if etype in (
                "session.created",
                "error",
                "conversation.item.input_audio_transcription.delta",
                "conversation.item.input_audio_transcription.completed",
            ):
                await client_ws.send_text(json.dumps(event))
                continue

            # Optional: forward everything for debugging
            # await client_ws.send_text(json.dumps(ev))

@app.websocket("/ws")
async def ws_bridge(client_ws: WebSocket):
    """
    WebSocket bridge:
      Browser/SDK  <───WS───>  FastAPI  <───WS───>  OpenAI Realtime (transcription)

    Client payloads we accept:
      - Binary raw PCM16 (24 kHz mono) frames → forwarded as input_audio_buffer.append (base64)
      - JSON {"type":"input_audio_buffer.append","audio":"<base64>"}
      - JSON {"type":"input_audio_buffer.commit"} / {"type":"input_audio_buffer.clear"}

    Server → client:
      - Pass-through of OpenAI transcription events:
          conversation.item.input_audio_transcription.delta / .completed
      - session.created / error (for visibility)
    """
    await client_ws.accept()

    openai_ws = await _open_openai_ws()

    conv_id = conversation.id

    # Two pumps running concurrently
    to_oai = asyncio.create_task(_pump_client_to_openai(client_ws, openai_ws))
    from_oai = asyncio.create_task(_pump_openai_to_client(openai_ws, client_ws, conv_id))

    try:
        await asyncio.gather(to_oai, from_oai)


        # done, pending = await asyncio.wait(
        #     {to_oai, from_oai}, return_when=asyncio.ALL_COMPLETED
        # )
        # # surface exceptions if any
        # for t in done:
        #     e = t.exception()
        #     if e:
        #         raise e
    except WebSocketDisconnect:
        pass
    except Exception as e:
        # Try to inform client; ignore if it's already gone
        try:
            await client_ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
    finally:
        # Cleanup
        for t in (to_oai, from_oai):
            if not t.done():
                t.cancel()
        with contextlib.suppress(Exception):
            await openai_ws.close()
        with contextlib.suppress(Exception):
            await client_ws.close()
