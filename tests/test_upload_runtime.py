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
        self.update_calls = []
        self.list_calls = []
        self.update_error = None
        self.update_response = None
        self.list_response = None

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return _FakeUploadRequest(self.response)

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        if self.update_error is not None:
            raise self.update_error
        response = self.update_response
        if response is None:
            response = self.response
        return _FakeCommentRequest(response)

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        response = self.list_response or self.update_response or self.response
        return _FakeCommentRequest({"items": [response]})


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
                "status": {"privacyStatus": "public"},
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


def test_youtube_upload_public_recovers_existing_private_video_without_duplicate_upload(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    fake = _FakeYouTube()
    fake.videos_api.response["status"]["privacyStatus"] = "private"
    fake.videos_api.update_response = {
        "id": "video-123",
        "status": {"privacyStatus": "public"},
    }
    _patch_youtube_upload(monkeypatch, fake)

    video_id = ultimate_bot.upload_to_youtube(
        str(video_path),
        {"title": "Test Short", "seo_description": "Description.", "pinned_comment": "Comment."},
        {"label": "News", "category_id": "25", "hashtags": ["#News"]},
        "public",
    )

    assert video_id == "video-123"
    assert len(fake.videos_api.insert_calls) == 1
    assert len(fake.videos_api.update_calls) == 1
    assert fake.videos_api.update_calls[0]["body"]["id"] == "video-123"
    assert fake.videos_api.update_calls[0]["body"]["status"]["privacyStatus"] == "public"
    assert len(fake.comments_api.insert_calls) == 1


def test_youtube_upload_public_uses_status_readback_when_update_response_is_incomplete(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    fake = _FakeYouTube()
    fake.videos_api.response["status"]["privacyStatus"] = "private"
    fake.videos_api.update_response = {
        "id": "video-123",
        "status": {"privacyStatus": "private"},
    }
    fake.videos_api.list_response = {
        "id": "video-123",
        "status": {"privacyStatus": "public"},
    }
    _patch_youtube_upload(monkeypatch, fake)

    video_id = ultimate_bot.upload_to_youtube(
        str(video_path),
        {"title": "Test Short", "seo_description": "Description.", "pinned_comment": "Comment."},
        {"label": "News", "category_id": "25", "hashtags": ["#News"]},
        "public",
    )

    assert video_id == "video-123"
    assert len(fake.videos_api.insert_calls) == 1
    assert len(fake.videos_api.update_calls) == 1
    assert len(fake.videos_api.list_calls) == 1
    assert fake.videos_api.list_calls[0]["id"] == "video-123"


def test_youtube_upload_public_reports_unrecoverable_visibility_block(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    fake = _FakeYouTube()
    fake.videos_api.response["status"]["privacyStatus"] = "private"
    fake.videos_api.update_error = RuntimeError("403 forbiddenPrivacySetting")
    _patch_youtube_upload(monkeypatch, fake)

    try:
        ultimate_bot.upload_to_youtube(
            str(video_path),
            {"title": "Test Short", "seo_description": "Description.", "pinned_comment": "Comment."},
            {"label": "News", "category_id": "25", "hashtags": ["#News"]},
            "public",
        )
    except Exception as exc:
        assert type(exc).__name__ == "YouTubePublicVisibilityError"
        assert "could not make it public" in str(exc)
        assert "forbiddenPrivacySetting" in str(exc)
        assert exc.video_id == "video-123"
    else:
        raise AssertionError("An unrecoverable public-visibility block must be reported to the caller.")


def test_workflow_controller_rejects_duplicate_upload_for_same_run(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    controller = WorkflowController(type("Bot", (), {})())
    controller.state.completed = True
    controller.state.thread_alive = False
    controller.state.video_path = str(video_path)
    controller.state.uploaded_video_id = "video-existing"
    monkeypatch.setattr(
        __import__("final_qc_runtime"),
        "validate_final_video",
        lambda *_args, **_kwargs: True,
    )
    import final_qc_runtime
    monkeypatch.setattr(
        final_qc_runtime,
        "validate_final_upload_metadata",
        lambda title, description, comment: (title, description, comment),
    )

    try:
        controller.upload_manual(
            str(video_path),
            {"title": "Approved title"},
            "Approved title",
            "Approved description",
            "Approved comment",
            "private",
            {"label": "News", "category_id": "25", "hashtags": []},
        )
    except RuntimeError as exc:
        assert "already been uploaded" in str(exc)
    else:
        raise AssertionError("Duplicate upload must be blocked.")


def test_workflow_controller_records_private_video_after_public_visibility_enforcement(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    controller = WorkflowController(type("Bot", (), {})())
    controller.state.completed = True
    controller.state.thread_alive = False
    controller.state.video_path = str(video_path)

    def forced_private_uploader(*args, **kwargs):
        raise ultimate_bot.YouTubePublicVisibilityError(
            "YouTube accepted video video-private-789 but persisted privacyStatus='private' instead of 'public'."
        )

    controller.bot.upload_to_youtube = forced_private_uploader

    import final_qc_runtime

    monkeypatch.setattr(final_qc_runtime, "validate_final_video", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        final_qc_runtime,
        "validate_final_upload_metadata",
        lambda title, description, comment: (title, description, comment),
    )

    try:
        controller.upload_manual(
            str(video_path),
            {"title": "Approved title"},
            "Approved title",
            "Approved description",
            "Approved comment",
            "public",
            {"label": "News", "category_id": "25", "hashtags": []},
        )
    except RuntimeError as exc:
        assert "already exists; do not retry" in str(exc)
    else:
        raise AssertionError("A public-visibility-enforced upload must stop without inviting a duplicate retry.")

    assert controller.state.uploaded_video_id == "video-private-789"

    try:
        controller.upload_manual(
            str(video_path),
            {"title": "Approved title"},
            "Approved title",
            "Approved description",
            "Approved comment",
            "public",
            {"label": "News", "category_id": "25", "hashtags": []},
        )
    except RuntimeError as exc:
        assert "already been uploaded" in str(exc)
    else:
        raise AssertionError("The same run must not be uploaded a second time.")


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

    import final_qc_runtime

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



def test_dashboard_binding_does_not_replace_canonical_youtube_uploader():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("runtime_bindings.py").read_text(encoding="utf-8")
    assert "_patch_youtube_creator_comments(bot)" not in source
    assert '"upload_to_youtube"' in source

def test_youtube_upload_blocks_public_publish_when_script_is_private_only(monkeypatch, tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"synthetic mp4")

    fake = _FakeYouTube()
    _patch_youtube_upload(monkeypatch, fake)

    try:
        ultimate_bot.upload_to_youtube(
            str(video_path),
            {
                "title": "Test Short",
                "seo_description": "This description contains enough words for metadata validation.",
                "pinned_comment": "Comment.",
                "public_publish_blocked": True,
            },
            {"label": "News", "category_id": "25", "hashtags": ["#News"]},
            "public",
        )
    except RuntimeError as exc:
        assert "private-only" in str(exc)
    else:
        raise AssertionError("The canonical uploader must enforce a private-only script flag.")

    assert fake.videos_api.insert_calls == []
