import inspect

import numpy as np

from dashboard_runtime import upload_ready_for_manual_decision
from pipeline_integrity_runtime import _wrap_compile
from branding_runtime import build_scene_branding_overlays


def test_canonical_branding_overlays_are_rgba_and_scene_sized():
    overlays = build_scene_branding_overlays(
        None,
        108,
        192,
        source_credit="Reuters",
    )

    assert len(overlays) == 2
    assert all(isinstance(overlay, np.ndarray) for overlay in overlays)
    assert all(overlay.shape == (192, 108, 4) for overlay in overlays)
    assert all(overlay.dtype == np.uint8 for overlay in overlays)


def test_pipeline_integrity_no_longer_has_endpoint_subtitle_layer():
    import pipeline_integrity_runtime

    assert not hasattr(pipeline_integrity_runtime, "_add_endpoint_subtitles")

    source = inspect.getsource(_wrap_compile)
    assert "_add_endpoint_subtitles" not in source


def test_upload_gate_only_opens_for_completed_idle_render():
    assert upload_ready_for_manual_decision({"completed": True, "thread_alive": False, "video_path": "final_video.mp4"})
    assert not upload_ready_for_manual_decision({"completed": True, "thread_alive": True, "video_path": "final_video.mp4"})
    assert not upload_ready_for_manual_decision({"completed": False, "thread_alive": False, "video_path": "final_video.mp4"})
    assert not upload_ready_for_manual_decision({"completed": True, "thread_alive": False, "video_path": ""})
