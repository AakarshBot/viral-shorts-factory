"""YouTube upload metadata, creator comment and newsroom runtime hooks."""

import os
import re


def _clean_comment(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:4900].strip()


def build_pinned_comment(script_data, video_title, genre_label=""):
    """Return an engaging creator comment with a light CTA, not a spoken-video CTA."""
    candidate = _clean_comment(script_data.get("pinned_comment", ""))

    if candidate and "?" in candidate:
        comment = candidate
    else:
        scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
        final_voice = ""
        if scenes and isinstance(scenes[-1], dict):
            final_voice = _clean_comment(scenes[-1].get("voiceover", ""))
        comment = final_voice if "?" in final_voice else f"What do you make of this: {video_title}?"

    lower = comment.lower()
    if not any(term in lower for term in ("subscribe", "follow", "more stories")):
        comment = f"{comment} If you want more stories like this, subscribe for the next one."
    elif len(comment) < 120 and "?" not in comment:
        comment = f"{comment} What do you think?"

    return _clean_comment(comment)


def post_creator_comment(youtube, video_id, script_data, video_title, genre_label=""):
    """Post one top-level creator comment after upload.

    YouTube's public Data API can create the comment but does not expose a
    pin/unpin operation, so the comment must be pinned once in YouTube Studio.
    """
    comment_text = build_pinned_comment(script_data, video_title, genre_label)
    body = {
        "snippet": {
            "videoId": video_id,
            "topLevelComment": {
                "snippet": {"textOriginal": comment_text}
            },
        }
    }
    response = youtube.commentThreads().insert(part="snippet", body=body).execute()
    comment_id = response.get("id")
    print(f"   [+] Creator comment posted: {comment_text}", flush=True)
    print("   [!] YouTube Data API does not expose comment pinning. Pin this comment once in YouTube Studio.", flush=True)
    return comment_id, comment_text



def ensure_shorts_title(title):
    """Normalize legacy title input without forcing a Shorts hashtag."""
    base = re.sub(r"\s*#shorts\b", "", str(title or ""), flags=re.IGNORECASE).strip()
    return (base[:100].rstrip() or "Shorts").strip()


def build_description_hashtags(genre_cfg, trend_keyword=""):
    """Return at most three relevant hashtags for the description."""
    candidates = []
    if trend_keyword:
        trend_tag = re.sub(r"[^a-zA-Z0-9]", "", str(trend_keyword))
        if trend_tag:
            candidates.append(f"#{trend_tag}")
    for value in (genre_cfg or {}).get("hashtags", []):
        tag = str(value or "").strip()
        if tag and not tag.startswith("#"):
            tag = "#" + re.sub(r"[^a-zA-Z0-9]", "", tag)
        if tag:
            candidates.append(tag)
    result = []
    seen = set()
    for tag in candidates:
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            result.append(tag)
        if len(result) == 3:
            break
    return result

def _build_clean_metadata(script_data, genre_cfg, trend_keyword):
    raw_title = str(script_data.get("title") or genre_cfg.get("label", "Shorts")).strip()
    raw_title = re.sub(r"\s*#shorts\b", "", raw_title, flags=re.IGNORECASE).strip()
    if trend_keyword and str(trend_keyword).lower() not in raw_title.lower():
        raw_title = f"{trend_keyword}: {raw_title}"
    title = ensure_shorts_title(raw_title)

    desc_body = str(script_data.get("seo_description") or "").strip()
    if trend_keyword and str(trend_keyword).lower() not in desc_body.lower():
        desc_body = f"Trending now: {trend_keyword}. {desc_body}"
    hashtags = list(genre_cfg.get("hashtags", ["#Trending"]))
    if trend_keyword:
        trend_tag = re.sub(r"[^a-zA-Z0-9]", "", str(trend_keyword))
        if trend_tag:
            hashtags.insert(0, f"#{trend_tag}")
    description = f"{desc_body}\n\n{' '.join(build_description_hashtags(genre_cfg, trend_keyword))}".strip()[:5000]

    tags = script_data.get("tags", ["Shorts", genre_cfg.get("label", "Shorts")])
    if not isinstance(tags, list):
        tags = [tags]
    tags = [str(tag).strip() for tag in tags if str(tag).strip()][:30]
    return title, description, tags


def patch_youtube_upload(bot):
    """Install the creator-comment uploader on the already-bound runtime."""
    current = getattr(bot, "upload_to_youtube", None)
    if current is None or getattr(current, "_creator_comment_wrapped", False):
        return current

    def upload_with_creator_comment(
        video_path,
        script_data,
        genre_cfg,
        publish_mode,
        trend_keyword=None,
        title_override=None,
        description_override=None,
        comment_override=None,
    ):
        try:
            import googleapiclient.discovery
            from googleapiclient.http import MediaFileUpload

            if not video_path or not os.path.isfile(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")

            creds = bot.get_google_credentials()
            youtube = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
            generated_title, generated_description, tags = _build_clean_metadata(
                script_data, genre_cfg, trend_keyword
            )
            title = ensure_shorts_title(title_override) if title_override is not None else generated_title
            description = str(description_override).strip()[:5000] if description_override is not None else generated_description
            comment_text = _clean_comment(comment_override) if comment_override is not None else build_pinned_comment(
                script_data, title, genre_cfg.get("label", "")
            )

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": str(genre_cfg.get("category_id", "24")),
                },
                "status": {
                    "privacyStatus": "private" if str(publish_mode).lower() == "private" else "public",
                    "selfDeclaredMadeForKids": False,
                },
            }
            media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    print(f"   [Upload Progress] {int(status.progress() * 100)}%", flush=True)

            video_id = response.get("id") if response else None
            if not video_id:
                raise RuntimeError("YouTube upload completed without a video ID.")
            print(f"   [+] Successfully uploaded to YouTube! Video ID: {video_id}", flush=True)

            if body["status"]["privacyStatus"] == "public":
                try:
                    comment_id, posted_comment = post_creator_comment(
                        youtube,
                        video_id,
                        {**script_data, "pinned_comment": comment_text},
                        title,
                        genre_cfg.get("label", ""),
                    )
                    script_data["creator_comment_id"] = comment_id or ""
                    script_data["creator_comment"] = posted_comment
                except Exception as exc:
                    print(f"   [!] Creator comment failed, but upload succeeded: {exc}", flush=True)
            else:
                print("   [i] Creator comment skipped because the video is not public.", flush=True)

            return video_id
        except Exception as exc:
            print(f"   [!] YouTube upload failed: {exc}", flush=True)
            return None

    upload_with_creator_comment._creator_comment_wrapped = True
    bot.upload_to_youtube = upload_with_creator_comment
    return upload_with_creator_comment
