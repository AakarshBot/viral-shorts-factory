"""Load the pipeline integrity runtime with its standard-library JSON helper bound."""
import json
import pipeline_integrity_runtime

pipeline_integrity_runtime.json = json
patch_pipeline_integrity = pipeline_integrity_runtime.patch_pipeline_integrity
