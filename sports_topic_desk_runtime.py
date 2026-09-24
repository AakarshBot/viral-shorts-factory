"""Lean sports topic discovery: cheap retrieval, event clustering and explicit diversity."""

from __future__ import annotations

import html
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import story_ranker as sr
from event_discovery_runtime import cluster_news_events

SPORTS_DESK_VERSION = "sports-desk-v15-2026-09-24"
LOOKBACK_HOURS = 48
MAX_DASHBOARD_HEADLINES = 60
PER_BUCKET = 20
REQUEST_TIMEOUT = 4.0
DISCOVERY_TIMEOUT = 7.0
SECONDARY_TIMEOUT = 2.5
SOURCE_TIMEOUT = REQUEST_TIMEOUT
SECONDARY_REQUEST_TIMEOUT = SECONDARY_TIMEOUT
GOOGLE_RESULT_LIMIT = 25
DIRECT_RESULT_LIMIT = 30
ICC_RSS_URL = "https://www.icc-cricket.com/index?feed=rss2"
SPORTS_RSS_URL = "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-IN&gl=IN&ceid=IN:en"

# Three retrieval profiles are the architecture. They are not three ranking hacks:
# each profile asks for a different kind of story before clustering happens.
DISCOVERY_PROFILES = {
    "news": (
        'India cricket when:2d',
        'India cricket (selection OR injury OR retirement OR appointment OR result OR record) when:3d',
        'India cricket (women OR domestic OR Ranji OR U19 OR milestone OR comeback) when:3d',
    ),
    "emerging": (
        'India cricket (uncapped OR debut OR breakthrough OR milestone OR record OR upset) when:7d',
        'India cricket (unusual OR bizarre OR surprise OR comeback) when:7d',
        'India cricket (women OR domestic OR Ranji OR U19) (young OR emerging OR breakout) when:7d',
    ),
    "social": (
        'India cricket (reaction OR reactions OR statement OR debate OR controversy OR fans) when:3d',
        'India cricket (player OR coach OR former) (said OR comments OR response OR praised OR slammed) when:3d',
        'India cricket (social media OR viral) (reaction OR controversy OR player OR fans) when:3d',
    ),
}
GLOBAL_PROFILE_QUERIES = {
    "news": (
        '(Australia OR England OR South Africa OR New Zealand OR West Indies OR Pakistan OR Sri Lanka OR Bangladesh OR Afghanistan OR Zimbabwe OR Ireland) cricket latest when:3d',
        '(Pakistan OR Sri Lanka OR Bangladesh OR Afghanistan OR Zimbabwe OR Ireland) cricket (selection OR injury OR retirement OR appointment OR result OR record OR sanction OR controversy) when:3d',
        '(women cricket OR women\'s cricket) (Australia OR England OR South Africa OR New Zealand OR West Indies OR Pakistan OR Sri Lanka OR Bangladesh) latest result selection record controversy when:3d',
    ),
    "emerging": (
        'international cricket (uncapped OR emerging OR debut OR breakthrough OR young OR grassroots) (record OR milestone OR upset OR comeback OR selection) when:7d',
        '(Pakistan OR Sri Lanka OR Bangladesh OR Afghanistan OR Zimbabwe OR Ireland OR Nepal OR Netherlands) cricket (upset OR comeback OR record OR debut OR breakthrough OR bizarre) when:7d',
        'international cricket (bizarre OR unusual OR surprise OR sanctions OR investigation OR controversy) player team when:7d',
    ),
    "social": (
        'international cricket (reaction OR reacts OR comments OR statement OR debate OR controversy OR fans) when:3d',
        '(Pakistan OR Australia OR England OR South Africa OR New Zealand OR Sri Lanka OR Bangladesh) cricket (player OR coach OR former) (said OR called OR praised OR slammed OR warned) when:3d',
        'cricket social media reaction fans player controversy statement worldwide when:3d',
    ),
}
NICHE_PROFILE_QUERIES = {
    "news": (
        'India football soccer (ISL OR I-League OR national team) latest result transfer coach record controversy when:3d',
        'India (tennis OR badminton OR table tennis OR squash) latest result record injury retirement selection tournament when:3d',
        'India (hockey OR athletics OR shooting OR archery OR wrestling OR boxing) latest result medal record qualification controversy when:3d',
    ),
    "emerging": (
        'India sport (emerging OR junior OR U23 OR U19 OR academy OR uncapped) (debut OR breakthrough OR record OR upset OR comeback OR milestone) when:7d',
        '(football OR tennis OR badminton OR hockey OR athletics) India (women OR junior OR youth OR academy) (breakthrough OR upset OR record OR qualification) when:7d',
        '(motorsport OR golf OR basketball OR volleyball OR kabaddi OR chess) India (breakthrough OR upset OR record OR debut OR qualification OR title) when:7d',
    ),
    "social": (
        'India sports (reaction OR reacts OR comments OR controversy OR debate OR fans OR statement) when:3d',
        '(football OR tennis OR badminton OR hockey OR athletics) India (player OR coach OR fan) (said OR called OR praised OR slammed OR reacts) when:3d',
        '(boxing OR wrestling OR shooting OR motorsport OR golf OR kabaddi OR chess) India (reaction OR controversy OR fans OR statement OR viral) when:3d',
    ),
}

DIRECT_CRICKET_SOURCES = (
    ("ICC", "https://www.icc-cricket.com/news"),
    ("BCCI", "https://www.bcci.tv/news"),
    ("ESPNcricinfo", "https://www.espncricinfo.com/cricket-news"),
)
GLOBAL_CRICKET_SOURCES = (
    ("ICC", "https://www.icc-cricket.com/news"),
    ("ESPNcricinfo", "https://www.espncricinfo.com/cricket-news"),
    ("Wisden", "https://www.wisden.com/cricket-news"),
)

REACTION_TERMS = (
    "said", "says", "called", "responded", "reaction", "reacts", "comment",
    "comments", "praised", "criticised", "criticized", "slammed", "warned",
    "debate", "backlash", "fans", "meme", "viral", "social media", "post",
)
HOOK_TERMS = (
    "record", "first", "fastest", "youngest", "oldest", "debut", "breakthrough",
    "comeback", "upset", "survived", "scare", "controversy", "investigation",
    "sanctioned", "fined", "retired", "ruled out", "recalled", "dropped",
    "statement", "revealed", "banned", "appointed", "returns", "bizarre",
)
SATURATION_TERMS = ("world cup", "final", "asia cup", "asian games", "ashes", "india vs", "india v", "championship")
CRICKET_PLAYER_ALIASES = (
    "virat kohli", "kohli", "rohit sharma", "jasprit bumrah", "bumrah", "shubman gill",
    "gill", "rishabh pant", "pant", "hardik pandya", "hardik", "ravindra jadeja",
    "jadeja", "suryakumar yadav", "suryakumar", "yashasvi jaiswal", "jaiswal",
    "kl rahul", "sanju samson", "smriti mandhana", "harmanpreet kaur", "rashid khan",
    "babar azam", "shaheen afridi", "pat cummins", "travis head", "ben stokes",
    "joe root", "steve smith", "mohammed siraj", "kane williamson",
)
INDIA_ASIA_PLAYER_ALIASES = (
    "virat kohli", "kohli", "rohit sharma", "jasprit bumrah", "bumrah",
    "shubman gill", "gill", "rishabh pant", "pant", "hardik pandya", "hardik",
    "ravindra jadeja", "jadeja", "suryakumar yadav", "suryakumar",
    "yashasvi jaiswal", "jaiswal", "kl rahul", "sanju samson",
    "smriti mandhana", "harmanpreet kaur", "mohammed siraj",
)
INDIA_ASIA_ANCHORS = (
    "india", "indian", "bcci", "india a", "ranji", "duleep", "wpl", "ipl", "u19", "u-19",
    "u23", "u-23", "pakistan", "pcb", "sri lanka", "bangladesh", "afghanistan", "nepal",
    "uae", "hong kong", "japan", "asia", "asian games", "asia cup", "acc",
)

def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _word_match(text, term):
    text = _clean(text).casefold()
    term = _clean(term).casefold()
    if not text or not term:
        return False
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))


def _age_hours(value):
    raw = _clean(value)
    if not raw:
        return 9999.0
    parsed = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        pass
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            pass
    if parsed is None:
        for fmt in ("%Y%m%d%H%M%S", "%Y%m%dT%H%M%S", "%Y%m%dT%H%M%SZ"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return 9999.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(
        0.0,
        (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0,
    )


def _domain(item):
    try:
        return urlparse(_clean(item.get("url") or item.get("link"))).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _tokens(value):
    return set(sr._tokens(value))


def _is_cricket(item):
    item = item if isinstance(item, dict) else {}
    text = _clean(" ".join(
        str(item.get(k) or "")
        for k in ("title", "text", "description", "summary", "snippet", "trend_query")
    ))
    source_hint = _clean(" ".join(
        str(item.get(k) or "")
        for k in ("source", "source_name", "publisher", "url")
    )).casefold()
    # Generic words such as "coach", "captain", "pace" and "test" are not
    # enough on their own. Either an explicit cricket anchor or an unambiguous
    # player alias is sufficient.
    specific_hits = sum(
        1 for term in (
            "cricket", "icc", "bcci", "pcb", "wpl", "ipl", "psl",
            "t20", "odi", "wicket", "wickets", "innings", "batter",
            "bowler", "batting", "bowling",
        )
        if _word_match(text, term)
    )
    entity_hits = sum(
        1 for term in CRICKET_PLAYER_ALIASES
        if _word_match(text, term)
    )
    if specific_hits or entity_hits:
        return True
    if any(marker in source_hint for marker in (
        "icc-cricket.com", "bcci.tv", "cricbuzz", "wisden", "espncricinfo", "cricinfo.com",
    )):
        return True
    if bool(item.get("social_post")) and any(marker in source_hint for marker in (
        "r/cricket", "r/indiacricket", "cricketshitpost", "bsky",
    )):
        return True
    return False


def _scope_pass(item, scope):
    if _clean(scope).casefold() != "india / asia":
        return True
    text = _clean(" ".join(
        str(item.get(k) or "")
        for k in ("title", "text", "description", "summary", "snippet", "event_search_text")
    ))
    entity_anchors = tuple(dict.fromkeys(
        (*INDIA_ASIA_ANCHORS, *INDIA_ASIA_PLAYER_ALIASES),
    ))
    anchors = [term for term in entity_anchors if _word_match(text, term)]
    if anchors:
        return True
    entities = {
        str(entity).strip().casefold()
        for entity in (item.get("event_entities") or [])
        if str(entity).strip()
    }
    entity_scope_anchors = {
        "india", "indian", "bcci", "india a", "ranji", "duleep", "wpl", "ipl",
        "pakistan", "pcb", "sri lanka", "bangladesh", "afghanistan",
        "nepal", "uae", "hong kong", "japan", "asian games", "asia cup", "acc",
        *{
            str(alias).strip().casefold()
            for alias in INDIA_ASIA_PLAYER_ALIASES
            if str(alias).strip()
        },
    }
    return bool(entities & entity_scope_anchors)


def _source_local_date(text, now=None):
    value = html.unescape(_clean(text))
    current = now or datetime.now(timezone.utc)
    candidates = []

    for raw in re.findall(
        r"""(?:datetime|datePublished|dateModified|data-date|data-published|data-published-at)\s*[:=]\s*["']([^"']+)["']""",
        value,
        re.IGNORECASE,
    ):
        age = _age_hours(raw)
        if age != 9999.0:
            candidates.append(age)

    for raw in re.findall(r"\b20\d{2}-\d{2}-\d{2}(?:T[0-9:.+\-Z]+)?\b", value):
        age = _age_hours(raw)
        if age != 9999.0:
            candidates.append(age)

    month_names = (
        "Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|"
        "Jul|July|Aug|August|Sep|September|Oct|October|Nov|November|Dec|December"
    )
    patterns = (
        rf"\b(?:{month_names})\s+\d{{1,2}}(?:,\s*|\s+)\d{{4}}\b",
        rf"\b\d{{1,2}}\s+(?:{month_names})\s+\d{{4}}\b",
        rf"\b(?:{month_names})\s+\d{{1,2}}\b",
    )
    for pattern in patterns:
        for raw in re.findall(pattern, value, re.IGNORECASE):
            clean = raw.replace(",", "").strip()
            parsed = None
            for fmt in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
                try:
                    parsed = datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if parsed is None:
                for fmt in ("%b %d", "%B %d"):
                    try:
                        parsed = datetime.strptime(clean, fmt).replace(year=current.year, tzinfo=timezone.utc)
                        if parsed > current:
                            parsed = parsed.replace(year=current.year - 1)
                        break
                    except ValueError:
                        continue
            if parsed is not None:
                candidates.append(max(0.0, (current - parsed).total_seconds() / 3600.0))

    relative = re.search(
        r"(?<!\d)(\d{1,3})\s*(minute|minutes|min|hour|hours|hr|hrs|day|days|d|h)\s+ago\b",
        value.casefold(),
    )
    if relative:
        amount = float(relative.group(1))
        unit = relative.group(2)
        candidates.append(amount / 60.0 if unit.startswith("min") else amount if unit.startswith("h") else amount * 24.0)

    valid = [age for age in candidates if age <= LOOKBACK_HOURS]
    return min(valid) if valid else None


def _card_container(anchor):
    for parent in anchor.parents:
        classes = " ".join(parent.get("class") or []).casefold()
        role = str(parent.get("role") or "").casefold()
        tag = str(parent.name or "").casefold()
        if tag in {"article", "li"}:
            return parent
        if any(marker in classes or marker in role for marker in (
            "card", "story", "article", "news-item", "listing", "content-item", "media-object",
        )):
            return parent
        if parent is anchor.parent and tag in {"div", "section"}:
            return parent
    return anchor.parent


def _direct_listing_source(name, url):
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 ViralShortsFactory/2026 cricket-desk",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=SOURCE_TIMEOUT,
        )
        if response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text or "", "html.parser")
    except Exception:
        return []

    allowed_hosts = {
        "ICC": ("icc-cricket.com",),
        "BCCI": ("bcci.tv",),
        "Cricbuzz": ("cricbuzz.com",),
        "Wisden": ("wisden.com",),
        "ESPNcricinfo": ("espncricinfo.com", "cricinfo.com"),
    }.get(name, ())

    out, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        href = _clean(anchor.get("href"))
        title = _clean(anchor.get_text(" ", strip=True))
        if not href or len(title) < 12:
            continue
        if not href.startswith(("http://", "https://")):
            href = urljoin(url, href)
        parsed = urlparse(href)
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.casefold()
        if allowed_hosts and not any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts):
            continue
        if name == "BCCI" and "/news/article/" not in path:
            continue
        if name == "Cricbuzz" and "/cricket-news/" not in path:
            continue
        if name == "Wisden" and "/cricket-news/" not in path:
            continue
        if name == "ESPNcricinfo" and not any(marker in path for marker in ("/story/", "/cricket-news/")):
            continue
        if name == "ICC" and "/news/" not in path:
            continue

        card = _card_container(anchor)
        card_text = _clean(card.get_text(" ", strip=True) if card else "")
        age = _source_local_date(str(card) if card else card_text)
        if age is None:
            age = _source_local_date(str(anchor.parent) if anchor.parent else "")
        if age is None or age > LOOKBACK_HOURS:
            continue

        key = sr._canonical_url(href)
        if not key or key in seen:
            continue
        seen.add(key)
        published = datetime.now(timezone.utc) - timedelta(hours=age)

        # Keep only card-local context; never use a giant neighboring-page window.
        out.append({
            "title": title[:220],
            "text": card_text[:1200] or title,
            "description": card_text[:1200] or title,
            "source": name,
            "source_name": name,
            "publisher": name,
            "url": href,
            "publishedAt": published.isoformat(),
            "collection_source": "official" if name in {"ICC", "BCCI"} else "specialist_direct",
            "direct_source": name,
        })
        if len(out) >= DIRECT_RESULT_LIMIT:
            break
    return out


def _bluesky(query):
    try:
        r = requests.get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
            params={"q": query, "limit": 40, "sort": "latest"},
            headers={"User-Agent": "ViralShortsFactory/2026 cricket-desk"},
            timeout=SECONDARY_REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        posts = r.json().get("posts") or []
    except Exception:
        return []
    output = []
    for post in posts:
        record = post.get("record") or {}
        author = post.get("author") or {}
        text = _clean(record.get("text"))
        if not text:
            continue
        handle = _clean(author.get("handle"))
        uri = _clean(post.get("uri"))
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else ""
        output.append({
            "title": text[:220],
            "text": text,
            "description": text,
            "source": f"Bluesky @{handle}" if handle else "Bluesky",
            "source_name": f"Bluesky @{handle}" if handle else "Bluesky",
            "url": url,
            "publishedAt": record.get("createdAt") or post.get("indexedAt") or "",
            "collection_source": "bluesky",
            "discovery_profile": "social",
            "social_post": True,
            "social_like": float(post.get("likeCount") or 0),
            "social_reply": float(post.get("replyCount") or 0),
            "social_repost": float(post.get("repostCount") or 0),
            "social_quote": float(post.get("quoteCount") or 0),
        })
    return output


def _profile_queries(scope):
    key = _clean(scope).casefold()
    if key == "niche sports":
        return NICHE_PROFILE_QUERIES
    if key == "global":
        return GLOBAL_PROFILE_QUERIES
    return DISCOVERY_PROFILES


def _google_search(query, profile, scope):
    key = _clean(scope).casefold()
    hl, gl, ceid = (("en-IN", "IN", "IN:en") if key != "global" else ("en-GB", "GB", "GB:en"))
    try:
        rows = sr._google_news_search_items(
            query,
            "sports" if key == "niche sports" else "sports_stories_of_day",
            GOOGLE_RESULT_LIMIT,
            timeout=REQUEST_TIMEOUT,
            hl=hl,
            gl=gl,
            ceid=ceid,
        ) or []
    except Exception:
        return []
    for row in rows:
        if isinstance(row, dict):
            row["discovery_profile"] = profile
            row["discovery_query"] = query
    return rows


def _reddit_search(subreddit, query):
    try:
        response = requests.get(
            "https://www.reddit.com/search.json",
            params={"q": query, "restrict_sr": "on", "subreddit": subreddit, "sort": "new", "t": "week", "limit": 30},
            headers={"User-Agent": "ViralShortsFactory/2026 topic-discovery"},
            timeout=SECONDARY_TIMEOUT,
        )
        if response.status_code != 200:
            return []
        children = ((response.json() or {}).get("data") or {}).get("children") or []
    except Exception:
        return []
    output = []
    for child in children:
        data = child.get("data") or {}
        title = _clean(data.get("title"))
        if not title:
            continue
        output.append({
            "title": title[:220],
            "text": _clean(data.get("selftext"))[:1200] or title,
            "description": _clean(data.get("selftext"))[:1200] or title,
            "source": f"Reddit r/{subreddit}",
            "source_name": f"Reddit r/{subreddit}",
            "url": f"https://www.reddit.com{data.get('permalink', '')}",
            "publishedAt": datetime.fromtimestamp(float(data.get("created_utc") or 0), tz=timezone.utc).isoformat(),
            "collection_source": "reddit",
            "social_post": True,
            "social_like": float(data.get("ups") or 0),
            "social_reply": float(data.get("num_comments") or 0),
            "discovery_profile": "social",
            "discovery_query": query,
        })
    return output


def _is_non_cricket_sports(item):
    item = item if isinstance(item, dict) else {}
    text = _clean(" ".join(str(item.get(key) or "") for key in ("title", "text", "description", "summary", "snippet", "trend_query", "event_search_text", "event_entities"))).casefold()
    terms = (
        "football", "soccer", "tennis", "badminton", "hockey", "athletics", "basketball",
        "volleyball", "golf", "rugby", "motorsport", "formula 1", "f1", "motogp",
        "wrestling", "boxing", "mma", "kabaddi", "table tennis", "squash", "archery",
        "shooting", "swimming", "aquatics", "cycling", "gymnastics", "weightlifting",
        "olympics", "olympic", "chess", "judo", "karate", "taekwondo", "fencing",
        "equestrian", "rowing", "canoe", "triathlon", "skateboarding", "sport climbing",
        "powerlifting", "esports", "e-sports",
    )
    return any(_word_match(text, term) for term in terms)


def _normalise_rows(rows, scope="India / Asia"):
    scope_key = _clean(scope).casefold()
    niche_scope = scope_key == "niche sports"
    output, seen = [], set()
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        if niche_scope:
            if not _is_non_cricket_sports(raw):
                continue
        elif not _is_cricket(raw):
            query_context = _clean(raw.get("discovery_query")).casefold()
            is_cricket_query = (
                _clean(raw.get("collection_source")).casefold() == "google_news_rss"
                and _word_match(query_context, "cricket")
            )
            if not is_cricket_query:
                continue
        item = dict(raw)
        age = _age_hours(item.get("publishedAt") or item.get("published_at") or item.get("created_at"))
        if age == 9999.0 or age > LOOKBACK_HOURS:
            continue
        safe, _ = sr._safety_gate(item)
        if not safe:
            continue
        if not item.get("social_post"):
            if not sr._source_page_pass(item):
                continue
            if niche_scope:
                if not sr._discovery_source_pass(item):
                    continue
            elif not sr._cricket_service_title_pass(item):
                continue
        url = _clean(item.get("url") or item.get("link"))
        canonical = sr._canonical_url(url)
        title_key = " ".join(sorted(_tokens(item.get("title"))))
        key = canonical or ("title:" + title_key)
        if key in seen:
            continue
        seen.add(key)
        item["title"] = _clean(item.get("title"))
        item["source_domain"] = _domain(item)
        item["age_hours"] = round(age, 2)
        item["social_engagement"] = math.log1p(
            sum(float(item.get(k) or 0) for k in (
                "social_like", "social_reply", "social_repost", "social_quote"
            ))
        )
        output.append(item)
    return output


def _social_stats_by_url(rows):
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("social_post"):
            continue
        url = sr._canonical_url(row.get("url") or row.get("link"))
        if url:
            result[url] = result.get(url, 0.0) + float(row.get("social_engagement") or 0.0)
    return result


def _collect(scope="India / Asia"):
    profiles = _profile_queries(scope)
    key = _clean(scope).casefold()
    jobs = []

    # Exactly one small request set per editorial profile. Search engines provide
    # breadth; curated feeds provide reliable specialist coverage; social sources
    # provide reaction-led leads. Nothing else is required for normal discovery.
    for profile, queries in profiles.items():
        for query in queries:
            jobs.append((f"Google:{profile}", _google_search, (query, profile, scope), {}))

    if key == "niche sports":
        jobs.append(("Sports RSS", sr._rss_items, (SPORTS_RSS_URL, "sports", "rss", 40), {"timeout": REQUEST_TIMEOUT}))
    elif key == "global":
        jobs.append(("ICC RSS", sr._rss_items, (ICC_RSS_URL, "sports_stories_of_day", "official", 35), {"timeout": REQUEST_TIMEOUT}))
        for name, url in GLOBAL_CRICKET_SOURCES:
            jobs.append((name, _direct_listing_source, (name, url), {}))
    else:
        jobs.append(("ICC RSS", sr._rss_items, (ICC_RSS_URL, "sports_stories_of_day", "official", 35), {"timeout": REQUEST_TIMEOUT}))
        for name, url in DIRECT_CRICKET_SOURCES:
            jobs.append((name, _direct_listing_source, (name, url), {}))

    if key == "india / asia":
        jobs.extend([
            ("Reddit r/Cricket", _reddit_search, ("Cricket", "India cricket"), {}),
            ("Reddit r/IndiaCricket", _reddit_search, ("IndiaCricket", "India cricket"), {}),
            ("Bluesky", _bluesky, ('"India cricket"',), {}),
        ])
    elif key == "global":
        jobs.append(("Bluesky", _bluesky, ("cricket",), {}))

    trend_geo = "IN" if key != "global" else "GB"
    jobs.append(("Google Trends", sr._google_trends_items, (trend_geo, 20), {"timeout": SECONDARY_TIMEOUT}))

    rows = []
    counts = {}
    failures = []
    pool = ThreadPoolExecutor(max_workers=min(16, max(1, len(jobs))), thread_name_prefix="sports-discovery")
    future_map = {pool.submit(fn, *args, **kwargs): label for label, fn, args, kwargs in jobs}
    try:
        for future in as_completed(future_map, timeout=DISCOVERY_TIMEOUT):
            label = future_map[future]
            try:
                values = future.result() or []
                rows.extend(values)
                counts[label] = counts.get(label, 0) + len(values)
            except Exception as exc:
                failures.append(f"{label}:{type(exc).__name__}")
    except TimeoutError:
        for future, label in future_map.items():
            if not future.done():
                failures.append(f"{label}:timeout")
                future.cancel()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    print(
        f"   [Sports Desk] Raw intake: {', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or 'none'}",
        flush=True,
    )
    if failures:
        print(f"   [Sports Desk] Source issues: {', '.join(failures[:12])}", flush=True)
    return rows


def _enrich_events(events, rows):
    social_by_url = _social_stats_by_url(rows)
    profile_by_url = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = sr._canonical_url(row.get("url") or row.get("link"))
        profile = _clean(row.get("discovery_profile")).casefold()
        if url and profile:
            profile_by_url.setdefault(url, set()).add(profile)
    for event in events:
        evidence = event.get("event_evidence") or []
        profiles = []
        social_count = 0
        social_engagement = 0.0
        for item in evidence:
            url = sr._canonical_url(item.get("url") or item.get("link"))
            row_profiles = profile_by_url.get(url) or {
                _clean(item.get("discovery_profile")).casefold()
            }
            for profile in sorted(profile for profile in row_profiles if profile):
                if profile not in profiles:
                    profiles.append(profile)
            collection = _clean(item.get("collection_source")).casefold()
            if collection in {"bluesky", "reddit", "mastodon", "social"}:
                if "social" not in profiles:
                    profiles.append("social")
                social_count += 1
                social_engagement += social_by_url.get(url, 0.0)
        event["discovery_profiles"] = profiles or ["news"]
        event["social_post_count"] = social_count
        event["social_engagement_total"] = round(social_engagement, 3)
        event["event_article_count"] = max(0, int(event.get("event_article_count") or 0) - social_count)
        event["event_identity_key"] = str(event.get("event_identity_key") or event.get("event_id") or "").strip()
    return events


def _score(item, trends, history_titles=None, retained=None, scope="India / Asia"):
    text = _clean(" ".join(str(item.get(k) or "") for k in ("title", "text", "description", "event_search_text"))).casefold()
    reaction_hits = sum(1 for term in REACTION_TERMS if _word_match(text, term))
    hook_hits = sum(1 for term in HOOK_TERMS if _word_match(text, term))
    source_count = int(item.get("event_source_count") or 0)
    article_count = int(item.get("event_article_count") or 0)
    coverage = min(10.0, source_count * 2.0 + min(4.0, article_count * 0.4))
    social = min(10.0, float(item.get("social_engagement_total") or 0) * 1.2 + min(5.0, int(item.get("social_post_count") or 0) * 0.8))
    trend = 0.0
    title_tokens = _tokens(item.get("title"))
    for trend_row in trends or []:
        trend_tokens = _tokens(f"{trend_row.get('trend_query', '')} {trend_row.get('title', '')}")
        overlap = len(title_tokens & trend_tokens) / max(1, len(title_tokens | trend_tokens)) if title_tokens and trend_tokens else 0
        if overlap >= 0.30:
            trend = max(trend, float(trend_row.get("trend_bonus") or 0))
    undercovered = max(0.0, min(10.0, 10.0 - coverage + social * 0.5 + (2.5 if source_count <= 2 else 0)))
    age = float(item.get("age_hours") or _age_hours(item))
    freshness = max(0.0, min(10.0, 10.0 - age / 7.2))
    saturation = min(8.0, sum(1 for term in SATURATION_TERMS if _word_match(text, term)))
    history_penalty = 0.0
    current = _clean(item.get("title")).casefold()
    history = list(history_titles or []) + [_clean(x.get("title")) for x in (retained or []) if isinstance(x, dict)]
    if current and history:
        history_penalty = min(6.0, max((SequenceMatcher(None, current, value.casefold()).ratio() for value in history if value), default=0.0) * 6.0)
    news = freshness * 0.55 + coverage * 1.25 + hook_hits * 0.45 + reaction_hits * 0.15 - saturation * 0.5 - history_penalty
    viral = trend * 1.8 + social * 1.3 + undercovered * 1.5 + hook_hits * 0.8 + freshness * 0.5 - saturation * 0.7 - history_penalty
    social_score = social * 1.8 + reaction_hits * 1.3 + trend * 0.8 + undercovered * 1.1 + freshness * 0.4 - history_penalty
    item.update({
        "coverage_score": round(coverage, 2), "undercovered_score": round(undercovered, 2),
        "trend_signal_score": round(trend, 2), "social_signal_score": round(social, 2),
        "viral_signal_score": round(max(0.0, viral / 2.0), 2), "news_score": round(news, 2),
        "viral_score": round(viral, 2), "social_score": round(social_score, 2),
    })
    return item


def _diversify_events(events, limit=60, scope="India / Asia"):
    candidates = [x for x in events if isinstance(x, dict)]
    selected = []
    used = set()
    quotas = {"news": min(20, max(1, limit // 3)), "viral": min(20, max(1, limit // 3)), "social": min(20, max(1, limit - 2 * (limit // 3)))}
    preferences = {"news": ("news", "emerging", "social"), "viral": ("emerging", "social", "news"), "social": ("social", "emerging", "news")}

    def score(candidate, bucket):
        profiles = set(candidate.get("discovery_profiles") or [])
        profile_bonus = 12.0 if preferences[bucket][0] in profiles else 5.0 if preferences[bucket][1] in profiles else 0.0
        base = float(candidate.get(f"{bucket}_score") or 0.0)
        diversity_penalty = max((sr._story_theme_similarity(candidate, old) for old in selected), default=0.0) * 10.0
        return base + profile_bonus - diversity_penalty

    def bucket_eligible(item, bucket):
        profiles = {
            _clean(value).casefold()
            for value in (item.get("discovery_profiles") or [])
            if _clean(value)
        }

        # Editorial lanes are intentionally mutually exclusive. A broad
        # one-source story often has a high undercovered score; that is useful
        # for ranking novelty, but it must not turn every ordinary news item
        # into VIRAL. Likewise, a Google social-lane hit is a SOCIAL lead even
        # though it is not a Reddit/Bluesky post.
        social_first = "social" in profiles and not profiles.intersection({"news", "emerging"})
        emerging_first = "emerging" in profiles and not profiles.intersection({"news", "social"})

        if bucket == "social":
            return social_first

        if bucket == "viral":
            return emerging_first or (
                not profiles.intersection({"news", "emerging", "social"})
                and float(item.get("trend_signal_score") or 0.0) > 0.0
            )

        return not social_first and not emerging_first

    # Fill each editorial lane only from candidates that actually belong there.
    for bucket in ("news", "viral", "social"):
        for _ in range(quotas[bucket]):
            available = [x for x in candidates if id(x) not in used]
            if not available:
                break
            eligible = [x for x in available if bucket_eligible(x, bucket)]
            if not eligible:
                break
            best = max(eligible, key=lambda x: score(x, bucket))
            used.add(id(best))
            row = dict(best)
            row["discovery_bucket"] = bucket
            row["bucket_score"] = float(row.get(f"{bucket}_score") or 0)
            row["discovery_profiles"] = list(row.get("discovery_profiles") or ["news"])
            selected.append(row)

    # A sparse specialized lane must not hide otherwise valid current stories.
    # Remaining candidates that genuinely belong to NEWS fill the unused pool;
    # they are not re-labeled as NEWS when they are clearly emerging/social-first.
    if len(selected) < min(limit, len(candidates)):
        remaining = [
            x for x in candidates
            if id(x) not in used and bucket_eligible(x, "news")
        ]
        remaining.sort(key=lambda x: score(x, "news"), reverse=True)
        for candidate in remaining:
            row = dict(candidate)
            row["discovery_bucket"] = "news"
            row["bucket_score"] = float(row.get("news_score") or 0)
            row["discovery_profiles"] = list(row.get("discovery_profiles") or ["news"])
            selected.append(row)
            used.add(id(candidate))
            if len(selected) >= limit:
                break
    return selected[:limit]

def discover_sports_topics(bot, conn=None, scope="India / Asia", requested_topic="", max_candidates=60, retained_candidates=None):
    raw = _collect(scope)
    all_rows = _normalise_rows(raw, scope=scope)
    trend_rows = [row for row in all_rows if _clean(row.get("collection_source")).casefold() == "google_trends"]
    rows = [row for row in all_rows if _clean(row.get("collection_source")).casefold() != "google_trends" and _scope_pass(row, scope)]
    events = cluster_news_events(rows)
    events = _enrich_events(events, rows)
    for event in events:
        title_tokens = _tokens(event.get("title"))
        best = 0.0
        for trend in trend_rows:
            trend_tokens = _tokens(f"{trend.get('trend_query', '')} {trend.get('title', '')}")
            overlap = len(title_tokens & trend_tokens) / max(1, len(title_tokens | trend_tokens)) if title_tokens and trend_tokens else 0
            if overlap >= 0.30:
                best = max(best, float(trend.get("trend_bonus") or 0.0))
        event["trend_signal_score"] = round(min(6.0, best * 1.25), 2)

    uploaded = sr._load_uploaded_story_identities(conn)
    events = [event for event in events if not sr._uploaded_story_match(event, uploaded)]
    niche_scope = _clean(scope).casefold() == "niche sports"
    history_titles = []
    if conn is not None:
        try:
            rows = conn.execute("SELECT topic, title_used FROM vault WHERE status IN ('UPLOADED','UPLOADED_PRIVATE','COMPLETED') AND video_id IS NOT NULL AND TRIM(video_id) <> '' ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT 500").fetchall()
            history_titles = [_clean(value) for row in rows for value in row[:2] if _clean(value)]
        except Exception:
            history_titles = []

    scored = []
    for event in events:
        if requested_topic and not sr._requested_topic_pass(event, requested_topic):
            continue
        event["recommended_category"] = "sports" if niche_scope else "sports_stories_of_day"
        event["primary_genre"] = "sports" if niche_scope else "cricket"
        scored.append(_score(event, trend_rows, history_titles, retained_candidates or [], scope))

    output = []
    for item in _diversify_events(scored, limit=min(MAX_DASHBOARD_HEADLINES, int(max_candidates or MAX_DASHBOARD_HEADLINES)), scope=scope):
        row = dict(item)
        row["dashboard_discovery_version"] = SPORTS_DESK_VERSION
        row["story_key"] = sr._story_key(row)
        row["story_url"] = row.get("url") or sr._story_url(row)
        row["source_label"] = sr._source_label(row) or _clean(row.get("source")) or ("Sports source" if niche_scope else "Cricket source")
        row["recommended_category"] = "sports" if niche_scope else "sports_stories_of_day"
        row["recommended_format"] = "regular" if niche_scope else "cricket"
        row["cricket_pipeline"] = not niche_scope
        row["primary_genre"] = "sports" if niche_scope else "cricket"
        row["verification_level"] = "social lead" if int(row.get("social_post_count") or 0) and int(row.get("event_article_count") or 0) == 0 else "corroborated" if int(row.get("event_source_count") or 0) >= 2 else "single-source lead"
        profiles = ", ".join(str(x).replace("_", " ").title() for x in row.get("discovery_profiles") or [])
        row["discovery_reason"] = f"{profiles or 'News'} discovery · {int(row.get('event_source_count') or 0)} publisher(s) · {int(row.get('social_post_count') or 0)} social signal(s) · {float(row.get('age_hours') or 0):.1f}h old"
        output.append(row)
    bucket_counts = {bucket: sum(1 for item in output if item.get("discovery_bucket") == bucket) for bucket in ("news", "viral", "social")}
    print(
        "   [Sports Desk] Buckets: "
        + ", ".join(f"{bucket.upper()}={count}" for bucket, count in bucket_counts.items()),
        flush=True,
    )
    return output


def render_sports_topic_desk(candidates, ui_text, ui_html, remember_callback, scope="India / Asia"):
    import streamlit as st
    niche_scope = _clean(scope).casefold() == "niche sports"
    desk_label = "Sports" if niche_scope else "Cricket"
    source_fallback = "Sports source" if niche_scope else "Cricket source"
    button_prefix = "sports_desk" if niche_scope else "cricket_desk"
    specs = (
        ("news", "NEWS", "Current developments and confirmed reporting."),
        ("viral", "VIRAL / EMERGING", "Less-covered stories, unusual developments and breakout moments."),
        ("social", "SOCIAL / REACTIONS", "Player comments, fan reactions, debate and social-first leads."),
    )
    grouped = {key: [] for key, _, _ in specs}
    for candidate in candidates or []:
        bucket = str(candidate.get("discovery_bucket") or "news").lower()
        grouped.get(bucket, grouped["news"]).append(candidate)

    st.markdown(
        f"<div class='live-bar'><div class='live-bar-copy'><b>{desk_label} story desk</b> · {len(candidates or [])} distinct event ideas</div></div>",
        unsafe_allow_html=True,
    )
    tabs = st.tabs([f"{label} · {len(grouped[key])}" for key, label, _ in specs])
    for tab, (bucket, label, description) in zip(tabs, specs):
        with tab:
            st.caption(description)
            for start in range(0, len(grouped[bucket]), 2):
                row = grouped[bucket][start:start + 2]
                cols = st.columns(len(row), gap="medium")
                for offset, candidate in enumerate(row):
                    index = start + offset
                    with cols[offset]:
                        title = ui_text(candidate.get("title"), "Untitled story")
                        source = ui_text(candidate.get("source_label"), source_fallback)
                        reason = ui_text(candidate.get("discovery_reason"))
                        url = str(candidate.get("story_url") or "").strip()
                        profiles = ", ".join(str(x).replace("_", " ").title() for x in candidate.get("discovery_profiles") or [])
                        chips = f"<span class='topic-chip strong'>{ui_html(profiles or 'News')}</span><span class='topic-chip'>Novel {float(candidate.get('undercovered_score') or 0):.1f}</span><span class='topic-chip'>Fresh {float(candidate.get('age_hours') or 0):.1f}h</span>"
                        if int(candidate.get("social_post_count") or 0):
                            chips += f"<span class='topic-chip'>{int(candidate.get('social_post_count') or 0)} social</span>"
                        if int(candidate.get("event_source_count") or 0):
                            chips += f"<span class='topic-chip'>{int(candidate.get('event_source_count') or 0)} publishers</span>"
                        st.markdown(
                            "<div class='topic-card'>"
                            f"<div class='topic-kicker'><span>{ui_html(label)} · {index + 1:02d}</span><span>{ui_html(candidate.get('verification_level', 'lead').upper())}</span></div>"
                            f"<div class='topic-title'>{ui_html(title)}</div><div class='topic-subtitle'>{ui_html(source)}</div><div class='topic-chips'>{chips}</div></div>",
                            unsafe_allow_html=True,
                        )
                        if reason:
                            st.caption(reason)
                        actions = st.columns(2)
                        with actions[0]:
                            if url.startswith(("http://", "https://")):
                                st.link_button("Open", url, width="stretch")
                        with actions[1]:
                            if st.button("Use story", type="primary", width="stretch", key=f"{button_prefix}_{bucket}_{index}_{candidate.get('story_key', index)}"):
                                remember_callback(candidate)
                                st.session_state.pending_candidate = dict(candidate)
                                st.session_state.visual_search_queries = ""
                                st.session_state.visual_query_story_key = ""
                                st.session_state.visual_query_suggestions = []
                                st.session_state.visual_query_field_count = 0
                                st.rerun()