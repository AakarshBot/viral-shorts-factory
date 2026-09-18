import inspect

from PIL import Image

from dashboard_runtime import upload_ready_for_manual_decision
from pipeline_integrity_runtime import _wrap_compile
from subtitle_runtime import _patch_deep_dive_subtitle_condition
from visual_content_runtime import _render_scene_overlay


def test_scene_overlay_contains_no_editorial_text_or_cards():
    image = Image.new("RGBA", (108, 192), (20, 30, 40, 255))
    rendered = _render_scene_overlay(
        None,
        image,
        scene_number=1,
        total_scenes=6,
        visual_type="GENERAL_CONTEXT",
        source_type="Reuters",
        voiceover="A factual sentence with 42 percent.",
    )
    assert rendered.size == image.size
    assert rendered.tobytes() == image.tobytes()


def test_first_slide_subtitle_patch_no_longer_reenables_scene_one():
    source = inspect.getsource(_patch_deep_dive_subtitle_condition)
    assert 'format_mode != "top5"' not in source
    assert "Deep Dive scene 1 subtitles enabled" not in source


def test_pipeline_integrity_no_longer_has_endpoint_subtitle_layer():
    import pipeline_integrity_runtime

    assert not hasattr(pipeline_integrity_runtime, "_add_endpoint_subtitles")

    source = inspect.getsource(_wrap_compile)
    assert "_add_endpoint_subtitles" not in source


def test_upload_gate_only_opens_for_completed_idle_render():
    assert upload_ready_for_manual_decision({"completed": True, "thread_alive": False, "video_path": "final_video.mp4"})
    assert not _upload_ready({"completed": True, "thread_alive": True, "video_path": "final_video.mp4"})
    assert not _upload_ready({"completed": False, "thread_alive": False, "video_path": "final_video.mp4"})
    assert not _upload_ready({"completed": True, "thread_alive": False, "video_path": ""})
