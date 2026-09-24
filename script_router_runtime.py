"""Lightweight script router for the restored historical-style writer."""
from __future__ import annotations
import json
import os

MIN_SCRIPT_SECONDS=16.0
MAX_SCRIPT_SECONDS=30.0
DURATION_REPAIR_TARGET_SECONDS=27.0

def _estimate_script_duration(script_data):
    import script_runtime as sr
    try:
        from ultimate_bot import PERSONA_PROFILES
        name=str(script_data.get("delivery_profile") or script_data.get("persona_used") or "LISTICLE HOST").upper()
        profile=PERSONA_PROFILES.get(name,PERSONA_PROFILES["LISTICLE HOST"])
    except Exception: profile={}
    return sr.estimate_narration_duration(script_data,profile)

def _validate_script_result(result,story_data,format_mode):
    if not isinstance(result,dict) or not isinstance(result.get("script"),list): return None,"Provider returned no usable script array."
    import script_runtime as sr
    try:
        cleaned,diag=sr.clean_script_data(result,story_data,format_mode)
        ok,reason=sr.validate_content_density(cleaned,story_data,format_mode)
        if not ok: return None,reason
        release_ok,release_reason,assessment=sr.assess_release_structure(cleaned,format_mode)
        if not release_ok: return None,release_reason
        titles=cleaned.get("titles")
        if not isinstance(titles,list) or len(titles)!=3 or any(not str(t or "").strip() for t in titles): return None,"Exactly three non-empty title candidates are required."
        try: idx=int(cleaned.get("recommended_title_index",1))
        except (TypeError,ValueError): return None,"Recommended title index is invalid."
        if idx not in (1,2,3): return None,"Recommended title index is invalid."
        originality=sr.check_script_originality(cleaned,story_data)
        if not originality.get("passed"): return None,"Script contains copied or near-verbatim source wording."
        cleaned["recommended_title_index"]=idx; cleaned["pipeline_diagnostics"]=diag; cleaned["narrative_structure"]=assessment; cleaned["originality_overlap"]=originality
        return cleaned,""
    except Exception as exc: return None,f"Script QC failed: {type(exc).__name__}: {exc}"

def _bounded_rewrite(provider_writer,story_data,candidate,language_cfg,genre_key,conn,format_mode,direction,reason=""):
    payload=dict(story_data or {})
    if direction=="qc":
        payload["_qc_repair_script"]=json.dumps(candidate,ensure_ascii=False)
        payload["_qc_repair_instruction"]=(
            "Repair the failed QC rule without changing the underlying story. "
            f"QC failure: {reason} Preserve every crucial supported fact, entity, number and attribution. "
            "If Scene 1 is over 14 words, compress it to 14 words or fewer. "
            "If narration is over 90 words, remove only repetition or non-essential wording. "
            "If retention bait, filler, narrative structure, title count, or originality caused the failure, "
            "rewrite the affected parts while preserving the complete factual story. "
            "Return a complete 4–5 scene replacement for regular Shorts with exactly 3 titles. "
            "Do not invent facts and do not pad duration."
        )
    elif direction=="tighten":
        payload["_duration_tighten_script"]=json.dumps(candidate,ensure_ascii=False)
        payload["_duration_tighten_instruction"]=f"Compress toward {DURATION_REPAIR_TARGET_SECONDS:.0f} seconds while preserving every crucial supported fact, entity, number, attribution and consequence; remove repetition only; do not add facts."
    else:
        payload["_duration_expand_script"]=json.dumps(candidate,ensure_ascii=False)
        payload["_duration_expand_instruction"]="Rewrite the unusually short draft within four or five scenes and add only missing supported context or crucial factual detail. Do not add filler or invented analysis. Aim for 18–24 seconds."
    try: revised=provider_writer(payload,language_cfg,genre_key,conn,format_mode)
    except Exception as exc: return None,f"bounded {direction} rewrite failed: {type(exc).__name__}: {exc}"
    valid,validation_reason=_validate_script_result(revised,story_data,format_mode)
    if valid is None: return None,validation_reason
    est=_estimate_script_duration(valid)
    valid["duration_repair_attempted"]=direction in {"tighten","expand"}
    valid["qc_repair_attempted"]=direction=="qc"
    valid["duration_repair_direction"]=direction if direction in {"tighten","expand"} else ""
    valid["qc_repair_reason"]=reason if direction=="qc" else ""
    valid["estimated_duration_seconds"]=est["seconds"]; valid["estimated_duration_word_count"]=est["word_count"]; valid["estimated_duration_effective_wpm"]=est["effective_wpm"]
    return valid,""

def _apply_delivery_profile(bot,script_data):
    try:
        from audio_direction_runtime import choose_delivery_profile
        profile=str(choose_delivery_profile(bot,script_data) or "").strip()
    except Exception: profile=""
    if profile: script_data["delivery_profile"]=profile
    return profile

def install_script_pipeline(bot):
    current=getattr(bot,"write_script",None); run_robot=getattr(bot,"run_robot",None)
    if not callable(current): raise RuntimeError("Script pipeline cannot install: primary writer is missing.")
    if run_robot is None or not hasattr(run_robot,"__globals__"): raise RuntimeError("Script pipeline cannot install: run_robot globals are unavailable.")
    if getattr(current,"_canonical_script_pipeline",False):
        run_robot.__globals__["write_script"]=current; return current
    def write_script(story_data,language_cfg,genre_key,conn,format_mode):
        data=dict(story_data or {})
        attempts=[("primary writer",lambda: current(data,language_cfg,genre_key,conn,format_mode))]
        if os.getenv("GEMINI_API_KEY"):
            from research_runtime import _gemini_script_fallback
            attempts.append(("Gemini",lambda: _gemini_script_fallback(data,language_cfg,genre_key,format_mode)))
        remote=str(os.getenv("VSF_REMOTE_MODE") or "").strip().lower(); ollama=str(os.getenv("OLLAMA_BASE_URL") or "").strip()
        if remote not in {"1","true","yes","remote","cloud","streamlit","streamlit_cloud"} or ollama:
            from research_runtime import _ollama_script_fallback
            attempts.append(("Ollama",lambda: _ollama_script_fallback(data,language_cfg,genre_key,format_mode)))
        reasons=[]
        for provider_name,call in attempts:
            try: print(f"   [Script Writer] Provider: {provider_name}.",flush=True); candidate=call()
            except Exception as exc: reasons.append(f"{provider_name}: {type(exc).__name__}: {exc}"); print(f"   [Script Writer] {reasons[-1]}",flush=True); continue
            if not candidate: reasons.append(f"{provider_name}: no candidate"); continue
            valid,reason=_validate_script_result(candidate,data,format_mode)
            if valid is None:
                repairable=any(marker in str(reason) for marker in (
                    "Narration exceeds the safety ceiling:","Scene 1 is too long:",
                    "contains prohibited retention-bait phrasing.","Narrative is incomplete:",
                    "Scene 1 must be the factual retention hook.","final scene must deliver the consequence",
                    "Narrative needs at least one development or context beat",
                    "Regular Short must contain exactly 4 or 5 scenes.",
                    "Exactly three non-empty title candidates are required.","Recommended title index is invalid.",
                    "Script contains copied or near-verbatim source wording.",
                ))
                if repairable:
                    repaired,rr=_bounded_rewrite(call,data,candidate,language_cfg,genre_key,conn,format_mode,"qc",str(reason))
                    if repaired is not None:
                        valid=repaired
                        _apply_delivery_profile(bot,valid)
                        print(f"   [Script Writer] {provider_name} failed QC once; bounded preflight repair succeeded.",flush=True)
                    else:
                        reasons.append(f"{provider_name}: {reason}; bounded QC repair failed: {rr}")
                        print(f"   [Script Writer] Rejected: {reason}; bounded QC repair failed: {rr}",flush=True)
                        continue
                else:
                    reasons.append(f"{provider_name}: {reason}")
                    print(f"   [Script Writer] Rejected: {reason}",flush=True)
                    continue
            _apply_delivery_profile(bot,valid); est=_estimate_script_duration(valid)
            valid["estimated_duration_seconds"]=est["seconds"]; valid["estimated_duration_word_count"]=est["word_count"]; valid["estimated_duration_effective_wpm"]=est["effective_wpm"]
            if est["seconds"]<MIN_SCRIPT_SECONDS:
                repaired,rr=_bounded_rewrite(call,data,valid,language_cfg,genre_key,conn,format_mode,"expand")
                if repaired is None or repaired.get("estimated_duration_seconds",0)<MIN_SCRIPT_SECONDS:
                    reasons.append(f"{provider_name}: short after bounded expansion ({rr})"); continue
                valid=repaired; est=_estimate_script_duration(valid)
            if est["seconds"]>=MAX_SCRIPT_SECONDS:
                if provider_name=="primary writer":
                    repaired,rr=_bounded_rewrite(call,data,valid,language_cfg,genre_key,conn,format_mode,"tighten")
                    if repaired is not None and repaired.get("estimated_duration_seconds",999)<MAX_SCRIPT_SECONDS: valid=repaired
                    else: valid["tts_speed_adjustment_required"]=True; valid["duration_repair_failed_reason"]=rr
                else: valid["tts_speed_adjustment_required"]=True
            valid["provider_used"]=provider_name; bot._active_script_data=valid; return valid
        raise ValueError("Script generation failed after the approved provider ladder: "+" | ".join(reasons))
    write_script._canonical_script_pipeline=True; write_script._research_layer_live=False; write_script._content_dense_bound=True
    bot.write_script=write_script; run_robot.__globals__["write_script"]=write_script; bot._script_pipeline_installed=True
    return write_script
