"""Dashboard runtime upgrades: data-driven scoring, semantic deduplication and visual design."""
import io, random, re, sys, traceback, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

_FITTED_WEIGHTS = None
_FITTED_SAMPLE_COUNT = -1
_SEMANTIC_MODEL = None
_SEMANTIC_ERROR = None


def install_safe_exception_hook():
    def hook(exctype, value, tb):
        print("💥 UNCAUGHT EXCEPTION DETECTED:")
        traceback.print_exception(exctype, value, tb)
    sys.excepthook = hook


def normalise_publish_mode(value):
    return "public" if str(value).strip().lower() == "public" else "private"


def fit_retention_weights(conn, min_samples=30, refit_every=20):
    """Fit editorial dimensions against normalized observed retention."""
    global _FITTED_WEIGHTS, _FITTED_SAMPLE_COUNT
    if conn is None: return None
    try:
        rows = conn.execute("""SELECT hook_strength,narrative_completeness,audience_fit,
            monetization_risk,shelf_life,avg_view_percentage FROM vault
            WHERE video_id NOT IN ('PENDING_QC','REJECTED')
            AND hook_strength IS NOT NULL AND narrative_completeness IS NOT NULL
            AND audience_fit IS NOT NULL AND monetization_risk IS NOT NULL
            AND shelf_life IS NOT NULL AND avg_view_percentage IS NOT NULL""").fetchall()
        n = len(rows)
        if n < min_samples: return None
        if _FITTED_WEIGHTS is not None and n - _FITTED_SAMPLE_COUNT < refit_every:
            return _FITTED_WEIGHTS
        x = np.asarray([r[:5] for r in rows], dtype=float)
        y0 = np.asarray([r[5] for r in rows], dtype=float)
        if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y0)) or y0.max() == y0.min(): return None
        y = (y0-y0.min())/(y0.max()-y0.min())
        std = x.std(axis=0); std[std < 1e-9] = 1.0
        design = np.column_stack([np.ones(n),(x-x.mean(axis=0))/std])
        coeffs,_,_,_ = np.linalg.lstsq(design,y,rcond=None)
        raw = coeffs[1:]; denom=float(np.abs(raw).sum())
        if denom < 1e-9: return None
        names=["hook_strength","narrative_completeness","audience_fit","monetization_risk","shelf_life"]
        _FITTED_WEIGHTS={k:float(v/denom) for k,v in zip(names,raw)}
        _FITTED_SAMPLE_COUNT=n
        print("   [Retention Model] Fitted from %d videos: %s" % (n,", ".join(f"{k}={v:+.3f}" for k,v in _FITTED_WEIGHTS.items())))
        return _FITTED_WEIGHTS
    except Exception as exc:
        print(f"   [Retention Model] Fit unavailable: {exc}")
        return None


def _get_semantic_model():
    global _SEMANTIC_MODEL, _SEMANTIC_ERROR
    if _SEMANTIC_MODEL is not None: return _SEMANTIC_MODEL
    if _SEMANTIC_ERROR: return None
    try:
        from sentence_transformers import SentenceTransformer
        print("   [Semantic Dedup] Loading all-MiniLM-L6-v2 on CPU (first run only)...")
        _SEMANTIC_MODEL=SentenceTransformer("all-MiniLM-L6-v2",device="cpu")
        return _SEMANTIC_MODEL
    except Exception as exc:
        _SEMANTIC_ERROR=str(exc)
        print(f"   [Semantic Dedup] Model unavailable: {exc}")
        return None


def semantic_duplicate_filter(stories, vault_topics, threshold=0.82):
    """Remove semantically duplicate titles using cosine similarity."""
    if not stories: return stories
    model=_get_semantic_model()
    if model is None:
        seen={re.sub(r"\W+"," ",str(x).lower()).strip() for x in vault_topics}
        return [s for s in stories if re.sub(r"\W+"," ",str(s.get("title","")).lower()).strip() not in seen]
    titles=[str(s.get("title","")).strip() for s in stories]
    refs=[str(x).strip() for x in vault_topics if str(x).strip()]
    emb=model.encode(titles+refs,normalize_embeddings=True,convert_to_numpy=True,show_progress_bar=False)
    cand,ref=emb[:len(titles)],emb[len(titles):]
    kept=[]; kept_emb=[]; dropped=0
    for story,e in zip(stories,cand):
        dup=bool(len(ref) and float(np.max(ref@e))>=threshold)
        if not dup and kept_emb: dup=float(np.max(np.asarray(kept_emb)@e))>=threshold
        if dup: dropped+=1
        else: kept.append(story); kept_emb.append(e)
    if dropped: print(f"   [Semantic Dedup] Removed {dropped} duplicates (cosine >= {threshold:.2f}).")
    return kept


def get_trend_signal_bonus(bot, keyword):
    """Return graded 0..10 Google Trends interest instead of binary 2.5/0."""
    keyword=bot.safe_text(keyword)
    if not keyword: return 0.0
    try:
        from pytrends.request import TrendReq
        stop={"the","and","for","with","from","this","that","into","after","before","over","under","what","how","why","world","news","latest","today","just"}
        terms=list(dict.fromkeys(t for t in re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}",keyword) if t.lower() not in stop))[:5]
        if not terms: return 0.0
        p=TrendReq(hl="en-US",tz=330,retries=1,backoff_factor=0.2)
        p.build_payload(terms,timeframe="now 7-d",geo="IN")
        df=p.interest_over_time()
        vals=[float(v) for t in terms if t in df.columns for v in df[t].tolist() if np.isfinite(v)] if df is not None and not df.empty else []
        return round(max(0,min(10,(max(vals)/10 if vals else 0))),2)
    except Exception: return 0.0


def _cheap_score(s):
    return float(s.get("corroboration_bonus",0))*2+float(s.get("velocity_score",0))-float(s.get("recency_penalty",0))+float(s.get("trend_bonus",0))


def preselect_candidates(stories, limit=15):
    """Rank a wider source pool cheaply, then send only the best 15 to the LLM."""
    return sorted(stories,key=_cheap_score,reverse=True)[:limit]


def genre_aware_epsilon_selection(options_dict,scores_dict,epsilon=0.2,strong_sample_threshold=8):
    keys=list(options_dict.keys())
    under=[k for k in keys if k not in scores_dict or scores_dict[k].get("score") is None or scores_dict[k].get("count",0)<5]
    strong=sum(1 for k in keys if scores_dict.get(k,{}).get("count",0)>=strong_sample_threshold and scores_dict.get(k,{}).get("score") is not None)
    eps=0.1 if strong>=max(1,len(keys)//2) else epsilon
    if under and random.random()<eps: return random.choice(under)
    scored=[(k,scores_dict.get(k,{}).get("score")) for k in keys if scores_dict.get(k,{}).get("score") is not None]
    return max(scored,key=lambda x:x[1])[0] if scored else random.choice(keys)


def auto_pilot_selection(bot,conn):
    fs=bot.get_smart_metrics(conn,"format_used","avg_view_percentage")
    fmt=genre_aware_epsilon_selection({"regular":1,"top5":1,"trending":1},fs)
    cs=bot.get_smart_metrics(conn,"genre","avg_view_percentage")
    cats={k:v for k,v in bot.CONTENT_CATEGORIES.items() if (v["usable_regular"] if fmt in ["regular","trending"] else v["usable_top5"]) and k!="tech_reviews"}
    cat=genre_aware_epsilon_selection(cats,cs)
    ls=bot.get_smart_metrics(conn,"language_used","avg_view_percentage")
    lang=genre_aware_epsilon_selection(bot.LANGUAGES,ls)
    print(f"   [Auto-Pilot] Genre-aware selection: {cat}; format={fmt}; language={lang}")
    return fmt,cat,bot.LANGUAGES[lang],f"{fmt}|{cat}|{lang}"


# ----------------------------- visual system -----------------------------
_TEMPLATES={"HYPE COMMENTATOR":["Bold Poster","Split Focus"],"ANALYTICAL INSIDER":["Magazine Cover","Split Focus"],"CYNICAL CRITIC":["Magazine Cover","Bold Poster"],"LISTICLE HOST":["Bold Poster","Split Focus"],"TECH REVIEWER":["Magazine Cover","Split Focus"]}

def _template(data): return random.choice(_TEMPLATES.get(str(data.get("persona_used","HYPE COMMENTATOR")).upper(),["Bold Poster","Magazine Cover"]))

def _vignette(size,strength=.5):
    small=Image.new("L",(120,213),0); p=small.load(); cx,cy=60,106.5; md=(cx*cx+cy*cy)**.5
    for y in range(213):
        for x in range(120):
            d=(((x-cx)**2+(y-cy)**2)**.5)/md
            normalized=max(0.0,(d-.18)/.82)
            p[x,y]=int(max(0,min(255,normalized**1.8*255*strength)))
    mask=small.resize(size,Image.Resampling.BILINEAR); out=Image.new("RGBA",size,(0,0,0,0)); out.paste((0,0,0,255),(0,0,*size),mask); return out

def _grain(img,opacity=12):
    g=Image.effect_noise((160,284),9).resize(img.size,Image.Resampling.BILINEAR); layer=Image.new("RGBA",img.size,(128,128,128,0)); layer.putalpha(g.point(lambda p:int(p*opacity/255))); return Image.alpha_composite(img,layer)

def _card_base(bg,box):
    base=bg.convert("RGBA"); crop=base.crop(box).filter(ImageFilter.GaussianBlur(24)); crop=ImageEnhance.Contrast(crop).enhance(1.12); crop=ImageEnhance.Color(crop).enhance(1.15); base.paste(crop,box); return _grain(Image.alpha_composite(base,_vignette(base.size)))

def _emphasis(text):
    words=text.split();
    if not words:return set()
    n=min(4,len(words)); return {re.sub(r"\W","",w).lower() for w in words[:n]}


def _hierarchy(draw,text,box,bot,font_choice,accent,start=80):
    font,lines=bot.fit_text_in_box(text,font_choice,box[2]-box[0],box[3]-box[1],start_size=start); size=getattr(font,"size",start); normal=bot.get_bold_font(size,font_choice); big=bot.get_bold_font(int(size*1.4),font_choice); emph=_emphasis(text); y=box[1]; center=(box[0]+box[2])/2
    for line in lines:
        ws=line.split(); widths=[draw.textlength(w,font=big if re.sub(r"\W","",w).lower() in emph else normal) for w in ws]; space=draw.textlength(" ",font=normal); x=center-(sum(widths)+space*(len(ws)-1))/2; lh=0
        for w,ww in zip(ws,widths):
            f=big if re.sub(r"\W","",w).lower() in emph else normal; fill=accent if f==big else (255,255,255); bb=draw.textbbox((0,0),w,font=f); lh=max(lh,bb[3]-bb[1]); draw.text((x+4,y+5),w,font=f,fill=(0,0,0,190)); draw.text((x,y),w,font=f,fill=fill,stroke_width=max(2,int(size*.035)),stroke_fill=(0,0,0,230)); x+=ww+space
        y+=lh+20

def render_hook_card(bot,bg_img,hook_text,width=1080,height=1920,font_choice=None,script_data=None):
    data=script_data or {}; accent=bot.PALETTE.get("accent_primary",(0,191,255)); box=[60,430,width-60,height-430]; base=_card_base(bg_img,box); draw=ImageDraw.Draw(base); t=_template(data)
    if t=="Bold Poster":
        draw.rounded_rectangle(box,radius=42,fill=(8,12,24,210),outline=accent+(220,),width=3); draw.rectangle([box[0],box[1],box[0]+12,box[3]],fill=accent+(255,)); _hierarchy(draw,hook_text,[120,640,width-120,height-620],bot,font_choice,accent,88)
    elif t=="Split Focus":
        draw.polygon([(box[0],box[1]),(box[2],box[1]),(box[2]-180,box[3]),(box[0],box[3])],fill=(8,12,24,222)); draw.line([(box[2]-180,box[1]),(box[2],box[1]+180)],fill=accent+(255,),width=9); _hierarchy(draw,hook_text,[105,1030,width-120,height-650],bot,font_choice,accent,76)
    else:
        draw.rounded_rectangle(box,radius=28,fill=(8,12,24,214),outline=(255,255,255,80),width=2); draw.line([(box[0]+75,box[1]+90),(box[2]-75,box[1]+90)],fill=accent+(255,),width=5); draw.text((box[0]+75,box[1]+42),str(data.get("persona_used","Editorial")).title(),font=bot.get_bold_font(34,font_choice),fill=accent); _hierarchy(draw,hook_text,[120,650,width-120,height-650],bot,font_choice,(255,255,255),70)
    stripe=Image.new("RGBA",base.size,(0,0,0,0)); sd=ImageDraw.Draw(stripe); sd.polygon([(width-250,0),(width,0),(width,70),(width-180,70)],fill=accent+(180,)); return Image.alpha_composite(base,stripe)

def create_branded_slide(bot,title_text,subtitle_text,is_outro=False,width=1080,height=1920,font_choice=None,script_data=None):
    data=script_data or {}; accent=bot.PALETTE.get("accent_primary",(0,191,255)); box=[60,420,width-60,height-360]; base=_card_base(Image.new("RGBA",(width,height),bot.PALETTE["bg"]+(255,)),box); draw=ImageDraw.Draw(base); t=_template(data); draw.rounded_rectangle(box,radius=36,fill=(8,12,24,220),outline=accent+(180,),width=3)
    if t=="Magazine Cover": draw.line([(box[0]+70,box[1]+90),(box[2]-70,box[1]+90)],fill=accent+(255,),width=4); draw.text((box[0]+70,box[1]+42),"VIRAL SHORTS FACTORY",font=bot.get_bold_font(32,font_choice),fill=accent)
    _hierarchy(draw,title_text,[120,box[1]+210,width-120,box[3]-300],bot,font_choice,accent,78)
    if subtitle_text:
        f=bot.get_bold_font(48,font_choice); bb=draw.textbbox((0,0),subtitle_text,font=f); x=(width-bb[2]+bb[0])/2; y=box[3]-210; draw.rounded_rectangle([x-35,y-20,x+bb[2]-bb[0]+35,y+70],radius=35,fill=(5,7,14,225),outline=accent+(190,),width=2); draw.text((x,y),subtitle_text,font=f,fill=accent)
    return _grain(base)

def render_top5_card(bot,bg_img,item_number,total_items,summary_text,width=1080,height=1920,font_choice=None,script_data=None):
    box=[60,280,width-60,height-280]; base=_card_base(bg_img,box); draw=ImageDraw.Draw(base); accent=bot.PALETTE.get("accent_primary",(0,191,255)); draw.rounded_rectangle(box,radius=40,fill=(8,12,24,200),outline=accent+(180,),width=3); draw.text((115,350),f"#{item_number}",font=bot.get_bold_font(112,font_choice),fill=accent); _hierarchy(draw,summary_text,[120,560,width-120,height-430],bot,font_choice,(255,255,255),66); return base


def patch_dashboard_runtime(bot):
    """Apply requested improvements to Streamlit execution."""
    def score(scored_data,batch,bonuses,last_genre,fmt):
        weights=fit_retention_weights(getattr(bot,"_active_scoring_conn",None)) or {"hook_strength":.25,"narrative_completeness":.20,"audience_fit":.20,"monetization_risk":-.20,"shelf_life":.15}
        out=[]
        for i,s in enumerate(scored_data):
            if i>=len(batch) or not isinstance(s,dict):continue
            try: vals={k:max(1,min(10,float(s.get(k,5)))) for k in weights}
            except (TypeError,ValueError):continue
            if s.get("hard_reject",False) or vals["monetization_risk"]>=8:continue
            story=batch[i]; story.update(vals); trend=get_trend_signal_bonus(bot,story.get("title","")); story["trend_bonus"]=trend
            comp=sum(vals[k]*weights[k] for k in weights)+trend+float(story.get("velocity_score",0))-float(story.get("recency_penalty",1))-((vals["monetization_risk"]-1)*.20)
            comp+=float(story.get("corroboration_bonus",0))+(bonuses.get(story.get("genre"),0) if fmt=="regular" else 0)+(2 if fmt=="regular" and story.get("genre")==last_genre else 0)
            story["composite_score"]=round(comp,3); out.append(story)
        return sorted(out,key=lambda x:x["composite_score"],reverse=True) or []
    bot.process_scored_candidates=score

    original_gather=bot.gather_and_filter_stories
    def gather(conn,genre_key,genre_cfg,trend_keyword=None,custom_gnews_q=None,custom_rss_url=None):
        stories=original_gather(conn,genre_key,genre_cfg,trend_keyword,custom_gnews_q,custom_rss_url)
        recent=[r[0] for r in conn.execute("SELECT topic FROM vault WHERE date_used >= ?",(datetime.now()-timedelta(days=30),)).fetchall()]
        return semantic_duplicate_filter(stories,recent,.82)
    bot.gather_and_filter_stories=gather

    # The newsroom contract requires genuinely diverse candidates. The legacy
    # selector used to backfill from rejected near-duplicates, which defeated
    # the exact-three gate in workflow_runtime. Replace that selector at the
    # live module boundary; direct discovery imports still see the canonical
    # function, while production receives the strict version after dashboard
    # runtime installation.
    try:
        import workflow_runtime
        if not getattr(workflow_runtime, "_strict_diversity_bound", False):
            def strict_diverse_top_three(stories):
                selected=[]
                for story in stories or []:
                    if not selected:
                        selected.append(story)
                        continue
                    if all(workflow_runtime._overlap(story.get("title",""), old.get("title","")) < 0.55 for old in selected):
                        selected.append(story)
                    if len(selected)==3:
                        return selected
                return selected[:3]
            workflow_runtime._diverse_top_three = strict_diverse_top_three
            workflow_runtime._strict_diversity_bound = True
    except Exception as exc:
        print(f"   [Dashboard] Strict discovery diversity binding unavailable: {type(exc).__name__}: {exc}", flush=True)

    original_editorial=bot.editorial_gate_batch
    def editorial(stories,bonuses,last_genre,fmt): return original_editorial(preselect_candidates(stories,15),bonuses,last_genre,fmt) if stories else None
    bot.editorial_gate_batch=editorial
    bot.get_trend_signal_bonus=lambda keyword:get_trend_signal_bonus(bot,keyword)
    bot.auto_pilot_selection=lambda conn:auto_pilot_selection(bot,conn)

    original_quality=bot.passes_quality_gate
    def quality(data,search_prompt="",video_title=""):
        if not original_quality(data,search_prompt,video_title):return False
        cv2=getattr(bot,"cv2",None); npmod=getattr(bot,"np",None); text=f"{search_prompt} {video_title}".lower(); terms=["person","people","man","woman","player","actor","actress","celebrity","politician","president","coach","cricketer","footballer","athlete","singer","director"]
        if cv2 is None or npmod is None or not any(t in text for t in terms):return True
        try:
            img=Image.open(io.BytesIO(data)).convert("RGB"); gray=cv2.cvtColor(npmod.array(img),cv2.COLOR_RGB2GRAY); c=cv2.CascadeClassifier(getattr(cv2.data,"haarcascades","")+"haarcascade_frontalface_default.xml"); return True if c.empty() else len(c.detectMultiScale(gray,1.1,4,minSize=(40,40)))>0
        except Exception:return True
    bot.passes_quality_gate=quality

    bot.render_hook_card=lambda bg_img,hook_text,width=1080,height=1920,font_choice=None:render_hook_card(bot,bg_img,hook_text,width,height,font_choice,getattr(bot,"_active_script_data",{}))
    bot.create_branded_slide=lambda title_text,subtitle_text,is_outro=False,width=1080,height=1920,font_choice=None:create_branded_slide(bot,title_text,subtitle_text,is_outro,width,height,font_choice,getattr(bot,"_active_script_data",{}))
    bot.render_top5_card=lambda bg_img,item_number,total_items,summary_text,width=1080,height=1920,font_choice=None:render_top5_card(bot,bg_img,item_number,total_items,summary_text,width,height,font_choice,getattr(bot,"_active_script_data",{}))
    old_write=bot.write_script
    def write(*a,**kw):
        result=old_write(*a,**kw); bot._active_script_data=result or {}; return result
    bot.write_script=write
    old_run=bot.run_robot
    def run(web_config=None):
        import sqlite3
        old_connect=sqlite3.connect
        def connect(*a,**kw):
            c=old_connect(*a,**kw); bot._active_scoring_conn=c; return c
        sqlite3.connect=connect
        try:return old_run(web_config=web_config)
        finally:sqlite3.connect=old_connect; bot._active_scoring_conn=None
    bot.run_robot=run
    return bot