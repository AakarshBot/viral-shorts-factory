"""YouTube post-upload engagement comment support."""

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


def patch_youtube_upload(bot):
    """Wrap upload_to_youtube so every successful public upload gets a creator comment."""
    current = getattr(bot, "upload_to_youtube", None)
    if current is None or getattr(current, "_creator_comment_wrapped", False):
        return current

    def upload_with_creator_comment(video_path, script_data, genre_cfg, publish_mode, trend_keyword=None):
        video_id = current(video_path, script_data, genre_cfg, publish_mode, trend_keyword=trend_keyword)
        if not video_id:
            return video_id
        if str(publish_mode).lower() != "public":
            print("   [i] Creator comment skipped because the video is not public.", flush=True)
            return video_id

        try:
            import googleapiclient.discovery
            creds = bot.get_google_credentials()
            youtube = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
            comment_id, comment_text = post_creator_comment(
                youtube,
                video_id,
                script_data,
                script_data.get("title", genre_cfg.get("label", "Shorts")),
                genre_cfg.get("label", ""),
            )
            script_data["creator_comment_id"] = comment_id or ""
            script_data["creator_comment"] = comment_text
        except Exception as exc:
            print(f"   [!] Creator comment failed, but upload succeeded: {exc}", flush=True)
        return video_id

    upload_with_creator_comment._creator_comment_wrapped = True
    bot.upload_to_youtube = upload_with_creator_comment
    return upload_with_creator_comment
