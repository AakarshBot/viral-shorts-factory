"""Dashboard visual rendering helpers and the supported runtime patch surface."""
import random, re, sys, traceback
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance


def install_safe_exception_hook():
    def hook(exctype, value, tb):
        print("💥 UNCAUGHT EXCEPTION DETECTED:")
        traceback.print_exception(exctype, value, tb)
    sys.excepthook = hook


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