from pathlib import Path

import ultimate_bot
from workflow_runtime import WorkflowController


class _FakeUploadRequest:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        return None, self.response


class _FakeVideos:
    def __init__(self, response):
        self.response = response
        self.insert_calls = []

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return _FakeUploadRequest(self.response)


class _FakeCommentRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class _FakeComments:
    def __init__(self, response):
        self.response = response
        self.insert_calls = []

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return _FakeCommentRequest(self.response)


class _FakeYouTube:
    def __init__(self):
        self.videos_api = _FakeVideos(
            {
                "id": "video-123",
                "snippet": {"channelId": "channel-123"},
            }
        )
        self.comments_api = _FakeComments(
            {
                "id": "comment-123",
                "snippet": {"topLevelComment": {"id": "comment-123"}},
            }
        )

    def videos(self):
        return self.videos_api

    def commentThreads(self):
        return self.comments_api


def _patch_youtube_upload(monkeypatch, fake_youtube):
    import googleapiclient.discovery
    import googleapiclient.http

    monkeypatch.setattr(ultimate_bot, "get_google_credentials", lambda: object())
    monkeypatch.setattr(
        googleapiclient.discovery,
        "build",
        lambda *args, **kwargs: fake_youtube,
    )
    monkeypatch.setattr(
        googleapiclient.http,
        "MediaFileUpload",
        lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
    )


def test_youtube_upload_private_sets_private_and_does_not_post_comment(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    fake = _FakeYouTube()
    _patch_youtube_upload(monkeypatch, fake)

    video_id = ultimate_bot.upload_to_youtube(
        str(video_path),
        {"title": "Test Short", "seo_description": "Description.", "pinned_comment": "Comment."},
        {"label": "News", "category_id": "25", "hashtags": ["#News"]},
        "private",
    )

    assert video_id == "video-123"
    body = fake.videos_api.insert_calls[0]["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert fake.comments_api.insert_calls == []


def test_youtube_upload_public_posts_approved_comment(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    fake = _FakeYouTube()
    _patch_youtube_upload(monkeypatch, fake)

    video_id = ultimate_bot.upload_to_youtube(
        str(video_path),
        {
            "title": "Test Short",
            "seo_description": "Description.",
            "pinned_comment": "Fallback comment.",
        },
        {"label": "News", "category_id": "25", "hashtags": ["#News"]},
        "public",
        comment_override="Approved dashboard comment.",
    )

    assert video_id == "video-123"
    body = fake.videos_api.insert_calls[0]["body"]
    assert body["status"]["privacyStatus"] == "public"
    assert len(fake.comments_api.insert_calls) == 1
    comment_body = fake.comments_api.insert_calls[0]["body"]
    assert (
        comment_body["snippet"]["topLevelComment"]["snippet"]["textOriginal"]
        == "Approved dashboard comment."
    )


def test_workflow_controller_upload_delegates_approved_payload(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    controller = WorkflowController(type("Bot", (), {})())
    controller.state.completed = True
    controller.state.thread_alive = False
    controller.state.video_path = str(video_path)

    calls = {}

    def fake_uploader(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return "video-456"

    controller.bot.upload_to_youtube = fake_uploader

    import dashboard_runtime
    import final_qc_runtime

    monkeypatch.setattr(dashboard_runtime, "live_qc_passes", lambda *args, **kwargs: True)
    monkeypatch.setattr(final_qc_runtime, "validate_final_video", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        final_qc_runtime,
        "validate_final_upload_metadata",
        lambda title, description, comment: (title, description, comment),
    )

    result = controller.upload_manual(
        str(video_path),
        {"title": "Approved title"},
        "Approved title",
        "Approved description",
        "Approved comment",
        "private",
        {"label": "News", "category_id": "25", "hashtags": []},
    )

    assert result == "video-456"
    assert calls["args"][0] == str(video_path)
    assert calls["args"][3] == "private"
    assert calls["kwargs"]["title_override"] == "Approved title"
    assert calls["kwargs"]["description_override"] == "Approved description"
    assert calls["kwargs"]["comment_override"] == "Approved comment"
