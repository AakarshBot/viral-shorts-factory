"""Segmented Auto-Pilot selection for the Viral Shorts Factory.

The selector evaluates complete format/category/language combinations instead of
choosing each dimension from one global average. It uses hierarchical evidence:
exact combination -> category/genre -> format/language -> global, with shrinkage
for small samples and controlled exploration for under-tested combinations.
"""
import math
import random
import re
import sqlite3

PRIOR_STRENGTH = 8.0
EXPLORATION_RATE = 0.14


def _clean(value):
    return str(value or "").strip().lower()


def _safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _parse_combo(combo):
    parts = [_clean(x) for x in str(combo or "").split("|")]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    return None, None, None


def _eligible_row(row):
    status = _clean(row.get("status"))
    video_id = _clean(row.get("video_id"))
    if status in {"pending_qc", "rejected", "failed"}:
        return False
    if not video_id or video_id in {"pending_qc", "rejected", "failed"}:
        return False
    return _safe_float(row.get("avg_view_percentage")) is not None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _shrunk_mean(values, prior, strength=PRIOR_STRENGTH):
    values = [v for v in values if v is not None]
    if not values:
        return prior, 0
    n = len(values)
    return ((sum(values) + prior * strength) / (n + strength)), n


def _extract_rows(conn):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT status, video_id, avg_view_percentage, genre, format_used,
                  language_used, combo_key
           FROM vault
           WHERE avg_view_percentage IS NOT NULL"""
    ).fetchall()
    return [dict(r) for r in rows]


def _category_from_row(row):
    _, combo_category, _ = _parse_combo(row.get("combo_key"))
    return combo_category or _clean(row.get("genre"))


def _family(category, genre):
    """Create a broad segment key when historical data has sparse exact labels."""
    text = f"{category} {genre}".lower()
    groups = {
        "sports": ("sport", "cricket", "football", "tennis", "ipl", "icc"),
        "tech": ("tech", "ai", "gadget", "technology"),
        "finance": ("business", "finance", "economy", "market", "stock"),
        "entertainment": ("entertainment", "movie", "bollywood", "tollywood"),
        "news": ("affair", "news", "global", "national", "politic"),
        "health": ("health", "fitness", "wellness", "nutrition"),
        "regional": ("telangana", "hyderabad", "andhra", "regional"),
        "viral": ("viral", "internet", "trend"),
    }
    for name, terms in groups.items():
        if any(term in text for term in terms):
            return name
    return _clean(category) or _clean(genre) or "general"


def _build_model(rows):
    eligible = [r for r in rows if _eligible_row(r)]
    global_values = [_safe_float(r.get("avg_view_percentage")) for r in eligible]
    global_prior = _mean(global_values) or 50.0

    model = {
        "rows": eligible,
        "global": _shrunk_mean(global_values, global_prior),
        "format": {},
        "language": {},
        "category": {},
        "family": {},
        "combo": {},
        "category_format": {},
        "category_language": {},
        "family_format": {},
    }

    for r in eligible:
        fmt, combo_cat, lang = _parse_combo(r.get("combo_key"))
        fmt = _clean(r.get("format_used")) or fmt
        lang = _clean(r.get("language_used")) or lang
        cat = combo_cat or _category_from_row(r)
        fam = _family(cat, r.get("genre"))
        value = _safe_float(r.get("avg_view_percentage"))
        if value is None:
            continue
        keys = [
            ("format", fmt),
            ("language", lang),
            ("category", cat),
            ("family", fam),
            ("combo", f"{fmt}|{cat}|{lang}" if fmt and cat and lang else ""),
            ("category_format", f"{cat}|{fmt}" if cat and fmt else ""),
            ("category_language", f"{cat}|{lang}" if cat and lang else ""),
            ("family_format", f"{fam}|{fmt}" if fam and fmt else ""),
        ]
        for bucket, key in keys:
            if key:
                model[bucket].setdefault(key, []).append(value)

    for bucket in ("format", "language", "category", "family", "combo",
                   "category_format", "category_language", "family_format"):
        for key, values in model[bucket].items():
            model[bucket][key] = _shrunk_mean(values, global_prior)
    return model


def _evidence(model, bucket, key):
    if not key:
        return None, 0
    item = model[bucket].get(key)
    return item if item else (None, 0)


def _candidate_score(model, fmt, category, language):
    combo = f"{_clean(fmt)}|{_clean(category)}|{_clean(language)}"
    cat = _clean(category)
    lang = _clean(language)
    fmt = _clean(fmt)
    fam = _family(category, category)

    global_score, _ = model["global"]
    combo_score, combo_n = _evidence(model, "combo", combo)
    cf_score, cf_n = _evidence(model, "category_format", f"{cat}|{fmt}")
    cl_score, cl_n = _evidence(model, "category_language", f"{cat}|{lang}")
    cat_score, cat_n = _evidence(model, "category", cat)
    fam_score, fam_n = _evidence(model, "family", fam)
    ff_score, ff_n = _evidence(model, "family_format", f"{fam}|{fmt}")
    fmt_score, fmt_n = _evidence(model, "format", fmt)
    lang_score, lang_n = _evidence(model, "language", lang)

    score = global_score
    weight = 1.0
    layers = [
        (fam_score, fam_n, 0.35),
        (cat_score, cat_n, 0.70),
        (ff_score, ff_n, 0.55),
        (cf_score, cf_n, 0.80),
        (cl_score, cl_n, 0.65),
        (fmt_score, fmt_n, 0.20),
        (lang_score, lang_n, 0.15),
        (combo_score, combo_n, 1.30),
    ]
    for value, count, layer_weight in layers:
        if value is None or count <= 0:
            continue
        confidence = min(1.0, math.sqrt(count / 8.0))
        w = layer_weight * confidence
        score = (score * weight + value * w) / (weight + w)
        weight += w

    evidence_n = combo_n + cf_n + cl_n + cat_n
    uncertainty = 1.8 / math.sqrt(max(1, evidence_n))
    return score + uncertainty, {
        "score": round(score, 2),
        "selection_score": round(score + uncertainty, 2),
        "combo_n": combo_n,
        "category_n": cat_n,
        "category_format_n": cf_n,
        "category_language_n": cl_n,
        "family_n": fam_n,
        "format_n": fmt_n,
        "language_n": lang_n,
    }


def select_auto_pilot(bot, conn):
    """Return format, category, language config and combo key."""
    model = _build_model(_extract_rows(conn))
    categories = {
        key: value for key, value in bot.CONTENT_CATEGORIES.items()
        if key != "tech_reviews"
    }
    formats = {
        "regular": lambda c: bool(categories[c].get("usable_regular")),
        "top5": lambda c: bool(categories[c].get("usable_top5")),
        "trending": lambda c: bool(categories[c].get("usable_regular")),
    }
    languages = list(bot.LANGUAGES.keys())

    candidates = []
    for fmt, allowed in formats.items():
        for category in categories:
            if not allowed(category):
                continue
            for language in languages:
                selection_score, details = _candidate_score(model, fmt, category, language)
                candidates.append((selection_score, fmt, category, language, details))

    if not candidates:
        return "regular", "national_global_affairs", bot.LANGUAGES["english"], "regular|national_global_affairs|english"

    candidates.sort(reverse=True, key=lambda x: x[0])
    under_tested = [c for c in candidates if c[4]["combo_n"] < 5]
    if under_tested and random.random() < EXPLORATION_RATE:
        chosen = random.choice(under_tested[:max(3, min(12, len(under_tested)))])
        mode = "exploration"
    else:
        chosen = candidates[0]
        mode = "exploitation"

    _, fmt, category, language, details = chosen
    print(
        "   [Auto-Pilot] %s | format=%s | category=%s | language=%s | "
        "score=%.2f | combo_n=%d | category_n=%d | family_n=%d"
        % (mode, fmt, category, language, details["score"], details["combo_n"],
           details["category_n"], details["family_n"])
    )

    return fmt, category, bot.LANGUAGES[language], f"{fmt}|{category}|{language}"
