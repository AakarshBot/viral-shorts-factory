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


def test_upload_gate_only_opens_for_completed_or_recoverable_idle_render(tmp_path):
    final_video = tmp_path / "final_video.mp4"
    final_video.write_bytes(b"placeholder")

    assert upload_ready_for_manual_decision({
        "completed": True,
        "thread_alive": False,
        "video_path": str(final_video),
    })
    assert not upload_ready_for_manual_decision({
        "completed": True,
        "thread_alive": True,
        "video_path": str(final_video),
    })
    assert upload_ready_for_manual_decision({
        "completed": False,
        "thread_alive": False,
        "stage": "error",
        "percent": 100,
        "video_path": str(final_video),
    })
    assert not upload_ready_for_manual_decision({
        "completed": False,
        "thread_alive": False,
        "stage": "error",
        "percent": 95,
        "video_path": str(final_video),
    })
    assert not upload_ready_for_manual_decision({
        "completed": False,
        "thread_alive": False,
        "stage": "error",
        "percent": 100,
        "video_path": "",
    })



def test_final_artifact_qc_does_not_depend_on_branding_runtime(monkeypatch):
    import sys
    import types
    import final_qc_runtime

    class FakeCapture:
        def __init__(self, _path):
            self.values = {
                7: 30.0,
                5: 1080.0,
                4: 1920.0,
                3: 300.0,
            }

        def isOpened(self):
            return True

        def get(self, prop):
            return self.values.get(prop, 0.0)

        def read(self):
            return True, object()

        def release(self):
            return None

    fake_cv2 = types.SimpleNamespace(
        VideoCapture=FakeCapture,
        CAP_PROP_FPS=7,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_FRAME_COUNT=5,
        CAP_PROP_FRAME_WIDTH=3,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setattr(final_qc_runtime.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(final_qc_runtime.os.path, "getsize", lambda _path: 4 * 1024 * 1024)

    ok, detail = final_qc_runtime._validate_final_artifact("final.mp4")
    assert ok is True
    assert "1080x1920" in detail
    assert "10.00s" in detail
