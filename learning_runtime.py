"""Reliable post-publication YouTube analytics sync for factory-created Shorts."""
from datetime import datetime, timedelta


def _safe_float(value):
    try:
        value = float(value)
        return value if value == value else None
    except (TypeError, ValueError):
        return None


def sync_factory_analytics(bot, conn):
    """Refresh analytics for factory-created videos using exact video IDs.

    Unlike the legacy channel-wide sweep, this never identifies a run by topic/title.
    It updates only the vault row whose video_id exactly matches the YouTube video.
    A row is marked reported only after YouTube statistics are successfully read.
    Retention/CTR are allowed to remain NULL while YouTube has not exposed them yet.
    """
    print("\n📊 RUNNING FACTORY LEARNING SYNC...")
    try:
        import googleapiclient.discovery

        creds = bot.get_google_credentials()
        youtube = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
        yt_analytics = googleapiclient.discovery.build(
            "youtubeAnalytics", "v2", credentials=creds
        )

        rows = conn.execute(
            """SELECT id, video_id, date_used, reported
               FROM vault
               WHERE video_id IS NOT NULL
                 AND video_id NOT IN ('', 'PENDING_QC', 'REJECTED', 'FAILED')
                 AND status NOT IN ('REJECTED', 'FAILED', 'PENDING_QC')
               ORDER BY id"""
        ).fetchall()

        updated = 0
        retention_ready = 0
        analytics_errors = 0

        for row_id, video_id, date_used, reported in rows:
            video_id = str(video_id).strip()
            if not video_id:
                continue

            try:
                video_resp = youtube.videos().list(
                    part="statistics,snippet", id=video_id
                ).execute()
                items = video_resp.get("items", [])
                if not items:
                    print(f"   [Learning] Video {video_id} was not returned by YouTube.")
                    continue

                stats = items[0].get("statistics", {})
                views_raw = stats.get("viewCount")
                likes_raw = stats.get("likeCount")
                views = int(views_raw) if views_raw is not None else None

                try:
                    published_at = items[0].get("snippet", {}).get("publishedAt", "")
                    start_date = published_at[:10]
                except Exception:
                    start_date = ""

                if not start_date:
                    if hasattr(date_used, "strftime"):
                        start_date = date_used.strftime("%Y-%m-%d")
                    else:
                        start_date = str(date_used)[:10]

                end_date = datetime.now().strftime("%Y-%m-%d")
                avg_pct = None
                avg_duration = None
                ctr = None

                # Shorts retention. New videos can legitimately have no row yet.
                try:
                    retention = yt_analytics.reports().query(
                        ids="channel==MINE",
                        startDate=start_date,
                        endDate=end_date,
                        metrics="averageViewPercentage,averageViewDuration",
                        dimensions="video",
                        filters=f"video=={video_id}",
                    ).execute()
                    if retention.get("rows"):
                        values = retention["rows"][0]
                        avg_pct = _safe_float(values[1]) if len(values) > 1 else None
                        avg_duration = _safe_float(values[2]) if len(values) > 2 else None
                except Exception as exc:
                    analytics_errors += 1
                    print(f"   [Learning] Retention unavailable for {video_id}: {exc}")

                # CTR is retained when YouTube exposes it. It is not assumed to exist for Shorts.
                try:
                    ctr_resp = yt_analytics.reports().query(
                        ids="channel==MINE",
                        startDate=start_date,
                        endDate=end_date,
                        metrics="videoThumbnailImpressionsClickThroughRate",
                        dimensions="video",
                        filters=f"video=={video_id}",
                    ).execute()
                    if ctr_resp.get("rows"):
                        ctr = _safe_float(ctr_resp["rows"][0][1])
                except Exception:
                    pass

                sets = ["reported=1", "updated_at=CURRENT_TIMESTAMP"]
                params = []
                if views is not None:
                    sets.append("views=?")
                    params.append(views)
                if avg_pct is not None:
                    sets.append("avg_view_percentage=?")
                    params.append(avg_pct)
                if avg_duration is not None:
                    sets.append("avg_view_duration=?")
                    params.append(avg_duration)
                if ctr is not None:
                    sets.append("title_ctr=?")
                    params.append(ctr)

                params.append(row_id)
                conn.execute(
                    f"UPDATE vault SET {', '.join(sets)} WHERE id=?",
                    params,
                )
                conn.commit()
                updated += 1
                if avg_pct is not None:
                    retention_ready += 1

                print(
                    f"   [Learning] {video_id}: views={views if views is not None else 'n/a'} "
                    f"retention={avg_pct if avg_pct is not None else 'pending'} "
                    f"duration={avg_duration if avg_duration is not None else 'pending'}"
                )

            except Exception as exc:
                analytics_errors += 1
                print(f"   [Learning] Failed to sync {video_id}: {type(exc).__name__}: {exc}")

        print(
            "   [+] Learning sync complete: %d factory videos refreshed; %d have retention data; %d analytics issues."
            % (updated, retention_ready, analytics_errors)
        )
        return {
            "updated": updated,
            "retention_ready": retention_ready,
            "analytics_errors": analytics_errors,
        }
    except Exception as exc:
        print(f"   [Learning] YouTube analytics sync unavailable: {type(exc).__name__}: {exc}")
        return {"updated": 0, "retention_ready": 0, "analytics_errors": 1}
