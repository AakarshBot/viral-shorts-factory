"""Deterministic story grounding for automatic visual entities."""
from __future__ import annotations
import re
from typing import Any
from visual_semantic_guard_runtime import clean_text, infer_role, key

_GENERIC={"latest","breaking","news","update","story","report","reports","official","photo","image","picture","video","today","yesterday","tomorrow","said","says","according","reported","announced","announcement","revealed","reveal","new"}
_STOP={"the","and","for","with","from","into","after","before","over","under","about","this","that","these","those","their","there","here","when","where","what","which","while","have","has","had","will","would","could","should","been","were","was","are","our","you","your","is","a","an","to","of","in","on","as","it","its","who","how","why","or","but","than","also","can","may","might"}
_NON_STABLE={"GENERAL_CONTEXT","PROCESS","CONCEPT","QUOTE","STATISTIC","COMPARISON","TIMELINE"}
_CUES={"team","squad","club","federation","association","company","corporation","organisation","organization","government","ministry","agency","board","committee","university","institute","foundation","product","phone","device","car","stadium","arena","landmark","country","city","women","woman","men","man","player","coach"}

def _norm(value: Any)->str:
    text=clean_text(value).replace("’","'").replace("–","-").replace("—","-")
    return re.sub(r"\s+"," ",text).strip()

def _tokens(value: Any)->list[str]:
    out=[]
    for raw in re.findall(r"[\w][\w\'&./-]*",_norm(value),flags=re.UNICODE):
        t=key(raw)
        if t and t not in _GENERIC and t not in _STOP: out.append(t)
    return out

def _evidence(script_data:dict[str,Any])->str:
    if not isinstance(script_data,dict): return ""
    parts=[]
    for field in ("title","step_1_headline","step_2_data_points","text","summary","description","research_bundle"):
        value=script_data.get(field)
        if value: parts.append(_norm(value))
    sources=script_data.get("research_sources")
    if isinstance(sources,list):
        for source in sources:
            if isinstance(source,dict):
                parts.extend(_norm(source.get(f,"")) for f in ("title","snippet","summary","description","source") if source.get(f))
    return "\n".join(x for x in parts if x)

def _role(scene:dict[str,Any],entity:str)->str:
    try: return str(infer_role({"primary_entity":entity,"visual_intent":scene.get("visual_intent",""),"visual_type":scene.get("visual_type","")}) or "GENERAL_CONTEXT").upper()
    except Exception: return "GENERAL_CONTEXT"

def _support(entity:str,evidence:str,role:str)->tuple[float,str]:
    et=_tokens(entity); ev=set(_tokens(evidence))
    if not et or not ev: return 0.0,"no usable evidence"
    phrase=" ".join(et); evidence_phrase=" ".join(_tokens(evidence))
    if phrase and phrase in evidence_phrase: return 1.0,"exact entity phrase found in story evidence"
    overlap=[t for t in et if t in ev]; ratio=len(overlap)/max(1,len(et))
    if role in _NON_STABLE: return 0.6,"descriptive/context visual subject"
    if role=="PERSON":
        if len(et)>=2 and len(overlap)==len(et): return 0.96,"all person-name tokens supported by evidence"
        if len(et)==2 and len(overlap)==1 and len(overlap[0])>=7: return 0.84,"distinctive person token supported by evidence"
        if len(et)==1 and len(et[0])>=7 and overlap: return 0.84,"distinctive person name supported by evidence"
        return 0.0,"person identity is not supported by story evidence"
    if role in {"ORGANIZATION","PRODUCT","LOCATION","EVENT","DOCUMENT"}:
        if len(et)==1 and len(et[0])>=6 and overlap: return 0.82,"distinctive named-entity token supported by evidence"
        if ratio>=0.66: return 0.92,"most entity tokens supported by evidence"
        if ratio>=0.50 and len(overlap)>=2: return 0.84,"multi-token entity supported by evidence"
        return 0.0,"named entity is not sufficiently supported by story evidence"
    return (0.80,"strong evidence overlap") if ratio>=0.60 else (0.0,"entity is weakly supported by story evidence")

def _anchors(script_data:dict[str,Any])->list[str]:
    headline=_norm(script_data.get("title") or script_data.get("step_1_headline") or "")
    words=re.findall(r"[\w][\w\'&.-]*",headline,flags=re.UNICODE)
    found=[]
    for size in range(min(6,len(words)),1,-1):
        for start in range(0,len(words)-size+1):
            phrase=" ".join(words[start:start+size]); ts=set(_tokens(phrase))
            if ts and ts & _CUES: found.append(phrase)
    for size in range(min(4,len(words)),1,-1):
        for start in range(0,len(words)-size+1):
            group=words[start:start+size]
            if group and all(w[:1].isupper() for w in group): found.append(" ".join(group))
    result=[]
    for item in found:
        item=_norm(item)
        if item and item.casefold() not in {x.casefold() for x in result}: result.append(item)
    return result[:8]

def ground_scene_entity(scene:dict[str,Any],script_data:dict[str,Any])->dict[str,Any]:
    original=_norm(scene.get("factual_primary_entity") or scene.get("primary_entity") or scene.get("visual_search_subject") or "")
    if not original: return {"entity":"","grounded":False,"changed":False,"reason":"no visual entity supplied","confidence":0.0}
    evidence=_evidence(script_data); role=_role(scene,original); score,reason=_support(original,evidence,role)
    if score>=0.80 or role in _NON_STABLE: return {"entity":original,"grounded":True,"changed":False,"reason":reason,"confidence":score or 0.6,"original_entity":original}
    for anchor in _anchors(script_data):
        a_score,a_reason=_support(anchor,evidence,_role(scene,anchor))
        if a_score>=0.80: return {"entity":anchor,"grounded":False,"changed":anchor.casefold()!=original.casefold(),"reason":f"unsupported identity repaired to story anchor: {a_reason}","confidence":a_score,"original_entity":original}
    return {"entity":original,"grounded":False,"changed":False,"reason":reason,"confidence":0.0,"original_entity":original}

def apply_grounding(scene:dict[str,Any],script_data:dict[str,Any])->dict[str,Any]:
    scene=dict(scene or {})
    if _norm(scene.get("manual_visual_query","")):
        scene["visual_entity_grounding"]="MANUAL_LOCK"; scene["visual_entity_grounded"]=True; scene["visual_entity_grounding_reason"]="manual visual query is authoritative"; return scene
    result=ground_scene_entity(scene,script_data)
    original=scene.get("primary_entity","")
    if result.get("changed"):
        scene["original_primary_entity"]=original; scene["primary_entity"]=result["entity"]; scene["visual_search_subject"]=result["entity"]
    scene["visual_entity_grounded"]=bool(result.get("grounded"))
    scene["visual_entity_grounding_confidence"]=float(result.get("confidence") or 0.0)
    scene["visual_entity_grounding_reason"]=str(result.get("reason") or "")
    scene["visual_entity_original"]=str(result.get("original_entity") or original)
    return scene

__all__=["apply_grounding","ground_scene_entity"]