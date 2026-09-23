import os
import io
import copy
import json
import sqlite3
import requests
import shutil
import asyncio
import textwrap
import re
import time
import random
import difflib
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from tqdm import tqdm
import hashlib
import math
import traceback
import subprocess

load_dotenv()

from visual_licensing_runtime import append_image_credits
from script_runtime import (
    append_research_sources, choose_editorial_angle, classify_hook_style, validate_content_density,
    estimate_narration_duration, classify_narration_duration, measure_audio_duration, validate_tts_duration,
)


def global_exception_hook(exctype, value, tb):
    print("💥 UNCAUGHT EXCEPTION DETECTED BY GLOBAL HOOK:")
    print("!"*60)
    traceback.print_exception(exctype, value, tb)
    print("!"*60)
    remote_mode = str(os.getenv("VSF_REMOTE_MODE", "") or "").strip().lower()
    if bool(getattr(sys.stdin, "isatty", lambda: False)()) and remote_mode not in {
        "1", "true", "yes", "remote", "cloud", "streamlit", "streamlit_cloud"
    }:
        input("\nPress Enter to exit...")

sys.excepthook = global_exception_hook

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None

os.environ["IMAGEIO_FFMPEG_EXE"] = "ffmpeg"

from PIL import Image, UnidentifiedImageError, ImageFilter, ImageDraw, ImageFont
import PIL
try:
    import edge_tts
except ImportError:
    edge_tts = None

if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = getattr(PIL.Image, "Resampling", PIL.Image).LANCZOS

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")

PALETTE = {
    "bg": (15, 20, 35),
    "bg_glass": (10, 15, 30, 190), 
    "accent_primary": (0, 191, 255),
    "accent_secondary": (255, 140, 0),
    "text_primary": (255, 255, 255),
    "text_muted": (200, 200, 200),
    "glass_border": (255, 255, 255, 60),
    "shadow": (0, 0, 0, 180)
}

# ==========================================
# STEP 1 UPDATE: DYNAMIC RELATIVE PATHS
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "output")
DB_PATH = os.path.join(BASE_DIR, "vault.db")
CLIENT_SECRETS_FILE = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

BRAND_ASSETS_DIR = os.path.join(BASE_DIR, "brand_assets")
BGM_DIR = os.path.join(BRAND_ASSETS_DIR, "bgm")
SFX_DIR = os.path.join(BRAND_ASSETS_DIR, "sfx")
ASSET_CACHE_DIR = os.path.join(BASE_DIR, "asset_cache")

os.makedirs(BGM_DIR, exist_ok=True)
os.makedirs(SFX_DIR, exist_ok=True)
os.makedirs(ASSET_CACHE_DIR, exist_ok=True)

OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl"
]

CONTENT_CATEGORIES = {
    "entertainment": {
        "label": "Movie Gossips & Entertainment",
        "gnews_q": "(Bollywood OR Tollywood OR Indian cinema OR Indian actor OR Indian music OR box office)",
        "india_gnews_q": "(India OR Indian OR Bollywood OR Tollywood) (film OR movie OR actor OR actress OR trailer OR release OR box office OR music OR streaming)",
        "global_gnews_q": "(Hollywood OR global cinema OR film OR streaming OR music) (launch OR release OR award OR controversy OR box office OR announcement)",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "24", "hashtags": ["#Entertainment", "#MovieGossip", "#Trending"],
        "usable_regular": True, "usable_top5": True
    },
    "national_global_affairs": {
        "label": "National & Global Affairs",
        "gnews_q": "(India OR Indian) (economy OR geopolitics OR government OR policy OR technology OR business) OR (world OR global) (major OR crisis OR decision OR summit)",
        "india_gnews_q": "(India OR Indian) (government OR policy OR economy OR geopolitics OR courts OR diplomacy OR technology OR major decision OR major development)",
        "global_gnews_q": "(world OR global OR international) (major development OR summit OR conflict OR economy OR policy OR breakthrough OR crisis)",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "25", "hashtags": ["#News", "#GlobalAffairs", "#CurrentEvents"],
        "usable_regular": True, "usable_top5": True
    },
    "viral_phenomenon": {
        "label": "Viral Trends & Internet Phenomena",
        "gnews_q": "(India OR Indian) (viral OR internet trend OR social media OR creator OR meme) OR (global internet trend OR viral phenomenon)",
        "india_gnews_q": "(India OR Indian) (viral OR trending OR social media OR creator OR meme OR internet phenomenon)",
        "global_gnews_q": "(global OR worldwide) (viral OR internet trend OR social media trend OR creator OR platform)",
        "rss_url": "https://www.reddit.com/r/Damnthatsinteresting/hot.json?limit=20",
        "category_id": "24", "hashtags": ["#Viral", "#TrendingNow", "#MindBlown"],
        "usable_regular": True, "usable_top5": True
    },
    "sports": {
        "label": "Asian & Global Sports Highlights",
        "gnews_q": "(India OR Indian) (cricket OR tennis OR football OR soccer OR badminton OR hockey OR athletics OR basketball OR golf OR rugby OR volleyball OR wrestling OR boxing OR motorsport OR Formula 1 OR F1 OR MotoGP) OR (global OR world) (sports OR tournament OR final OR record OR championship OR transfer OR Grand Slam OR Olympics)",
        "india_gnews_q": "(India OR Indian) (cricket OR football OR tennis OR badminton OR hockey OR athletics OR basketball OR kabaddi OR wrestling OR boxing OR motorsport OR Formula 1 OR F1 OR golf OR Olympics)",
        "global_gnews_q": "(global OR world) (sports OR tennis OR football OR soccer OR basketball OR golf OR rugby OR volleyball OR athletics OR motorsport OR Formula 1 OR F1 OR MotoGP OR boxing OR wrestling) (tournament OR final OR record OR championship OR transfer OR Grand Slam OR Olympics)",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "17", 
        "hashtags": ["#Cricket", "#Tennis", "#BGMI", "#Badminton", "#Football", "#SportsHighlights"],
        "usable_regular": True, 
        "usable_top5": False
    },
    "sports_stories_of_day": {
        "label": "Sports Stories of the Day",
        "gnews_q": "(India OR Indian OR BCCI OR IPL OR WPL OR ISL OR Indian Football) (cricket OR football OR tournament OR match OR record OR squad OR result)",
        "india_gnews_q": "(India OR Indian OR BCCI OR IPL OR WPL OR India Women) (cricket OR match OR result OR squad OR selection OR injury OR record OR series OR final OR win OR loss)",
        "global_gnews_q": "(ICC OR Australia Cricket OR England Cricket OR South Africa Cricket OR New Zealand Cricket OR West Indies Cricket OR T20 Cricket OR Test Cricket) (match OR result OR squad OR series OR final OR record OR win OR loss)",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "17", 
        "hashtags": ["#SportsNews", "#Cricket", "#Football", "#TrendingSports", "#SportsHighlights"],
        "usable_regular": True, 
        "usable_top5": True
    },
    "technology": {
        "label": "Tech & AI News",
        "gnews_q": "(India OR Indian) (AI OR technology OR startup OR smartphone OR semiconductor OR software OR gadget OR launch) OR (global AI OR technology)",
        "india_gnews_q": "(India OR Indian) (AI OR technology OR startup OR smartphone OR semiconductor OR software OR gadget OR launch OR research OR policy)",
        "global_gnews_q": "(global OR worldwide) (AI OR technology OR semiconductor OR smartphone OR space OR software) (launch OR breakthrough OR regulation OR deal)" ,
        "rss_url": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "28", "hashtags": ["#TechNews", "#AI", "#Gadgets"],
        "usable_regular": True, "usable_top5": True
    },
    "tech_reviews": {
        "label": "Tech & Gadget Reviews",
        "gnews_q": "(India OR Indian) (smartphone review OR laptop launch OR gadget review OR tech launch) OR (global gadget review OR major device launch)",
        "india_gnews_q": "(India OR Indian) (smartphone review OR laptop review OR gadget review OR device launch OR pricing)",
        "global_gnews_q": "(global) (major smartphone launch OR laptop launch OR gadget review OR consumer tech)",
        "rss_url": "https://news.google.com/rss/search?q=gadget+review+smartphone+launch&hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "28", "hashtags": ["#TechReview", "#Gadgets", "#Smartphone", "#TechUnboxing"],
        "usable_regular": True, "usable_top5": False
    },
    "business_finance": {
        "label": "Business & Finance",
        "gnews_q": "(India OR Indian) (stock market OR startups OR economy OR business OR RBI OR rupee OR budget) OR (global markets OR economy)",
        "india_gnews_q": "(India OR Indian) (stock market OR economy OR business OR startup OR RBI OR rupee OR budget OR investment OR IPO)",
        "global_gnews_q": "(global OR worldwide) (markets OR economy OR business OR central bank OR trade OR major deal OR earnings OR investment)",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "27", "hashtags": ["#Finance", "#Business", "#Investing"],
        "usable_regular": True, "usable_top5": True
    },
    "health_lifestyle": {
        "label": "Health & Lifestyle",
        "gnews_q": "(India OR Indian) (health OR medicine OR healthcare OR nutrition OR wellness OR public health) OR (global health OR science)",
        "india_gnews_q": "(India OR Indian) (healthcare OR medicine OR public health OR disease OR nutrition OR wellness OR medical research OR health policy)",
        "global_gnews_q": "(global OR worldwide) (health OR medicine OR science OR public health) (study OR approval OR outbreak OR breakthrough OR guidance)",
        "rss_url": "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "26", "hashtags": ["#Health", "#Wellness", "#FitnessTips"],
        "usable_regular": True, "usable_top5": True
    },
    "regional_state_news": {
        "label": "Telangana & AP Updates",
        "gnews_q": "(Telangana OR Hyderabad OR Andhra Pradesh OR Amaravati) (development OR government OR business OR infrastructure OR politics OR technology OR culture)",
        "india_gnews_q": "(Telangana OR Hyderabad OR Andhra Pradesh OR Amaravati) (government OR development OR business OR infrastructure OR technology OR major decision OR major event)",
        "global_gnews_q": "",
        "rss_url": "https://news.google.com/rss/search?q=Telangana+OR+Hyderabad&hl=en-IN&gl=IN&ceid=IN:en",
        "category_id": "25", "hashtags": ["#Telangana", "#Hyderabad", "#TeluguNews"],
        "usable_regular": True, "usable_top5": True
    }
}

LANGUAGES = {
    "english": {
        "key": "english", "label": "English", 
        "script_instruction": "Write ALL narration and titles in punchy, modern English with unique editorial framing.", 
        "voices": {"Male": "en-IN-PrabhatNeural", "Female": "en-IN-NeerjaNeural"}, "font": "arial.ttf"
    },
    "hindi": {
        "key": "hindi", "label": "Hindi", 
        "script_instruction": "Write ALL narration and titles in natural spoken Hindi using Devanagari script with distinct editorial perspective.", 
        "voices": {"Male": "hi-IN-MadhurNeural", "Female": "hi-IN-SwaraNeural"}, "font": "NotoSansDevanagari-Bold.ttf"
    },
    "telugu": {
        "key": "telugu", "label": "Telugu", 
        "script_instruction": "Write ALL narration and titles in natural Telugu using Telugu script with heavy cinematic elevations and unique editorial flavor.", 
        "voices": {"Male": "te-IN-MohanNeural", "Female": "te-IN-ShrutiNeural"}, "font": "NotoSansTelugu-Bold.ttf"
    },
}

PERSONA_PROFILES = {
    "HYPE COMMENTATOR": {
        "gender": "Female", "rate": "+8%", "pitch": "+4Hz",
        "catchphrases": ["Absolute madness!", "You will not believe this!", "Pure cinema!"],
        "forbidden": ["boring", "standard", "somewhat", "perhaps"]
    },
    "ANALYTICAL INSIDER": {
        "gender": "Male", "rate": "+1%", "pitch": "-2Hz",
        "catchphrases": ["Let us break down the numbers.", "The data reveals a stark reality.", "Look closely at this metric."],
        "forbidden": ["omg", "insane", "wild", "mindblown"]
    },
    "CYNICAL CRITIC": {
        "gender": "Male", "rate": "-4%", "pitch": "-4Hz",
        "catchphrases": ["Nobody wants to admit this.", "Saw this predictable disaster coming.", "Absolute corporate nonsense."],
        "forbidden": ["amazing", "brilliant", "wonderful", "flawless"]
    },
    "LISTICLE HOST": {
        "gender": "Female", "rate": "+5%", "pitch": "+2Hz",
        "catchphrases": ["Number one will completely break your brain.", "This next detail changes everything.", "You are not ready for this entry."],
        "forbidden": ["moving on slowly", "skip this", "whatever", "let's count down"]
    },
    "TECH REVIEWER": {
        "gender": "Male", "rate": "+0%", "pitch": "+0Hz",
        "catchphrases": ["Let us talk trade-offs.", "Here is the headline feature.", "Who is this actually built for?"],
        "forbidden": ["garbage", "trash", "game changer for absolutely everyone"]
    }
}

def cleanup_obsolete_run_workspaces(conn, retention_days=7):
    """Remove old run workspaces only when no live/reviewable run references them."""
    output_root = os.path.join(BASE_DIR, "output")
    if not os.path.isdir(output_root):
        return 0

    try:
        retention_seconds = max(86400, int(retention_days) * 86400)
    except (TypeError, ValueError):
        retention_seconds = 7 * 86400
    cutoff = time.time() - retention_seconds

    protected = {
        str(row[0]).strip()
        for row in conn.execute(
            """SELECT run_id FROM vault
               WHERE run_id IS NOT NULL
                 AND status IN (
                     'RUNNING', 'WAITING_SCRIPT_REVIEW', 'WAITING_VISUAL_REVIEW',
                     'READY_FOR_UPLOAD'
                 )"""
        ).fetchall()
        if str(row[0] or "").strip()
    }

    removed = 0
    for name in os.listdir(output_root):
        path = os.path.join(output_root, name)
        if not os.path.isdir(path) or name in protected:
            continue
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            safe_cleanup(path)
            if not os.path.exists(path):
                removed += 1
        except OSError:
            continue

    if removed:
        print(
            f"   [Cleanup] Removed {removed} obsolete run workspace(s); "
            f"retained all active/review/upload-ready runs.",
            flush=True,
        )
    return removed


def safe_cleanup(dir_path):
    if not os.path.exists(dir_path):
        return
    last_error = None
    for attempt in range(3):
        try:
            shutil.rmtree(dir_path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5)
    if last_error:
        print(f"   [!] Could not clean '{dir_path}': {last_error}")

def enforce_cache_ttl_hygiene():
    try:
        if not os.path.exists(ASSET_CACHE_DIR): return
        now = time.time()
        max_files = 150
        ttl_seconds = 7 * 86400 
        files = []
        for f in os.listdir(ASSET_CACHE_DIR):
            fp = os.path.join(ASSET_CACHE_DIR, f)
            if os.path.isfile(fp):
                mtime = os.path.getmtime(fp)
                if now - mtime > ttl_seconds:
                    try: os.remove(fp)
                    except:
                        pass
                else:
                    files.append((mtime, fp))
        if len(files) > max_files:
            files.sort(key=lambda x: x[0])
            to_remove = files[:len(files) - max_files]
            for _, fp in to_remove:
                try: os.remove(fp)
                except:
                    pass
    except Exception as e:
        pass

def parse_groq_json_response(content_str):
    """Safely parse provider JSON, preferring the object that matches the script contract."""
    if isinstance(content_str, dict):
        parsed = copy.deepcopy(content_str)
    elif isinstance(content_str, str):
        cleaned = content_str.strip()
        cleaned = re.sub(r"^\x60\x60\x60(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*\x60\x60\x60$", "", cleaned).strip()
        if not cleaned:
            raise ValueError(
                f"Failed to parse JSON response safely. Raw text: {content_str[:500]}"
            )
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            candidates = []
            for match in re.finditer(r"\{", cleaned):
                try:
                    candidate, _end = decoder.raw_decode(cleaned[match.start():])
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    contract_score = sum(
                        1
                        for key in (
                            "script",
                            "titles",
                            "creator_insight",
                            "editorial_angle",
                            "seo_description",
                            "recommended_title_index",
                            "data",
                        )
                        if key in candidate
                    )
                    candidates.append(
                        (contract_score, len(json.dumps(candidate, ensure_ascii=False)), candidate)
                    )
            if not candidates:
                raise ValueError(
                    f"Failed to parse JSON response safely. Raw text: {content_str[:500]}"
                )
            parsed = max(candidates, key=lambda item: (item[0], item[1]))[2]
    else:
        raise ValueError("Expected a string or decoded JSON object response.")

    if not isinstance(parsed, dict):
        raise ValueError("Groq JSON response must be an object.")
    if isinstance(parsed.get("data"), dict):
        parsed = parsed["data"]

    for key in ("seo_description", "pinned_comment", "creator_insight", "editorial_angle"):
        if key in parsed:
            parsed[key] = safe_text(parsed[key], "")
    if isinstance(parsed.get("titles"), list):
        parsed["titles"] = [safe_text(t, "") for t in parsed["titles"]]
    if isinstance(parsed.get("script"), list):
        parsed["script"] = [dict(scene) if isinstance(scene, dict) else scene for scene in parsed["script"]]
        for scene in parsed["script"]:
            if not isinstance(scene, dict):
                continue
            scene["voiceover"] = safe_text(scene.get("voiceover"), "")
            scene["primary_entity"] = safe_text(scene.get("primary_entity"), "none")
            scene["visual_intent"] = safe_text(scene.get("visual_intent"), "conceptual")
            scene["specific_search_prompt"] = safe_text(scene.get("specific_search_prompt"), "")
            scene["sport_or_topic_category"] = safe_text(scene.get("sport_or_topic_category"), "")
    return parsed

def _provider_http_error_detail(response, max_chars=900):
    """Extract a safe, useful provider error message instead of hiding HTTP 4xx/5xx details."""
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("detail") or error.get("type") or "").strip()
            if not detail:
                detail = str(payload.get("message") or payload.get("detail") or "").strip()
    except Exception:
        detail = ""
    if not detail:
        detail = str(getattr(response, "text", "") or "").strip()
    detail = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", detail)
    detail = re.sub(r"(?i)(?:gsk_|sk-or-v1-|AIza)[A-Za-z0-9._-]+", "[redacted]", detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    return detail[:max_chars]


def init_db(conn):
    """Ensure the canonical run-identity schema is present.

    Database ownership lives in db_architecture.py. Keeping a second legacy
    topic-primary-key schema here allowed duplicate-topic runs to be silently
    ignored whenever the identity bridge was not installed.
    """
    from db_architecture import migrate_vault
    migrate_vault(conn)

def safe_text(val, fallback=""):
    if val is None:
        return fallback
    if isinstance(val, str):
        # LLM/provider formatting artifacts such as `_arrow` are not user-facing text.
        return re.sub(r"(?<![A-Za-z0-9])_arrow(?:_(?:right|left|up|down))?(?![A-Za-z0-9])", "", val, flags=re.IGNORECASE).strip()
    # Prevent NumPy/array-like containers from leaking their repr into UI/video text.
    # Extract their actual values first; ordinary lists/dicts retain their existing behaviour.
    if hasattr(val, "tolist") and not isinstance(val, (bytes, bytearray)):
        try:
            return safe_text(val.tolist(), fallback)
        except Exception:
            pass
    if isinstance(val, dict):
        for key in ("text", "voiceover", "value", "content"):
            if key in val:
                return safe_text(val[key], fallback)
        return " ".join(safe_text(v) for v in val.values()).strip()
    if isinstance(val, list):
        return " ".join(safe_text(v) for v in val).strip()
    if isinstance(val, tuple):
        return " ".join(safe_text(v) for v in val).strip()
    return str(val).strip()

def _remote_mode_enabled():
    """Return True only when the host explicitly opts into remote mode."""
    return str(os.getenv("VSF_REMOTE_MODE", "")).strip().lower() in {
        "1", "true", "yes", "remote", "cloud", "streamlit", "streamlit_cloud"
    }


def _load_remote_youtube_credentials():
    """Build refreshable YouTube credentials from a Streamlit-provided token JSON."""
    raw = str(os.getenv("YOUTUBE_TOKEN_JSON", "") or "").strip()
    if not raw:
        return None

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        print("   [YouTube Auth] Remote credential secret is not valid JSON.", flush=True)
        return None

    if not isinstance(payload, dict) or not payload.get("refresh_token"):
        print("   [YouTube Auth] Remote credential secret is missing refresh_token.", flush=True)
        return None

    try:
        creds = Credentials.from_authorized_user_info(payload, OAUTH_SCOPES)
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            return creds
    except Exception as exc:
        print(
            f"   [YouTube Auth] Remote credential load failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
    return None


class YouTubePublicVisibilityError(RuntimeError):
    """YouTube accepted the upload but did not persist the requested public visibility."""

    def __init__(self, message, video_id=""):
        super().__init__(message)
        self.video_id = str(video_id or "").strip()


def _report_youtube_upload_visibility(response, video_id, requested_privacy):
    """Raise when YouTube did not persist the requested public visibility."""
    expected = str(requested_privacy or "").strip().lower()
    if expected != "public" or not isinstance(response, dict):
        return

    observed = str(
        (response.get("status") or {}).get("privacyStatus") or ""
    ).strip().lower()
    if observed and observed != "public":
        raise YouTubePublicVisibilityError(
            f"YouTube accepted video {video_id} but kept it {observed} instead of public. "
            "The video already exists on YouTube.",
            video_id=video_id,
        )


def _ensure_youtube_public_visibility(youtube, response, video_id):
    """Confirm public visibility, with one in-place API update before failing.

    The upload is never retried through videos.insert. If YouTube initially
    persists the requested public upload as private, update that same video
    once, then verify the returned resource. This avoids duplicate uploads
    while allowing an otherwise eligible channel/project to recover from a
    stale or overridden insert response.
    """
    video_id = str(video_id or "").strip()
    if not video_id:
        raise RuntimeError("Cannot verify public visibility without a YouTube video ID.")

    initial_status = str(
        (response.get("status") or {}).get("privacyStatus") or ""
    ).strip().lower() if isinstance(response, dict) else ""
    if initial_status == "public":
        return response

    current_status = response.get("status") if isinstance(response, dict) else {}
    if not isinstance(current_status, dict):
        current_status = {}

    update_body = {
        "id": video_id,
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": bool(
                current_status.get("selfDeclaredMadeForKids", False)
            ),
        },
    }

    print(
        f"   [YouTube] Upload returned privacyStatus={initial_status or 'unknown'}; "
        "updating the existing video to public once.",
        flush=True,
    )
    try:
        updated = youtube.videos().update(
            part="status",
            body=update_body,
        ).execute()
    except Exception as exc:
        raise YouTubePublicVisibilityError(
            f"YouTube accepted video {video_id} but could not make it public: "
            f"{type(exc).__name__}: {exc}. The existing video was not retried or duplicated. "
            "If this project is unverified, YouTube requires its API project to pass the "
            "YouTube API Services audit before videos.insert uploads can be made public.",
            video_id=video_id,
        ) from exc

    updated_status = str(
        (updated.get("status") or {}).get("privacyStatus") or ""
    ).strip().lower() if isinstance(updated, dict) else ""

    if updated_status != "public":
        try:
            verification = youtube.videos().list(
                part="status",
                id=video_id,
            ).execute()
            items = verification.get("items") if isinstance(verification, dict) else []
            if isinstance(items, list) and items and isinstance(items[0], dict):
                updated_status = str(
                    (items[0].get("status") or {}).get("privacyStatus") or ""
                ).strip().lower()
                if updated_status == "public":
                    return items[0]
        except Exception as exc:
            raise YouTubePublicVisibilityError(
                f"YouTube accepted video {video_id}, but the public visibility could not be "
                f"confirmed after update: {type(exc).__name__}: {exc}.",
                video_id=video_id,
            ) from exc

    if updated_status != "public":
        raise YouTubePublicVisibilityError(
            f"YouTube accepted video {video_id} but kept it {updated_status or 'unknown'} "
            "instead of public. The existing video was not retried or duplicated.",
            video_id=video_id,
        )

    return updated

def get_google_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Remote mode is opt-in. This branch never reads/writes the local token
    # files and never starts a browser on the user's laptop.
    if _remote_mode_enabled():
        creds = _load_remote_youtube_credentials()
        if creds:
            return creds
        raise RuntimeError(
            "Remote YouTube credentials are not configured. "
            "Set VSF_REMOTE_MODE=1 and provide YOUTUBE_TOKEN_JSON in Streamlit Secrets."
        )

    # Local behavior is intentionally preserved exactly: use token.json when
    # available, refresh it locally, and fall back to the installed-app browser flow.
    creds = None
    if os.path.exists(TOKEN_FILE):
        try: creds = Credentials.from_authorized_user_file(TOKEN_FILE, OAUTH_SCOPES)
        except Exception:
            pass
    if creds and creds.valid: return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f: f.write(creds.to_json())
            return creds
        except Exception:
            pass
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, OAUTH_SCOPES)
    creds = flow.run_local_server(port=0, access_type='offline', prompt='consent')
    with open(TOKEN_FILE, "w") as f: f.write(creds.to_json())
    return creds

def get_genre_bonuses(conn):
    c = conn.cursor()
    c.execute("SELECT genre FROM vault ORDER BY date_used DESC LIMIT 1")
    row = c.fetchone()
    last_genre = row[0] if row and row[0] else None

    scores = get_smart_metrics(conn, "genre", metric_col="avg_view_percentage")
    max_score = max([data['score'] for data in scores.values() if data['score'] is not None] + [1])
    bonuses = {}
    for g in CONTENT_CATEGORIES.keys():
        bonuses[g] = round((scores[g]['score'] / max_score) * 2.0, 1) if g in scores and scores[g]['score'] is not None else 0.0
    return bonuses, last_genre


def calculate_smart_score(records):
    """Return a recency-weighted trimmed mean for (value, days_ago) records."""
    valid = []
    for value, days_ago in records:
        try:
            value = float(value)
            days_ago = max(0.0, float(days_ago))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(value) and math.isfinite(days_ago)):
            continue
        valid.append((value, days_ago))

    if len(valid) < 3:
        return None

    if len(valid) >= 5:
        valid = sorted(valid, key=lambda item: item[0])[1:-1]

    weighted_sum = 0.0
    total_weight = 0.0
    for value, days_ago in valid:
        weight = 0.5 ** (days_ago / 30.0)
        weighted_sum += value * weight
        total_weight += weight

    return weighted_sum / total_weight if total_weight else 0.0

def get_smart_metrics(conn, dimension_col, metric_col="views"):
    try:
        c = conn.cursor()
        c.execute(
            "SELECT date_used, views, avg_view_percentage FROM vault "
            "WHERE video_id IS NOT NULL "
            "AND video_id NOT IN ('', 'PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED') "
            "AND status NOT IN ('PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED')"
        )
        all_videos = c.fetchall()
        if not all_videos: return {}
        
        allowed_dimensions = {
            "genre", "format_used", "language_used", "hook_style_used", "trend_keyword"
        }
        if dimension_col not in allowed_dimensions:
            return {}
        c.execute(
            f"SELECT {dimension_col}, date_used, {metric_col}, views FROM vault "
            f"WHERE {dimension_col} IS NOT NULL "
            "AND video_id IS NOT NULL "
            "AND video_id NOT IN ('', 'PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED') "
            "AND status NOT IN ('PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED')"
        )
        target_data = c.fetchall()
        
        grouped_records = {}
        for row in target_data:
            dim_key, date_used_str, metric_val, raw_views = row[0], row[1], row[2], row[3]
            if not metric_val: continue
            
            date_used = datetime.fromisoformat(date_used_str) if isinstance(date_used_str, str) else date_used_str
            days_ago = (datetime.now() - date_used).days
            
            window_views = [v[1] for v in all_videos if abs((datetime.fromisoformat(v[0] if isinstance(v[0], str) else str(v[0])) - date_used).days) <= 7]
            baseline_avg = (sum(window_views) / len(window_views)) if window_views else 1
            
            normalized_val = (metric_val / baseline_avg) if metric_col == "views" else metric_val
            if dim_key not in grouped_records: grouped_records[dim_key] = []
            grouped_records[dim_key].append((normalized_val, days_ago))
        
        scored_results = {}
        for key, records in grouped_records.items():
            scored_results[key] = {"score": calculate_smart_score(records), "count": len(records)}
        return scored_results
    except Exception as e:
        return {}

def print_metric_recommendations(title, scores, is_retention=False):
    print(f"\n📊 {title} (Recency-Weighted, Trimmed Mean):")
    sorted_items = sorted([k for k in scores.keys()], key=lambda x: scores[x]['score'] or -1, reverse=True)
    out = []
    for k in sorted_items:
        data = scores[k]
        if data['score'] is None: out.append(f"{k} (insufficient data: {data['count']} vid{'s' if data['count'] != 1 else ''})")
        else: out.append(f"{k} ({round(data['score'], 1)}{'% avg ret' if is_retention else 'x avg'})")
    if out: print(" > ".join(out))
    else: print("   Not enough historical data to generate recommendations.")

def auto_pilot_selection(conn):
    """Compatibility entry point delegated to the canonical Shorts selector."""
    from autopilot_runtime import select_auto_pilot
    return select_auto_pilot(sys.modules[__name__], conn)

def fetch_trending_topics(target="india", query_filter=None):
    """Return live Google Trends topics through the canonical RSS adapter."""
    from story_ranker import fetch_google_trending_topics

    geo = {
        "india": "IN",
        "us": "US",
        "united states": "US",
        "uk": "GB",
        "great britain": "GB",
    }.get(str(target or "").strip().lower(), "IN")
    trends = fetch_google_trending_topics((geo,), max_terms=30)
    if query_filter:
        terms = [
            token.strip()
            for token in re.split(r"\s+(?:OR|AND)\s+", str(query_filter), flags=re.IGNORECASE)
            if token.strip()
        ]
        filtered = [
            trend for trend in trends
            if any(term.casefold() in trend.casefold() for term in terms)
        ]
        if filtered:
            trends = filtered
    return trends or ["India Tech", "Bollywood", "Cricket", "Stock Market", "AI"]


def manual_prompts(conn):
    scores = get_smart_metrics(conn, "format_used", metric_col="avg_view_percentage")
    print_metric_recommendations("Format Retention History", scores, is_retention=True)
    
    hook_scores = get_smart_metrics(conn, "hook_style_used", metric_col="avg_view_percentage")
    print_metric_recommendations("Hook Style Retention History", hook_scores, is_retention=True)
    
    trend_scores = get_smart_metrics(conn, "trend_keyword", metric_col="avg_view_percentage")
    print_metric_recommendations("Trend Keyword Retention History", trend_scores, is_retention=True)
    
    print("\nSelect Production Format:\n  [1] Regular Deep-Dive / Standard Video\n  [2] Top 5 ... of the Day Countdown\n  [3] Trending Now (Ad-hoc Search Trend)")
    fmt_input = input("\nEnter choice (1-3, default is 1): ").strip()
    
    format_mode = "regular"
    if fmt_input == "2": format_mode = "top5"
    elif fmt_input == "3": format_mode = "trending"
    
    trend_keyword, custom_q, custom_rss = None, None, None
    if format_mode == "trending":
        trends = fetch_trending_topics()
        print("\n🔥 Select a Trending Topic:")
        for idx, t in enumerate(trends, 1):
            print(f"  [{idx}] {t}")
        t_choice = input(f"\nEnter choice (1-{len(trends)}): ").strip()
        try:
            trend_keyword = trends[int(t_choice) - 1]
        except:
            pass
        print(f"   [+] Selected Trend Keyword: '{trend_keyword}'")
        cat_choice = "national_global_affairs"
    else:
        cat_scores = get_smart_metrics(conn, "genre", metric_col="avg_view_percentage")
        print(f"\nSelect {format_mode.upper()} Category:")
        valid_cats = {k: v for k, v in CONTENT_CATEGORIES.items() if (v["usable_regular"] if format_mode == "regular" else v["usable_top5"]) and k != "tech_reviews"}
        idx_map = {}
        for i, (key, cfg) in enumerate(valid_cats.items(), 1):
            idx_map[str(i)] = key
            sd = cat_scores.get(key, {"score": None, "count": 0})
            ss = f"(Insufficient data: {sd['count']} vids)" if sd['score'] is None else f"({round(sd['score'], 1)}% avg retention)"
            print(f"  [{i}] {cfg['label']} {ss}")
        cat_choice = idx_map.get(input(f"\nEnter choice (1-{len(valid_cats)}): ").strip(), list(idx_map.values())[0])
    
    lang_scores = get_smart_metrics(conn, "language_used", metric_col="views")
    print_metric_recommendations("Language Performance History", lang_scores)
    print("\nSelect Narration Language:")
    idx_map_lang = {}
    for i, (key, cfg) in enumerate(LANGUAGES.items(), 1):
        idx_map_lang[str(i)] = key
        print(f"  [{i}] {cfg['label']}")
    lang_key = idx_map_lang.get(input(f"\nEnter choice (1-{len(LANGUAGES)}): ").strip(), "english")
    
    return format_mode, cat_choice, LANGUAGES[lang_key], f"{format_mode}|{cat_choice}|{lang_key}", trend_keyword, custom_q, custom_rss

def cricket_pipeline_prompts():
    print("\n🏏 CRICKET FOCUS PIPELINE ACTIVATED")
    print("Select Cricket Sub-Genre:\n  [1] Asian Giants Focus (BCCI, PCB, SLC, BCB, ACB)\n  [2] Global & Test Nation Elite (ICC, Ashes, BGT)\n  [3] ⚡ AI Auto-Detect (Scans live cricket trends)")
    sub_choice = input("\nEnter choice (1-3): ").strip()
    
    trend_keyword, custom_q, custom_rss = None, None, None
    if sub_choice == "1":
        custom_q = "India Cricket OR Pakistan Cricket OR Sri Lanka Cricket OR Bangladesh Cricket"
        custom_rss = "https://news.google.com/rss/search?q=India+Cricket+OR+Pakistan+Cricket+OR+BCCI&hl=en-IN&gl=IN&ceid=IN:en"
    elif sub_choice == "2":
        custom_q = "Test Cricket OR ICC OR Ashes OR Border Gavaskar Trophy OR Australia Cricket"
        custom_rss = "https://news.google.com/rss/search?q=Test+Cricket+OR+ICC+OR+Ashes&hl=en-IN&gl=IN&ceid=IN:en"
    else:
        trends = fetch_trending_topics(target="india", query_filter="Cricket OR BCCI OR IPL")
        trend_keyword = trends[0]
        print(f"   [+] AI Auto-Detected Hottest Cricket Trend: '{trend_keyword}'")
    
    print("\nSelect Production Format:\n  [1] Regular Deep-Dive\n  [2] Top 5 Countdown")
    fmt_input = input("\nEnter choice (1-2): ").strip()
    format_mode = "top5" if fmt_input == "2" else "regular"
    
    print("\nSelect Narration Language:")
    idx_map_lang = {str(i): key for i, (key, _) in enumerate(LANGUAGES.items(), 1)}
    for i, (key, cfg) in enumerate(LANGUAGES.items(), 1):
        print(f"  [{i}] {cfg['label']}")
    lang_key = idx_map_lang.get(input(f"\nEnter choice (1-{len(LANGUAGES)}): ").strip(), "english")
    
    return format_mode, "sports_stories_of_day", LANGUAGES[lang_key], f"{format_mode}|cricket_focus|{lang_key}", trend_keyword, custom_q, custom_rss

def run_analytics_sweep(conn):
    """Compatibility entry point delegated to exact-video learning sync."""
    from learning_runtime import sync_factory_analytics
    return sync_factory_analytics(sys.modules[__name__], conn)

def gather_and_filter_stories(
    conn,
    genre_key,
    genre_cfg,
    trend_keyword=None,
    custom_gnews_q=None,
    custom_rss_url=None,
):
    """Compatibility entry point backed by the canonical free discovery funnel."""
    from story_ranker import collect_high_recall_stories, rank_story_candidates

    events, social_titles = collect_high_recall_stories(
        None,
        genre_key,
        genre_cfg,
        trend_keyword=trend_keyword,
        custom_gnews_q=custom_gnews_q,
        custom_rss_url=custom_rss_url,
        broad_discovery=False,
    )
    return rank_story_candidates(
        events,
        conn=conn,
        target_category=genre_key,
        target_format="regular",
        target_language="english",
        social_titles=social_titles,
        ai_cricket=(genre_key == "sports_stories_of_day"),
    )


def editorial_gate_batch(stories, bonuses, last_genre, format_mode):
    if not stories: 
        print("   [!] Editorial gate received an empty story list.")
        return None
    
    batch_stories = stories[:15]
    print(f"\n🧠 Executing Groq Editorial Scoring ({len(batch_stories)} candidates)...")
    
    sys_prompt = (
        "Score each story in the input array (1-10) on: hook_strength, narrative_completeness, audience_fit, monetization_risk, shelf_life. "
        "For hook_strength, judge the immediate scroll-stop potential of the story headline and opening fact: reward a specific conflict, surprising result, consequential change, record, or attributed quote that can be understood immediately; "
        "penalize generic setup, routine schedules/previews, and empty 'latest update' framing. Never reward unsupported sensationalism or clickbait. "
        "For narrative_completeness, judge whether the event has enough substance for a concise but complete Short. "
        "Set hard_reject=true only for a clear safety/policy violation or a story that is objectively unusable as a factual Short; "
        "never set hard_reject=true merely because monetization_risk is 8-10, because monetization risk is a soft penalty rather than an automatic rejection. "
        "Return ONLY this exact JSON object structure: {\"results\": [{\"hook_strength\": 8, \"narrative_completeness\": 8, \"audience_fit\": 8, \"monetization_risk\": 9, \"shelf_life\": 7, \"hard_reject\": false, \"one_line_reasoning\": \"...\"}]} "
        "matching the input order one-to-one."
    )
    
    groq_api_key = str(os.getenv("GROQ_API_KEY") or globals().get("GROQ_API_KEY") or "").strip()
    if groq_api_key:
        for attempt in range(1, 3):
            try:
                groq_url = "https://api.groq.com/openai/v1/chat/completions"
                resp = requests.post(
                    groq_url,
                    headers={"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "openai/gpt-oss-120b",
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": json.dumps([
                                {"title": s["title"], "text": s["text"][:200]}
                                for s in batch_stories
                            ])},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                    timeout=20,
                )
                if resp.status_code == 200:
                    parsed_json = parse_groq_json_response(
                        resp.json()["choices"][0]["message"]["content"]
                    )
                    return process_scored_candidates(
                        parsed_json["results"],
                        batch_stories,
                        bonuses,
                        last_genre,
                        format_mode,
                    )

                retryable = resp.status_code == 429 or 500 <= resp.status_code < 600
                print(
                    f"   [!] Groq editorial scoring returned status {resp.status_code}"
                    + ("; bounded retry." if retryable and attempt < 2 else "; falling through."),
                    flush=True,
                )
                if retryable and attempt < 2:
                    time.sleep(2 if resp.status_code == 429 else 1)
                else:
                    break
            except Exception as exc:
                print(
                    f"   [!] Groq editorial scoring failed: {type(exc).__name__}"
                    + ("; bounded retry." if attempt < 2 else "; falling through."),
                    flush=True,
                )
                if attempt < 2:
                    time.sleep(1)

    if GEMINI_API_KEY:
        print("   [!] Groq editorial gate exhausted. Falling back to Gemini API...")
        for g_attempt in range(1, 3):
            try:
                gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
                gemini_payload = {
                    "contents": [{"role": "user", "parts": [{"text": sys_prompt + "\n\nCANDIDATES:\n" + json.dumps([{"title": s['title'], "text": s['text'][:200]} for s in batch_stories])}]}],
                    "generationConfig": {
                        "responseFormat": {
                            "text": {"mimeType": "application/json"}
                        }
                    }
                }
                g_resp = requests.post(
                    gemini_url,
                    headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
                    json=gemini_payload,
                    timeout=25,
                )
                if g_resp.status_code != 200:
                    print(f"   [!] Gemini editorial error {g_resp.status_code} on attempt {g_attempt}.")
                    time.sleep(2)
                    continue
                    
                raw_text = g_resp.json()['candidates'][0]['content']['parts'][0]['text']
                parsed_json = parse_groq_json_response(raw_text)
                scored_data = parsed_json["results"]
                return process_scored_candidates(scored_data, batch_stories, bonuses, last_genre, format_mode)
            except Exception as e:
                time.sleep(2)

    print("   [!] All editorial providers exhausted. Falling back to rule-filtered ranking.")
    for s in batch_stories:
        s.update({"hook_strength": 6, "narrative_completeness": 6, "audience_fit": 6, "monetization_risk": 8, "shelf_life": 6, "composite_score": s.get('velocity_score', 0.0)})
    batch_stories.sort(key=lambda x: x['composite_score'], reverse=True)
    return batch_stories

def process_scored_candidates(scored_data, batch_stories, bonuses, last_genre, format_mode):
    """Compatibility entry point delegated to the canonical editorial scorer."""
    from editorial_runtime import score_candidates
    return score_candidates(
        scored_data or [],
        batch_stories or [],
        bonuses or {},
        last_genre,
        format_mode,
    )

def get_insights_for_script(conn):
    try:
        log_path = os.path.join(BASE_DIR, "editorial_feedback_log.txt")
        if not os.path.exists(log_path):
            return ""
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines:
            return ""
        return (
            "PAST EDITORIAL FEEDBACK — quality guidance only. Never copy wording, structure, hooks or framing:\n"
            + "".join(lines[-10:])
        )
    except Exception:
        return ""


def validate_script(script_data, source_text, format_mode):
    try:
        return validate_content_density(
            script_data,
            {"research_evidence_text": str(source_text or "")},
            format_mode,
        )
    except Exception as exc:
        return False, f"Canonical script validation failed: {type(exc).__name__}: {exc}"
def self_critique_pass(script_data, format_mode):
    """Compatibility surface; substantive script QC lives in script_runtime/quality_runtime."""
    try:
        from script_runtime import assess_narrative_completeness
        assessment = assess_narrative_completeness(script_data)
        return (8, "Passed") if assessment["passed"] else (5, assessment["reason"])
    except Exception as exc:
        return 5, f"Critique unavailable: {type(exc).__name__}: {exc}"



def write_script(story_data, language_cfg, genre_key, conn, format_mode):
    """Generate one compact original script; the router may request one bounded duration repair."""
    format_mode_key = str(format_mode or "").strip().lower()
    print(f"\n✍️ Generating Original Editorial Script ({format_mode_key.upper()} MODE)...")

    research_evidence_text = str(story_data.get("research_evidence_text", "") or "").strip()
    if research_evidence_text:
        source_text = research_evidence_text[:20000]
    else:
        source_text = str(
            story_data.get("text")
            or story_data.get("summary")
            or story_data.get("description")
            or story_data.get("title")
            or story_data.get("topic")
            or ""
        )[:8000]

    persona_name = (
        "LISTICLE HOST" if format_mode_key == "top5"
        else "TECH REVIEWER" if genre_key == "tech_reviews"
        else "HYPE COMMENTATOR" if genre_key in ["sports", "sports_stories_of_day"]
        else "ANALYTICAL INSIDER" if genre_key in ["national_global_affairs", "business_finance", "technology"]
        else "CYNICAL CRITIC"
    )

    scene_contract = (
        "For Top-5 mode, output 6 scenes: one opening hook/title beat followed by five substantive ranked entries; "
        "the fifth entry should deliver the final payoff. "
        if format_mode_key == "top5"
        else "For a regular Short, output 3 or 4 scenes: hook, development/context, and consequence/payoff. "
    )
    word_contract = (
        "- Target roughly 65–75 spoken words in Top-5 mode; never exceed the 90-word safety ceiling.\n"
        if format_mode_key == "top5"
        else "- Target roughly 55–65 spoken words; never exceed the 90-word safety ceiling.\n"
    )
    from script_runtime import SCRIPT_OUTPUT_JSON_SCHEMA, choose_editorial_angle
    editorial_angle = choose_editorial_angle(story_data, format_mode)

    system_prompt = (
        "You are the original-news Shorts writer for a human-reviewed video factory. "
        "Use only the supplied evidence. Write a fresh narration in your own wording. "
        "Never copy any complete sentence verbatim from the evidence. "
        "Do not invent facts, quotes, motives, numbers, or outcomes.\n\n"
        "RUNTIME CONTRACT — NON-NEGOTIABLE:\n"
        + word_contract
        + "- Scene 1: 8–14 words, a factual headline, and the most compact scene.\n"
        "- Keep the scene count exactly aligned with the selected format contract below; do not pad with filler.\n"
        "- Spoken duration is authoritative: keep the narration below 30 seconds at the factory's configured voice rate.\n"
        "- No intro, greeting, CTA, retention bait, generic filler, or production instructions.\n"
        "- Curiosity must come from a real fact or tension, not withheld information.\n\n"
        "STORY SHAPE:\n"
        "Scene 1 = the concrete event/person and strongest supported hook. "
        "Later scenes = the most important evidence and context, then the immediate consequence or final useful fact. "
        "Every sentence must earn its speaking time.\n\n"
        "Return ONLY JSON matching this exact object shape; do not wrap it in Markdown or add commentary. "
        "{\"creator_insight\":\"...\",\"editorial_angle\":\"...\",\"titles\":[\"...\",\"...\",\"...\"],"
        "\"recommended_title_index\":1,\"seo_description\":\"...\",\"pinned_comment\":\"...\","
        "\"script\":[{\"voiceover\":\"...\",\"narrative_role\":\"hook\","
        "\"primary_entity\":\"...\",\"visual_intent\":\"news_event\","
        "\"specific_search_prompt\":\"...\",\"sport_or_topic_category\":\"...\"}]}. "
        "Use narrative_role values hook, development, context, consequence. "
        + scene_contract
        + f"EDITORIAL ANGLE — {editorial_angle['instruction']}\n"
        "CREATOR INSIGHT: Provide one concise evidence-grounded synthesis of why the documented event matters. "
        "This is analysis of the supplied facts, not a new fact, opinion, motive, or prediction.\n"
        + f"Language: {language_cfg['script_instruction']}\n"
    )

    duration_repair_script = story_data.get("_duration_tighten_script")
    if duration_repair_script:
        system_prompt += (
            "\nBOUNDED DURATION REPAIR:\n"
            + str(story_data.get("_duration_tighten_instruction") or "")
            + "\nRewrite the supplied draft instead of starting a longer new script. "
              "Preserve supported facts, entities, numbers, attributions and narrative roles."
        )

    user_content = (
        f"STORY TITLE: {str(story_data.get('title') or story_data.get('topic') or '').strip()}\n"
        f"VERIFIED EVIDENCE:\n{source_text}"
    )
    if duration_repair_script:
        user_content += "\n\nEXISTING DRAFT TO TIGHTEN:\n" + str(duration_repair_script)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    groq_api_key = str(os.getenv("GROQ_API_KEY") or globals().get("GROQ_API_KEY") or "").strip()
    if not groq_api_key:
        raise RuntimeError("Groq script provider unavailable: GROQ_API_KEY is not configured.")

    try:
        structured_payload = {
            "model": "openai/gpt-oss-120b",
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "viral_shorts_script",
                    "strict": True,
                    "schema": SCRIPT_OUTPUT_JSON_SCHEMA,
                },
            },
            "include_reasoning": False,
            "reasoning_effort": "low",
            "temperature": 0.5,
            "max_completion_tokens": 900,
        }
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json",
            },
            json=structured_payload,
            timeout=30,
        )

        data = None
        retry_reason = ""
        if response.status_code == 200:
            try:
                raw_content = response.json()["choices"][0]["message"]["content"]
                data = parse_groq_json_response(raw_content)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                retry_reason = (
                    "successful response was empty or not parseable JSON "
                    f"({type(exc).__name__})"
                )
        elif response.status_code == 400:
            detail = _provider_http_error_detail(response)
            retry_reason = "structured-output request returned HTTP 400" + (
                f": {detail}" if detail else ""
            )
        else:
            detail = _provider_http_error_detail(response)
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"Groq script provider HTTP {response.status_code}{suffix}; "
                "falling through to the next provider."
            )

        if retry_reason:
            print(
                "   [Script Writer] Groq primary attempt needs bounded compatibility retry: "
                + retry_reason
                + ". Retrying once with GPT-OSS 20B JSON mode.",
                flush=True,
            )
            compatibility_messages = [
                {
                    "role": "user",
                    "content": (
                        system_prompt
                        + "\n\n"
                        + user_content
                        + "\n\nReturn ONLY a valid JSON object matching the factory schema. "
                          "Do not output Markdown or any commentary."
                    ),
                }
            ]
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openai/gpt-oss-20b",
                    "messages": compatibility_messages,
                    "response_format": {"type": "json_object"},
                    "include_reasoning": False,
                    "reasoning_effort": "low",
                    "temperature": 0.2,
                    "max_completion_tokens": 900,
                },
                timeout=30,
            )
            if response.status_code != 200:
                detail = _provider_http_error_detail(response)
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(
                    f"Groq script provider HTTP {response.status_code}{suffix}; "
                    "falling through to the next provider."
                )
            try:
                raw_content = response.json()["choices"][0]["message"]["content"]
                data = parse_groq_json_response(raw_content)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Groq compatibility response was not parseable JSON."
                ) from exc

        if not isinstance(data, dict):
            raise ValueError(
                "Groq script provider returned invalid JSON; falling through to the next provider."
            )

        data["hook_type"] = classify_hook_style(data)
        data["hook_style_used"] = data["hook_type"]
        data["structure_used"] = "Top 5" if format_mode_key == "top5" else "Editorial Explainer"
        data["persona_used"] = persona_name.title()

        return data
    except Exception as exc:
        print(f"   [Script Writer] Groq failed: {type(exc).__name__}: {exc}", flush=True)
        raise


async def generate_voiceover_and_timestamps(script_data, language_cfg):
    print(f"\n🎙️ Generating Audio & Mapping Karaoke Timestamps...")
    audio_paths, word_timings = [], []
    profile = PERSONA_PROFILES[next((k for k in PERSONA_PROFILES.keys() if k in script_data.get("persona_used", "LISTICLE HOST").upper()), "LISTICLE HOST")]
    script_data["voice_gender"] = profile["gender"]
    
    scenes = script_data.get("script", [])
    for idx, seg in enumerate(tqdm(scenes, desc="Generating Audio", unit="scene")):
        path = os.path.join(ASSETS_DIR, f"voiceover_{idx+1}.mp3")
        text = re.sub(r'[*_#`\[\]()~^"“”‘’]', '', seg.get("voiceover", "")).strip() or f"Point number {idx+1}."
        success, scene_timings = False, []
        for attempt in range(1, 3):
            try:
                communicate = edge_tts.Communicate(text, language_cfg["voices"][profile["gender"]], rate=profile["rate"], pitch=profile["pitch"])
                with open(path, "wb") as f:
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio": f.write(chunk["data"])
                        elif chunk["type"] == "WordBoundary": scene_timings.append({"word": chunk["text"], "start": chunk["offset"] / 10000000.0, "end": (chunk["offset"] + chunk["duration"]) / 10000000.0})
                if os.path.exists(path) and os.path.getsize(path) > 500:
                    audio_paths.append(path); word_timings.append(scene_timings); success = True; break
            except Exception:
                pass
        if not success: return [], []
    return audio_paths, word_timings

def get_cached_asset(query):
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', query.lower().strip()) + ".jpg"
    cache_path = os.path.join(ASSET_CACHE_DIR, safe_name)
    if os.path.exists(cache_path): return Image.open(cache_path).convert("RGB"), cache_path
    return None, cache_path

def save_to_cache(img_bytes, cache_path):
    try:
        with open(cache_path, "wb") as f: f.write(img_bytes)
    except:
        pass

def fetch_hf_ai_image(prompt):
    if not HF_TOKEN: return None
    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={"inputs": prompt},
            timeout=25,
        )
        if response.status_code == 200 and response.content:
            try:
                return Image.open(io.BytesIO(response.content)).convert("RGB")
            except (UnidentifiedImageError, OSError):
                return None
    except requests.RequestException:
        pass
    return None

def get_image_hash(img_bytes):
    return hashlib.md5(img_bytes).hexdigest()

def get_bold_font(size, custom_font_name=None):
    font_paths = []
    if custom_font_name: font_paths.extend([custom_font_name, f"C:\\Windows\\Fonts\\{custom_font_name}", "C:\\Windows\\Fonts\\Nirmala.ttf", "C:\\Windows\\Fonts\\mangal.ttf"])
    font_paths.extend(["C:\\Windows\\Fonts\\segoeprb.ttf", "C:\\Windows\\Fonts\\arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"])
    for path in font_paths:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except:
                pass
    return ImageFont.load_default()

def draw_text_with_double_shadow(draw, position, text, font, fill, shadow_color=PALETTE["shadow"]):
    x, y = position
    draw.text((x + 3, y + 3), text, font=font, fill=shadow_color)
    draw.text((x - 1, y - 1), text, font=font, fill=shadow_color)
    draw.text((x, y), text, font=font, fill=fill)

def wrap_text_exact(text, font, max_width):
    words = text.split()
    lines, current_line = [], ""
    temp_draw = ImageDraw.Draw(Image.new("RGBA", (1,1)))
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = temp_draw.textbbox((0,0), test_line, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current_line = test_line
        else:
            if current_line: lines.append(current_line)
            current_line = word
    if current_line: lines.append(current_line)
    return lines

def fit_text_in_box(text, font_choice, max_width, max_height, start_size=85):
    temp_draw = ImageDraw.Draw(Image.new("RGBA", (1,1)))
    size = start_size
    while size > 20:
        font = get_bold_font(size, font_choice)
        lines = wrap_text_exact(text, font, max_width)
        total_h = sum((temp_draw.textbbox((0,0), line, font=font)[3] - temp_draw.textbbox((0,0), line, font=font)[1]) + 15 for line in lines)
        if total_h <= max_height: return font, lines
        size -= 4
    font = get_bold_font(20, font_choice)
    return font, wrap_text_exact(text, font, max_width)

def create_branded_slide(title_text, subtitle_text, is_outro=False, width=1080, height=1920, font_choice=None):
    base = Image.new("RGBA", (width, height), PALETTE["bg"] + (255,))
    overlay = Image.new("RGBA", (width, height), (0,0,0,0))
    draw = ImageDraw.Draw(overlay)
    
    draw.rectangle([0, 0, width, 40], fill=PALETTE["accent_primary"] + (220,))
    draw.rectangle([0, height - 40, width, height], fill=PALETTE["accent_secondary"] + (220,))
    
    box_coords = [60, 350, width - 60, height - 350]
    draw.rounded_rectangle(box_coords, radius=35, fill=PALETTE["bg_glass"], outline=PALETTE["glass_border"], width=2)
    
    base = Image.alpha_composite(base, overlay)
    draw_base = ImageDraw.Draw(base)
    
    logo_path = os.path.join(BRAND_ASSETS_DIR, "logo.png")
    if not os.path.exists(logo_path): logo_path = os.path.join(BRAND_ASSETS_DIR, "channels4_profile.jpg")
    logo_img = Image.open(logo_path).convert("RGBA").resize((140, 140), Image.Resampling.LANCZOS) if is_outro and os.path.exists(logo_path) else None

    if is_outro:
        max_q_height = (box_coords[3] - box_coords[1]) * 0.55
        font_title, wrapped_lines = fit_text_in_box(title_text, font_choice, width - 180, max_q_height, start_size=80)
        
        y_text = box_coords[1] + 60
        for line in wrapped_lines:
            bbox = draw_base.textbbox((0, 0), line, font=font_title)
            w = bbox[2] - bbox[0]
            draw_text_with_double_shadow(draw_base, ((width - w) / 2, y_text), line, font_title, fill=PALETTE["text_primary"])
            y_text += (bbox[3] - bbox[1]) + 15
        
        y_text += 60 
        cta_font = get_bold_font(55, font_choice)
        cta_bbox = draw_base.textbbox((0,0), subtitle_text, font=cta_font)
        cta_w, cta_h = cta_bbox[2] - cta_bbox[0], cta_bbox[3] - cta_bbox[1]
        
        pill_pad_x, pill_pad_y = 50, 30
        pill_coords = [(width - cta_w)/2 - pill_pad_x, y_text, (width + cta_w)/2 + pill_pad_x, y_text + cta_h + pill_pad_y*2]
        
        pill_overlay = Image.new("RGBA", base.size, (0,0,0,0))
        ImageDraw.Draw(pill_overlay).rounded_rectangle(pill_coords, radius=40, fill=(20, 20, 20, 220), outline=PALETTE["accent_secondary"], width=3)
        base = Image.alpha_composite(base, pill_overlay)
        draw_base = ImageDraw.Draw(base)
        
        draw_text_with_double_shadow(draw_base, ((width - cta_w)/2, y_text + pill_pad_y), subtitle_text, cta_font, fill=PALETTE["accent_secondary"])
        if logo_img: base.paste(logo_img, ((width - logo_img.size[0]) // 2, int(pill_coords[3]) + 40), logo_img)
    else:
        font_title = get_bold_font(85, font_choice)
        font_sub = get_bold_font(65, font_choice)
        y_text = 500
        for line in wrap_text_exact(title_text, font_title, width - 180):
            bbox = draw_base.textbbox((0, 0), line, font=font_title)
            w = bbox[2] - bbox[0]
            draw_text_with_double_shadow(draw_base, ((width - w) / 2, y_text), line, font_title, fill=PALETTE["text_primary"])
            y_text += (bbox[3] - bbox[1]) + 20
        if subtitle_text:
            y_text += 40
            for line in wrap_text_exact(subtitle_text, font_sub, width - 180):
                bbox = draw_base.textbbox((0, 0), line, font=font_sub)
                w = bbox[2] - bbox[0]
                draw_text_with_double_shadow(draw_base, ((width - w) / 2, y_text), line, font_sub, fill=PALETTE["accent_secondary"])
                y_text += (bbox[3] - bbox[1]) + 20
                
    return base.convert("RGBA")

def render_hook_card(bg_img, hook_text, width=1080, height=1920, font_choice=None):
    base = bg_img.convert("RGBA")
    box_coords = [60, 450, width - 60, height - 450]
    base.paste(base.crop(box_coords).filter(ImageFilter.GaussianBlur(radius=25)), box_coords)
    
    overlay = Image.new("RGBA", (width, height), (0,0,0,0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, 0, width, 40], fill=PALETTE["accent_primary"] + (240,))
    draw.rectangle([0, height - 40, width, height], fill=PALETTE["accent_secondary"] + (240,))
    draw.rounded_rectangle(box_coords, radius=40, fill=(10, 15, 30, 220), outline=PALETTE["accent_primary"], width=4)
    base = Image.alpha_composite(base, overlay)
    draw_base = ImageDraw.Draw(base)
    
    y_text = 650
    font_body, wrapped_lines = fit_text_in_box(hook_text, font_choice, width - 180, (box_coords[3] - box_coords[1]) - 150, start_size=75)
    for line in wrapped_lines:
        bbox = draw_base.textbbox((0, 0), line, font_body)
        w = bbox[2] - bbox[0]
        draw_text_with_double_shadow(draw_base, ((width - w) / 2, y_text), line, font_body, fill=PALETTE["accent_primary"])
        y_text += (bbox[3] - bbox[1]) + 20
    return base

def generate_karaoke_clip(chunk, active_index, font_path, video_width, output_path, bg_img_path=None, source_type="bg"):
    # Made canvas taller to support massive text
    canvas_w, canvas_h = int(video_width * 0.95), 450
    img = Image.new("RGBA", (canvas_w, canvas_h), (0,0,0,0))
    draw = ImageDraw.Draw(img)

    text_color = (255, 255, 255) 
    active_color = (0, 255, 255) # High-contrast Viral Cyan
    
    font_size = 130 # Massive subtitles
    font = get_bold_font(font_size, font_path)
    
    while font_size > 50:
        total_w = 0
        for i, wt in enumerate(chunk):
            f = get_bold_font(int(font_size * 1.15) if i == active_index else font_size, font_path)
            total_w += draw.textlength(wt['word'], font=f)
        total_w += draw.textlength(" ", font=font) * (len(chunk) - 1)
        
        if total_w < canvas_w - 40: 
            break
        font_size -= 5
        font = get_bold_font(font_size, font_path)

    space_w = draw.textlength(" ", font=get_bold_font(font_size, font_path))
    word_widths = [draw.textlength(wt['word'], font=get_bold_font(int(font_size * 1.15) if i == active_index else font_size, font_path)) for i, wt in enumerate(chunk)]
    total_text_w = sum(word_widths) + space_w * (len(chunk) - 1)
    
    x = (canvas_w - total_text_w) / 2
    y = (canvas_h - (font_size * 1.15)) / 2

    # Ultra-thick stroke for supreme legibility without a background box
    stroke_w = max(6, int(font_size * 0.12))

    for i, wt in enumerate(chunk):
        w_str = wt['word']
        is_active = (i == active_index)
        color = active_color if is_active else text_color
        
        current_fs = int(font_size * 1.15) if is_active else font_size
        current_font = get_bold_font(current_fs, font_path)
        
        y_offset = (font_size * 1.15) - current_fs 

        # Heavy Double Drop-Shadow
        draw.text((x + 10, y + y_offset + 10), w_str, font=current_font, fill=(0,0,0,200))
        
        # Main text with thick black stroke
        draw.text((x, y + y_offset), w_str, font=current_font, fill=color, stroke_width=stroke_w, stroke_fill=(0,0,0,255))
        
        x += word_widths[i] + space_w

    img.save(output_path, "PNG")
    return output_path

def _scene_visual_segment_count(scene_duration):
    """Keep each visual beat no longer than four seconds when narration is long."""
    try:
        duration = max(0.0, float(scene_duration))
    except (TypeError, ValueError):
        duration = 0.0
    return max(1, int(math.ceil(duration / 4.0)))


def _caption_y_position(video_height, scene_source_type="", format_mode="regular"):
    """Place captions away from Top-5 ranking text and the lower safe area."""
    if str(format_mode or "").lower() == "top5":
        return int(video_height * 0.67)
    if str(scene_source_type or "").lower() == "person":
        return int(video_height * 0.60)
    return int(video_height * 0.52)



def _normalize_audio_loudness(input_path, output_path):
    """Normalize final program audio to approximately -14 LUFS with FFmpeg."""
    ffmpeg_bin = os.getenv("IMAGEIO_FFMPEG_EXE", "ffmpeg").strip() or "ffmpeg"
    if not os.path.isabs(ffmpeg_bin):
        ffmpeg_bin = shutil.which(ffmpeg_bin) or ffmpeg_bin
    command = [
        ffmpeg_bin, "-y", "-i", input_path,
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "copy",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(
            "Final audio loudness normalization failed: " + str(detail)[-1600:]
        ) from exc
    return output_path


def compile_video(scene_visual_packages, audio_paths, word_timings, language_cfg, format_mode):
    print("\n🎬 Rendering Kinetic Final Video (captions, motion, branding and loudness)...")
    if not scene_visual_packages:
        raise ValueError("No visual packages were supplied.")
    if not audio_paths:
        raise ValueError("No audio files were supplied.")

    video_output_path = os.path.join(ASSETS_DIR, "final_video_output.mp4")
    pre_loudness_path = os.path.join(ASSETS_DIR, "final_video_preloudnorm.mp4")
    from moviepy import (
        ImageClip,
        AudioFileClip,
        CompositeVideoClip,
        concatenate_videoclips,
    )
    from moviepy.audio.AudioClip import CompositeAudioClip
    from moviepy.audio.fx import AudioLoop

    width, height = 1080, 1920
    final_clips = []
    audio_clips = []
    bgm_clip = None

    sfx_files = [
        f for f in os.listdir(SFX_DIR)
        if f.lower().endswith(".mp3")
    ] if os.path.exists(SFX_DIR) else []
    font_path = language_cfg.get("font")

    try:
        for idx, layer_paths in enumerate(
            tqdm(scene_visual_packages, desc="Rendering Video Scenes", unit="scene")
        ):
            if not layer_paths:
                continue

            audio = None
            if idx < len(audio_paths) and os.path.exists(audio_paths[idx]):
                audio = AudioFileClip(audio_paths[idx])
                audio_clips.append(audio)

            # Visual scene boundaries must not create artificial narration gaps.
            # Audio is already encoded at its natural duration, so concatenate
            # scene clips without an extra silence tail.
            scene_duration = max(0.1, audio.duration if audio else 4.0)
            bg_image_file = layer_paths[0]["image"]
            scene_source_type = layer_paths[0].get("source_type", "bg")
            segment_count = _scene_visual_segment_count(scene_duration)
            segment_duration = scene_duration / segment_count

            background_clips = []
            for cut_idx in range(segment_count):
                bg_clip = ImageClip(bg_image_file).with_duration(segment_duration)

                def scale_at(
                    t,
                    scene_index=idx,
                    cut_index=cut_idx,
                    segment_time=segment_duration,
                ):
                    progress = min(
                        max(0.0, float(t)) / max(0.1, segment_time),
                        1.0,
                    )
                    if scene_index == 0:
                        return 1.18 + 0.10 * progress
                    base = 1.08 + 0.03 * ((scene_index + cut_index) % 3)
                    if cut_index % 2 == 0:
                        return base + 0.07 * progress
                    return base + 0.07 * (1.0 - progress)

                def position_at(
                    t,
                    scene_index=idx,
                    cut_index=cut_idx,
                    segment_time=segment_duration,
                ):
                    progress = min(
                        max(0.0, float(t)) / max(0.1, segment_time),
                        1.0,
                    )
                    scale = scale_at(t, scene_index, cut_index)
                    overflow_x = max(0.0, (width * scale - width) / 2.0)
                    overflow_y = max(0.0, (height * scale - height) / 2.0)
                    if scene_index == 0:
                        return (
                            -overflow_x * (0.35 + 0.35 * progress),
                            -overflow_y * (0.15 + 0.25 * progress),
                        )
                    direction = -1.0 if (scene_index + cut_index) % 2 else 1.0
                    return (
                        -overflow_x * (0.15 + 0.45 * progress) * direction,
                        -overflow_y * (0.10 + 0.25 * progress) * (-direction),
                    )

                background_clips.append(
                    bg_clip
                    .resized(scale_at)
                    .with_position(position_at)
                    .with_start(cut_idx * segment_duration)
                )

            if idx > 0 and sfx_files:
                try:
                    sfx_clip = (
                        AudioFileClip(os.path.join(SFX_DIR, random.choice(sfx_files)))
                        .multiply_volume(0.3)
                        .with_duration(min(0.5, scene_duration))
                    )
                    audio_clips.append(sfx_clip)
                    scene_audio = (
                        CompositeAudioClip([audio, sfx_clip])
                        if audio is not None
                        else sfx_clip
                    )
                except Exception:
                    scene_audio = audio
            else:
                scene_audio = audio

            text_clips = []
            scene_wt = word_timings[idx] if idx < len(word_timings) else []
            if not scene_wt:
                raw_text = (
                    layer_paths[0].get("narration_text")
                    or layer_paths[0].get("text")
                    or ""
                )
                words = str(raw_text).split()
                if words:
                    dur_per_word = max(0.1, (scene_duration - 0.2) / len(words))
                    curr_t = 0.1
                    scene_wt = []
                    for word in words:
                        scene_wt.append(
                            {
                                "word": word,
                                "start": curr_t,
                                "end": min(scene_duration, curr_t + dur_per_word),
                            }
                        )
                        curr_t += dur_per_word

            chunks, current_chunk, current_len = [], [], 0
            for wt in scene_wt:
                if not isinstance(wt, dict):
                    continue
                raw_word = safe_text(wt.get("word"), "")
                w_text = re.sub(
                    r"[^\x00-\x7F\u0900-\u097F\u0C00-\u0C7F]+",
                    "",
                    raw_word,
                ).strip()
                if not w_text:
                    continue
                wt["word"] = w_text
                if current_len + len(w_text) > 18 and current_chunk:
                    chunks.append(current_chunk)
                    current_chunk, current_len = [], 0
                current_chunk.append(wt)
                current_len += len(w_text) + 1
            if current_chunk:
                chunks.append(current_chunk)

            safe_y_pos = _caption_y_position(height, scene_source_type, format_mode)
            for chunk_idx, chunk in enumerate(chunks):
                for active_idx, wt in enumerate(chunk):
                    start_t = max(0.0, float(wt.get("start", 0.0)))
                    end_t = min(
                        scene_duration,
                        max(
                            start_t + 0.08,
                            float(wt.get("end", start_t + 0.08)),
                        ),
                    )
                    if start_t >= scene_duration or end_t <= start_t:
                        continue
                    sub_path = os.path.join(
                        ASSETS_DIR,
                        f"sub_{idx}_{chunk_idx}_{active_idx}.png",
                    )
                    generate_karaoke_clip(
                        chunk, active_idx, font_path, width, sub_path
                    )
                    text_clips.append(
                        ImageClip(sub_path)
                        .with_start(start_t)
                        .with_duration(end_t - start_t)
                        .with_position(("center", safe_y_pos))
                    )


            from branding_runtime import build_scene_branding_overlays

            source_provenance = layer_paths[0].get("asset_provenance") or {}
            source_credit = (
                source_provenance
                if isinstance(source_provenance, dict)
                else str(layer_paths[0].get("source_credit") or "").strip()
            )
            branding_layers = [
                ImageClip(rgba).with_duration(scene_duration)
                for rgba in build_scene_branding_overlays(
                    None, width, height, source_credit
                )
            ]
            scene_layers = background_clips + text_clips + branding_layers
            scene = CompositeVideoClip(
                scene_layers, size=(width, height)
            ).with_duration(scene_duration)

            if scene_audio is not None:
                scene = scene.with_audio(scene_audio)

            final_clips.append(scene)

        if not final_clips:
            raise ValueError("No video scenes were rendered.")

        final_master = concatenate_videoclips(final_clips, method="compose")

        bgm_files = [
            f for f in os.listdir(BGM_DIR)
            if f.lower().endswith(".mp3")
        ] if os.path.exists(BGM_DIR) else []

        if bgm_files:
            try:
                bgm_clip = AudioFileClip(
                    os.path.join(BGM_DIR, random.choice(bgm_files))
                ).multiply_volume(0.08)
                if bgm_clip.duration < final_master.duration:
                    bgm_clip = AudioLoop(duration=final_master.duration).apply(bgm_clip)
                else:
                    bgm_clip = bgm_clip.with_duration(final_master.duration)
                audio_layers = [bgm_clip]
                if final_master.audio is not None:
                    audio_layers.insert(0, final_master.audio)
                final_master = final_master.with_audio(
                    CompositeAudioClip(audio_layers)
                )
            except Exception:
                bgm_clip = None

        if os.path.exists(pre_loudness_path):
            try:
                os.remove(pre_loudness_path)
            except OSError:
                pass
        if os.path.exists(video_output_path):
            try:
                os.remove(video_output_path)
            except OSError:
                pass

        print("   [+] Writing video file to disk for Quality Control...")
        final_master.write_videofile(
            pre_loudness_path,
            fps=24,
            preset="ultrafast",
            codec="libx264",
            audio_codec="aac",
            logger="bar",
            temp_audiofile=os.path.join(ASSETS_DIR, "temp_audio.m4a"),
            remove_temp=True,
        )
        print("   [+] Normalizing final audio to approximately -14 LUFS...")
        _normalize_audio_loudness(pre_loudness_path, video_output_path)
        return video_output_path

    finally:
        for clip in audio_clips:
            try:
                clip.close()
            except Exception:
                pass
        for clip in final_clips:
            try:
                clip.close()
            except Exception:
                pass
        if bgm_clip is not None:
            try:
                bgm_clip.close()
            except Exception:
                pass
        try:
            if "final_master" in locals():
                final_master.close()
        except Exception:
            pass
        try:
            if os.path.exists(pre_loudness_path):
                os.remove(pre_loudness_path)
        except OSError:
            pass

def upload_to_youtube(
    video_path,
    script_data,
    genre_cfg,
    publish_mode,
    trend_keyword=None,
    title_override=None,
    description_override=None,
    comment_override=None,
):
    print("\n🚀 Initializing Live YouTube Upload...")
    try:
        import googleapiclient.discovery
        from googleapiclient.http import MediaFileUpload

        if not video_path or not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        creds = get_google_credentials()
        youtube = googleapiclient.discovery.build(
            "youtube", "v3", credentials=creds
        )

        raw_title = safe_text(
            title_override or script_data.get("title"), genre_cfg.get("label", "Shorts")
        )

        # Keep the approved editorial title intact. Trend keywords may enrich
        # the description, but they must never be prepended to the title after
        # the channel-specific title scorer has already selected it.
        title = re.sub(r"\s*#shorts\b", "", raw_title, flags=re.IGNORECASE).strip()[:100]
        if not title:
            title = "Shorts"

        desc_body = safe_text(description_override or script_data.get("seo_description"), "")
        if trend_keyword and trend_keyword.lower() not in desc_body.lower():
            desc_body = f"Trending now: {trend_keyword}. {desc_body}"

        category_tags = []
        if trend_keyword:
            trend_tag = re.sub(r"[^a-zA-Z0-9]", "", trend_keyword)
            if trend_tag:
                category_tags.append(f"#{trend_tag}")
        for tag in genre_cfg.get("hashtags", []):
            clean_tag = str(tag or "").strip()
            if clean_tag and not clean_tag.startswith("#"):
                clean_tag = "#" + re.sub(r"[^a-zA-Z0-9]", "", clean_tag)
            if clean_tag:
                category_tags.append(clean_tag)
        seen_tags = set()
        clean_tags = []
        for tag in category_tags:
            key = tag.casefold()
            if key not in seen_tags:
                seen_tags.add(key)
                clean_tags.append(tag)
            if len(clean_tags) == 3:
                break
        hashtags_str = " ".join(clean_tags)
        description = (
            f"{desc_body}\n\n{hashtags_str}\n\nFollow for daily updates!"
        ).strip()
        description = append_image_credits(
            description,
            script_data.get("visual_provenance") or [],
            max_bytes=5000,
        )
        description = append_research_sources(
            description,
            script_data.get("research_sources") or [],
            max_chars=5000,
        )

        tags = script_data.get("tags", ["Shorts", genre_cfg.get("label", "Shorts")])
        if not isinstance(tags, list):
            tags = [safe_text(tags, "Shorts")]
        tags = [safe_text(tag) for tag in tags if safe_text(tag)]
        tags = tags[:30]

        privacy = "private" if publish_mode == "private" else "public"
        if privacy == "public" and bool(
            (script_data or {}).get("public_publish_blocked")
        ):
            raise RuntimeError(
                "Public upload is blocked because the script pipeline marked this "
                "production run as private-only."
            )
        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags,
                "categoryId": str(genre_cfg.get("category_id", "24")),
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            video_path,
            chunksize=-1,
            resumable=True,
            mimetype="video/mp4",
        )
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(
                    f"   [Upload Progress] {int(status.progress() * 100)}%"
                )

        vid_id = response.get("id")
        if not vid_id:
            raise RuntimeError("YouTube upload completed without a video ID.")

        if privacy == "public":
            response = _ensure_youtube_public_visibility(youtube, response, vid_id)
        _report_youtube_upload_visibility(response, vid_id, privacy)

        print(f"   [+] Successfully uploaded to YouTube! Video ID: {vid_id}")

        # Public publishing uses the dashboard-approved pinned-comment text.
        # YouTube exposes top-level comment creation through commentThreads.insert;
        # the existing OAuth scope already includes youtube.force-ssl.
        if privacy == "public":
            public_comment = safe_text(
                comment_override or script_data.get("pinned_comment"), ""
            ).strip()
            if public_comment:
                try:
                    comment_body = {
                        "snippet": {
                            "channelId": response.get("snippet", {}).get("channelId", ""),
                            "videoId": vid_id,
                            "topLevelComment": {
                                "snippet": {
                                    "textOriginal": public_comment[:10000],
                                }
                            },
                        }
                    }
                    comment_response = youtube.commentThreads().insert(
                        part="snippet",
                        body=comment_body,
                    ).execute()
                    comment_id = str(
                        (comment_response.get("snippet") or {}).get("topLevelComment", {}).get("id")
                        or comment_response.get("id")
                        or ""
                    ).strip()
                    if comment_id:
                        print(f"   [+] Public comment posted successfully. Comment ID: {comment_id}")
                    else:
                        print("   [!] Public upload succeeded, but YouTube returned no comment ID.")
                except Exception as comment_exc:
                    # Never turn a successful video upload into a failed upload
                    # solely because the follow-up comment could not be posted.
                    print(
                        f"   [!] Public upload succeeded, but the comment could not be posted: "
                        f"{type(comment_exc).__name__}: {comment_exc}"
                    )
            else:
                print("   [!] Public upload succeeded without a comment because the approved comment was empty.")

        return vid_id
    except YouTubePublicVisibilityError as exc:
        print(f"   [!] YouTube public upload visibility was blocked: {exc}", flush=True)
        raise
    except Exception as exc:
        print(f"   [!] YouTube upload failed: {exc}")
        if str(publish_mode).lower() == "public":
            raise
        return None

def font_preflight_check(lang_cfg):
    print(f"\n🔍 Running Font Preflight Check for {lang_cfg['label']}...")
    try:
        font_name = lang_cfg["font"]
        font_path = font_name if os.path.exists(font_name) else os.path.join("C:\\Windows\\Fonts", font_name)
        if not os.path.exists(font_path):
            print(f"   [!] Warning: '{font_name}' not found locally. Falling back to Arial Bold.")
            lang_cfg["font"] = "arialbd.ttf"
        
        font = get_bold_font(50, lang_cfg["font"])
        img = Image.new("RGBA", (200, 100), (0,0,0,0))
        ImageDraw.Draw(img).text((10,10), "Test", font=font, fill="white")
        print(f"   [+] Font preflight passed successfully!")
    except Exception as e:
        pass


# ==========================================
# STEP 1 UPDATE: STREAMLIT DASHBOARD SUPPORT
# ==========================================
def run_robot(web_config=None):
    print("==================================================")
    print("   PRO VIRAL STUDIO AUTOMATION FACTORY (v2)       ")
    print("==================================================")

    # If web_config is passed, it forces headless mode automatically
    is_headless = "--headless" in sys.argv or web_config is not None
    def _run_manual_workflow_hook(payload, hook_key, description):
        """Run a dashboard/manual workflow checkpoint from the core production path."""
        if not isinstance(web_config, dict):
            return payload
        hook = web_config.get(hook_key)
        if not callable(hook):
            return payload
        print(f"   [Workflow Gate] {description}", flush=True)
        result = hook(payload)
        return result if result is not None else payload

    conn = sqlite3.connect(DB_PATH)

    # Every production execution gets an isolated workspace. The previous
    # shared output directory allowed a new run to delete a different run's
    # READY_FOR_UPLOAD video during the startup cleanup.
    global ASSETS_DIR
    requested_run_id = (
        str(web_config.get("run_id") or "").strip()
        if isinstance(web_config, dict)
        else ""
    )
    workspace_id = requested_run_id or (
        f"standalone-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    workspace_id = re.sub(r"[^A-Za-z0-9._-]+", "_", workspace_id).strip("._") or "run"
    ASSETS_DIR = os.path.join(BASE_DIR, "output", workspace_id)
    os.makedirs(ASSETS_DIR, exist_ok=True)

    try:
        init_db(conn)
        cleanup_obsolete_run_workspaces(conn)
        enforce_cache_ttl_hygiene()

        stale_threshold = datetime.now() - timedelta(hours=2)
        c = conn.cursor()
        c.execute(
            "UPDATE vault SET video_id = 'REJECTED', reported = 1, "
            "rejected_reason = 'Stale timeout', status = 'REJECTED', updated_at = CURRENT_TIMESTAMP "
            "WHERE status = 'RUNNING' AND date_used < ?",
            (stale_threshold,),
        )
        conn.commit()

        sync_file = os.path.join(BASE_DIR, "last_sync.txt")
        locked_story = bool(
            isinstance(web_config, dict)
            and isinstance(web_config.get("selected_story"), dict)
            and str(web_config["selected_story"].get("title") or "").strip()
        )
        should_sync = not locked_story
        if locked_story:
            print(
                "   [Learning] Analytics sync skipped: dashboard story is already locked; "
                "no discovery ranking depends on a fresh learning sweep.",
                flush=True,
            )
        if os.path.exists(sync_file):
            try:
                with open(sync_file, "r", encoding="utf-8") as f:
                    last_sync = datetime.fromisoformat(f.read().strip())
                should_sync = (
                    datetime.now() - last_sync >= timedelta(hours=24)
                )
            except (ValueError, OSError):
                should_sync = True

        if should_sync:
            analytics_result = run_analytics_sweep(conn)
            # Only advance the cooldown after the exact-video sync has actually
            # completed without analytics errors. The dashboard used to replace
            # this function with a no-op, which could falsely mark stale data fresh.
            if not isinstance(analytics_result, dict) or not int(
                analytics_result.get("analytics_errors", 0) or 0
            ):
                try:
                    with open(sync_file, "w", encoding="utf-8") as f:
                        f.write(datetime.now().isoformat())
                except OSError:
                    pass

        # ASSETS_DIR is already a fresh run-scoped workspace. Never wipe
        # the shared output root here: earlier READY_FOR_UPLOAD artifacts may
        # still be waiting for human upload approval.
        trend_keyword = custom_q = custom_rss = None

        if web_config:
            print("\n🌐 WEB DASHBOARD MODE ACTIVATED: Pulling settings from Streamlit.")
            format_mode = web_config.get("format_mode", "regular")
            cat_choice = web_config.get("category", "national_global_affairs")
            # Older dashboard sessions used a virtual AI category. Normalize
            # it before every production lookup so the real Sports config is used.
            if str(cat_choice or "").strip().lower() == "ai_recommendation":
                cat_choice = "sports"
            lang_key = web_config.get("language", "english")
            lang_cfg = LANGUAGES.get(lang_key, LANGUAGES["english"])
            combo_key = f"{format_mode}|{cat_choice}|{lang_key}"
            trend_keyword = web_config.get("trend_keyword")
            custom_q = web_config.get("custom_q")
            custom_rss = web_config.get("custom_rss")
        elif is_headless:
            print("\n👻 GHOST MODE ACTIVATED: Running fully headless Auto-Pilot.")
            format_mode, cat_choice, lang_cfg, combo_key = auto_pilot_selection(conn)
        else:
            print(
                "\nSelect Factory Pipeline:\n"
                "  [1] Manual Mode (Full Customization)\n"
                "  [2] Auto-Pilot Mode (Epsilon-Greedy Intelligence)\n"
                "  [3] Cricket Focus Pipeline (Dedicated Cricket Engine)"
            )
            mode_input = input("\nEnter choice (1-3): ").strip()

            if mode_input == "2":
                format_mode, cat_choice, lang_cfg, combo_key = auto_pilot_selection(conn)
            elif mode_input == "3":
                (
                    format_mode,
                    cat_choice,
                    lang_cfg,
                    combo_key,
                    trend_keyword,
                    custom_q,
                    custom_rss,
                ) = cricket_pipeline_prompts()
            else:
                (
                    format_mode,
                    cat_choice,
                    lang_cfg,
                    combo_key,
                    trend_keyword,
                    custom_q,
                    custom_rss,
                ) = manual_prompts(conn)

        font_preflight_check(lang_cfg)
        genre_cfg = CONTENT_CATEGORIES[cat_choice]
        bonuses, last_genre = get_genre_bonuses(conn)

        selected_story = web_config.get("selected_story") if isinstance(web_config, dict) else None
        if isinstance(selected_story, dict) and str(selected_story.get("title", "")).strip():
            # Dashboard production must render the story the user explicitly selected.
            # Never perform a second broad discovery pass here.
            pool = [dict(selected_story)]
            print(
                f"   [Workflow] Production story locked: {selected_story.get('title')}",
                flush=True,
            )
        else:
            pool = gather_and_filter_stories(
                conn,
                cat_choice,
                genre_cfg,
                trend_keyword=trend_keyword,
                custom_gnews_q=custom_q,
                custom_rss_url=custom_rss,
            )

        if format_mode == "top5":
            cands = editorial_gate_batch(
                pool, bonuses, last_genre, format_mode
            )
            if not cands or len(cands) < 5:
                cands = pool
            if not cands or len(cands) < 5:
                print("   [!] Not enough stories for Top 5.")
                return

            top5_stories = [dict(item) for item in cands[:5] if isinstance(item, dict)]
            aggregated_event_evidence = [
                dict(evidence)
                for story in top5_stories
                for evidence in (story.get("event_evidence") or [])
                if isinstance(evidence, dict)
            ]
            event_entities = []
            event_actions = []
            for story in top5_stories:
                for value in (story.get("event_entities") or []):
                    value = str(value or "").strip()
                    if value and value not in event_entities:
                        event_entities.append(value)
                for value in (story.get("event_actions") or []):
                    value = str(value or "").strip()
                    if value and value not in event_actions:
                        event_actions.append(value)
            story_payload = {
                "title": f"{genre_cfg['label']} - {datetime.now().strftime('%b %d')}",
                "text": json.dumps(top5_stories),
                "summary": "\n".join(
                    str(story.get("summary") or story.get("description") or story.get("title") or "").strip()
                    for story in top5_stories
                    if str(story.get("summary") or story.get("description") or story.get("title") or "").strip()
                ),
                "event_evidence": aggregated_event_evidence,
                "event_entities": event_entities,
                "event_actions": event_actions,
            }
            main_topic = story_payload["title"]
        else:
            if isinstance(selected_story, dict) and str(selected_story.get("title", "")).strip():
                # The dashboard already made the editorial selection. Re-scoring that
                # single locked story adds an avoidable LLM call and can introduce a
                # second provider failure without changing the user’s selection.
                story_payload = dict(selected_story)
                from editorial_runtime import _fallback_editorial_scores
                for key, value in _fallback_editorial_scores(story_payload).items():
                    story_payload.setdefault(key, value)
                print("   [Workflow] Editorial LLM scoring skipped: dashboard story is already locked.", flush=True)
            else:
                cands = editorial_gate_batch(
                    pool, bonuses, last_genre, format_mode
                )
                if not cands:
                    print("   [!] No viable stories found.")
                    return
                story_payload = cands[0]
            main_topic = story_payload["title"]

        insert_cursor = conn.execute(
            "INSERT OR IGNORE INTO vault "
            "(topic, date_used, genre, video_id) VALUES (?, ?, ?, ?)",
            (main_topic, datetime.now(), cat_choice, "PENDING_QC"),
        )
        if getattr(insert_cursor, "rowcount", 1) != 1:
            raise RuntimeError(
                "Production run record was not created as a new vault row; "
                "refusing to continue with ambiguous run identity."
            )
        run_row_id = getattr(insert_cursor, "lastrowid", None)
        if not run_row_id:
            raise RuntimeError(
                "Production run row identity is unavailable; refusing to continue."
            )
        conn.commit()

        def _mark_run_status(status, reason=""):
            assignments = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            values = [str(status)]
            if reason:
                assignments.append("rejected_reason = ?")
                values.append(str(reason)[:1000])
            values.append(run_row_id)
            conn.execute(
                f"UPDATE vault SET {', '.join(assignments)} WHERE rowid=?",
                values,
            )
            conn.commit()

        # PENDING_QC is reserved for an actual QC decision, not an active
        # production. Active human-review stages can last up to 24 hours, so
        # they must not look like stale runs to the startup sweeper.
        conn.execute(
            "UPDATE vault SET status='RUNNING', updated_at=CURRENT_TIMESTAMP WHERE rowid=?",
            (run_row_id,),
        )
        conn.commit()

        dashboard_manual_control = bool(
            isinstance(web_config, dict) and web_config.get("_dashboard_manual_control")
        )
        if dashboard_manual_control and not callable(
            web_config.get("_manual_script_review_hook")
        ):
            raise RuntimeError(
                "Production blocked: dashboard manual script review hook is not installed."
            )

        script_data = write_script(
            story_payload, lang_cfg, cat_choice, conn, format_mode
        )
        if not script_data:
            reason = "Script generation returned no usable script."
            _mark_run_status("FAILED", reason)
            print(f"   [!] Error: {reason}")
            return

        # The script router owns the one bounded pre-review duration repair.
        # This remains a fail-closed assertion in case an alternate binding bypasses it.
        delivery_profile = str(
            script_data.get("delivery_profile")
            or script_data.get("persona_used")
            or "LISTICLE HOST"
        ).strip().upper()
        persona_profile = PERSONA_PROFILES.get(
            delivery_profile,
            PERSONA_PROFILES["LISTICLE HOST"],
        )
        script_data["delivery_profile"] = delivery_profile
        duration_estimate = estimate_narration_duration(script_data, persona_profile)
        script_data["estimated_duration_seconds"] = duration_estimate["seconds"]
        script_data["estimated_duration_word_count"] = duration_estimate["word_count"]
        script_data["estimated_duration_effective_wpm"] = duration_estimate["effective_wpm"]
        script_data["duration_band"] = classify_narration_duration(duration_estimate["seconds"])
        print(
            f"   [Script Duration] Initial estimate: {duration_estimate['seconds']:.1f}s "
            f"({duration_estimate['word_count']} words at {duration_estimate['effective_wpm']:.0f} WPM).",
            flush=True,
        )
        if duration_estimate["seconds"] >= 30.0:
            raise RuntimeError(
                f"Initial script duration gate failed ({duration_estimate['seconds']:.1f}s); "
                "the overlong script was rejected before audio generation."
            )

        if dashboard_manual_control:
            conn.execute(
                "UPDATE vault SET status='WAITING_SCRIPT_REVIEW', updated_at=CURRENT_TIMESTAMP WHERE rowid=?",
                (run_row_id,),
            )
            conn.commit()
        script_data = _run_manual_workflow_hook(
            script_data,
            "_manual_script_review_hook",
            "Manual script review is active. Waiting for the dashboard decision.",
        )
        if dashboard_manual_control:
            conn.execute(
                "UPDATE vault SET status='RUNNING', updated_at=CURRENT_TIMESTAMP WHERE rowid=?",
                (run_row_id,),
            )
            conn.commit()
        if not isinstance(script_data, dict):
            raise RuntimeError("Manual script review returned an invalid script payload.")

        titles = script_data.get("titles") or [main_topic]
        if not isinstance(titles, list):
            titles = [safe_text(titles, main_topic)]
        titles = [safe_text(t, main_topic) for t in titles] or [main_topic]

        try:
            rec_idx = int(script_data.get("recommended_title_index", 1))
        except (TypeError, ValueError):
            rec_idx = 1
        rec_idx = max(1, min(rec_idx, len(titles)))

        # Bypass manual title selection if running from Dashboard or Headless
        if web_config or is_headless:
            script_data["title"] = titles[rec_idx - 1]
        else:
            print("\n🛑 A/B TITLE TESTING GATE")
            for i, title in enumerate(titles):
                print(f"  [{i + 1}] {title}")
            try:
                choice = input(
                    f"\nChoice (1-{len(titles)}) or Enter for AI favorite "
                    f"[#{rec_idx}]: "
                ).strip()
                choice_idx = int(choice) if choice else rec_idx
                if not 1 <= choice_idx <= len(titles):
                    raise ValueError
                script_data["title"] = titles[choice_idx - 1]
            except (ValueError, EOFError):
                script_data["title"] = titles[rec_idx - 1]

        # Persist the approved script before expensive narration/visual work.
        # This is the recovery source if the process later reaches READY_FOR_UPLOAD
        # and the Streamlit session itself is recreated.
        conn.execute(
            """UPDATE vault SET script_json=?, format_used=?, language_used=?,
            trend_keyword=?, updated_at=CURRENT_TIMESTAMP WHERE rowid=?""",
            (
                json.dumps(script_data, ensure_ascii=False),
                str(format_mode or ""),
                str(lang_key if "lang_key" in locals() else ""),
                str(trend_keyword or ""),
                run_row_id,
            ),
        )
        conn.commit()

        print("\n⚙️ Starting Asset Generation & Rendering Pipeline...")
        try:
            audio_paths, word_timings = asyncio.run(
                generate_voiceover_and_timestamps(script_data, lang_cfg)
            )
            if not audio_paths:
                reason = "Voiceover generation failed to produce audio files."
                _mark_run_status("FAILED", reason)
                print(f"   [!] Error: {reason}")
                return

            # The audio runtime already measures every generated scene. Reuse that
            # authoritative total; only fall back to a second pass for legacy/alternate
            # audio providers that do not populate the runtime duration fields.
            stored_scene_durations = script_data.get("audio_scene_durations")
            stored_total_duration = script_data.get("audio_total_duration")
            if (
                isinstance(stored_scene_durations, list)
                and len(stored_scene_durations) == len(audio_paths)
                and stored_total_duration is not None
            ):
                audio_duration = {
                    "scene_durations": [float(value) for value in stored_scene_durations],
                    "total_seconds": float(stored_total_duration),
                    "scene_count": len(stored_scene_durations),
                }
            else:
                audio_duration = measure_audio_duration(audio_paths)
            tts_qc = validate_tts_duration(
                script_data.get("estimated_duration_seconds"),
                audio_duration["total_seconds"],
            )
            script_data["actual_audio_duration_seconds"] = audio_duration["total_seconds"]
            script_data["tts_duration_qc"] = tts_qc
            print(
                f"   [TTS Duration QC] Actual {audio_duration['total_seconds']:.1f}s vs "
                f"estimated {float(script_data.get('estimated_duration_seconds') or 0):.1f}s "
                f"(delta {tts_qc.get('delta_seconds', 0):+.1f}s).",
                flush=True,
            )
            if not tts_qc.get("passed"):
                raise RuntimeError(
                    "TTS duration materially differs from the pre-TTS estimate; "
                    "stopping without silently rewriting the approved script."
                )
            if audio_duration["total_seconds"] > 30.0:
                raise RuntimeError(
                    f"Final synthesized narration is over 30s ({audio_duration['total_seconds']:.1f}s); "
                    "stopping before visual rendering."
                )

            visuals = asyncio.run(
                process_visuals_async(script_data, lang_cfg, format_mode)
            )
            if not visuals:
                print("   [!] Error: Visual sourcing failed to produce packages.")
                return

            if dashboard_manual_control and not callable(
                web_config.get("_manual_visual_review_hook")
            ):
                raise RuntimeError(
                    "Rendering blocked: dashboard manual visual review hook is not installed."
                )

            if dashboard_manual_control:
                conn.execute(
                    "UPDATE vault SET status='WAITING_VISUAL_REVIEW', updated_at=CURRENT_TIMESTAMP WHERE rowid=?",
                    (run_row_id,),
                )
                conn.commit()
            visuals = _run_manual_workflow_hook(
                visuals,
                "_manual_visual_review_hook",
                "Manual visual review is active. Rendering is paused until the dashboard decision.",
            )
            if dashboard_manual_control:
                conn.execute(
                    "UPDATE vault SET status='RUNNING', updated_at=CURRENT_TIMESTAMP WHERE rowid=?",
                    (run_row_id,),
                )
                conn.commit()
            if not isinstance(visuals, list):
                raise RuntimeError("Manual visual review returned an invalid visual package.")

            video_path = compile_video(
                visuals, audio_paths, word_timings, lang_cfg, format_mode
            )
        except Exception as exc:
            print("\n\n" + "!" * 60)
            print("💥 PIPELINE CRASHED AFTER TOPIC SELECTION:")
            print("!" * 60)
            print(f"{type(exc).__name__}: {exc}")
            print("!" * 60)
            return

        print("\n🛑 POST-RENDER VALIDATION GATE...")
        if not video_path or not os.path.exists(video_path):
            reason = "Post-render validation failed: final video is missing."
            _mark_run_status("FAILED", reason)
            print(f"   [!] {reason}")
            return

        video_size = os.path.getsize(video_path)
        if video_size < 500_000:
            reason = "Post-render validation failed: final video is under 500KB."
            _mark_run_status("FAILED", reason)
            print(f"   [!] {reason}")
            return

        try:
            from moviepy import VideoFileClip
            with VideoFileClip(video_path) as vfc:
                actual_dur = vfc.duration
            print(
                f"   [+] Validation Passed: Output generated successfully "
                f"({actual_dur:.1f}s, {video_size / 1_000_000:.1f}MB)."
            )
        except Exception as exc:
            print(f"   [!] Could not validate video duration: {exc}")

        post_render_hook = web_config.get("_manual_post_render_hook") if isinstance(web_config, dict) else None
        if callable(post_render_hook):
            post_render_hook(video_path)

        # Persist the visual rights ledger before the human upload gate so the
        # selected/rejected render remains auditable.
        conn.execute(
            "UPDATE vault SET asset_credits_json=? WHERE rowid=?",
            (
                json.dumps(script_data.get("visual_provenance") or [], ensure_ascii=False),
                run_row_id,
            ),
        )
        conn.commit()

        # Dashboard production is always manual-QC and never auto-uploads.
        if isinstance(web_config, dict) and web_config.get("manual_qc_required"):
            print("   [Workflow] Manual QC complete for rendering. Upload remains a dashboard-only action.", flush=True)
            return

        if not is_headless and not web_config:
            print("\n🔍 QUALITY CONTROL GATE")
            print(f"🎬 Title: {script_data.get('title')}")
            print(f"📁 Video ready for review at: {video_path}")
            qc_choice = input(
                "\n[1] Approve & Upload\n[2] Reject, Delete & Abort\nChoice: "
            ).strip()
            if qc_choice != "1":
                safe_cleanup(ASSETS_DIR)
                return

        # Assign publishing mode
        if web_config or is_headless:
            pub_mode = web_config.get("publish_mode", "private") if web_config else "private"
        else:
            pub_mode = "private" if input("  [1] Public\n  [2] Private\nChoice: ").strip() == "2" else "now"

        vid_id = upload_to_youtube(
            video_path,
            script_data,
            genre_cfg,
            pub_mode,
            trend_keyword=trend_keyword,
        )
        if not vid_id:
            _mark_run_status(
                "READY_FOR_UPLOAD",
                "Rendered video is valid but the YouTube upload did not complete.",
            )
            print("   [!] Upload failed; keeping the rendered video for review.")
            return

        conn.execute(
            """UPDATE vault SET video_id=?, title_used=?, hook_type=?,
            structure_used=?, persona_used=?, hook_strength=?,
            narrative_completeness=?, audience_fit=?, monetization_risk=?,
            shelf_life=?, composite_score=?, asset_credits_json=?, ai_image_ratio=?, voice_gender=?,
            format_used=?, language_used=?, combo_key=?, hook_style_used=?,
            trend_keyword=?, status=? WHERE rowid=?""",
            (
                vid_id,
                script_data["title"],
                script_data.get("hook_type", ""),
                script_data.get("structure_used", ""),
                script_data.get("persona_used", ""),
                story_payload.get("hook_strength"),
                story_payload.get("narrative_completeness"),
                story_payload.get("audience_fit"),
                story_payload.get("monetization_risk"),
                story_payload.get("shelf_life"),
                story_payload.get("composite_score"),
                json.dumps(script_data.get("visual_provenance") or [], ensure_ascii=False),
                script_data.get("ai_image_ratio", 0.0),
                script_data.get("voice_gender", ""),
                format_mode,
                lang_cfg["key"],
                combo_key,
                script_data.get("hook_style_used", ""),
                trend_keyword,
                "UPLOADED_PRIVATE" if str(pub_mode or "").strip().lower() == "private" else "UPLOADED",
                run_row_id,
            ),
        )
        conn.commit()
        print("\n✅ Database updated.")

    finally:
        conn.close()

if __name__ == "__main__":
    run_robot()