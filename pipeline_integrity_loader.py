"""Load the canonical pipeline integrity runtime without adding a second audio guard."""
import pipeline_integrity_runtime


def patch_pipeline_integrity(bot):
    """Install the single canonical integrity wrapper set."""
    return pipeline_integrity_runtime.patch_pipeline_integrity(bot)
