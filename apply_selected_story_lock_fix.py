from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
path = ROOT / "ultimate_bot.py"
backup = ROOT / "ultimate_bot.py.pre_selected_story_lock.bak"

if not path.exists():
    raise SystemExit(f"Missing required file: {path}")

shutil.copy2(path, backup)
text = path.read_text(encoding="utf-8")

old = '''        pool = gather_and_filter_stories(\n            conn,\n            cat_choice,\n            genre_cfg,\n            trend_keyword=trend_keyword,\n            custom_gnews_q=custom_q,\n            custom_rss_url=custom_rss,\n        )\n'''

new = '''        selected_story = web_config.get("selected_story") if isinstance(web_config, dict) else None\n        if isinstance(selected_story, dict) and str(selected_story.get("title", "")).strip():\n            # Dashboard production must render the story the user explicitly selected.\n            # Never perform a second broad discovery pass here.\n            pool = [dict(selected_story)]\n            print(\n                f"   [Workflow] Production story locked: {selected_story.get('title')}",\n                flush=True,\n            )\n        else:\n            pool = gather_and_filter_stories(\n                conn,\n                cat_choice,\n                genre_cfg,\n                trend_keyword=trend_keyword,\n                custom_gnews_q=custom_q,\n                custom_rss_url=custom_rss,\n            )\n'''

count = text.count(old)
if count != 1:
    raise SystemExit(
        f"PATCH ABORTED: selected-story discovery block expected 1 match, found {count}"
    )

path.write_text(text.replace(old, new), encoding="utf-8")
print("[PATCH] Production uses the exact dashboard-selected story")
print("[PATCH] Second broad discovery pass is bypassed for selected production")
print(f"[BACKUP] {backup}")
print("\nSELECTED STORY LOCK FIX APPLIED")
