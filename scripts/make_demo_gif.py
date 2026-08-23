"""
scripts/make_demo_gif.py
------------------------
Generates a realistic animated GIF of the Catalog Enrichment Pipeline dashboard.
Renders multiple frames showing: run start → step progress → results → charts.

Run:
    python scripts/make_demo_gif.py
Output:
    docs/demo.gif
"""

from PIL import Image, ImageDraw, ImageFont
import os, math

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H = 1100, 620
OUT  = os.path.join(os.path.dirname(__file__), '..', 'docs', 'demo.gif')

# ── Palette ───────────────────────────────────────────────────────────────────
BG       = (248, 247, 244)
SURFACE  = (255, 255, 255)
SURFACE2 = (242, 240, 236)
BORDER   = (230, 227, 220)
TEXT     = ( 26,  24,  22)
TEXT2    = ( 92,  87,  80)
TEXT3    = (160, 156, 148)
INDIGO   = ( 79,  70, 229)
INDIGOLT = (238, 239, 254)
TEAL     = ( 13, 148, 136)
TEALLT   = (240, 253, 250)
AMBER    = (217, 119,   6)
ROSE     = (225,  29,  72)
GREEN    = ( 22, 163,  74)
GREENLT  = (240, 253, 244)
PURPLE   = (124,  58, 237)
GRAY     = (100, 100, 100)

# ── Font helpers ──────────────────────────────────────────────────────────────
def font(size=13, bold=False):
    """Return best available font."""
    candidates = [
        '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/System/Library/Fonts/SFNSDisplay.ttf',
        '/Library/Fonts/Arial.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

F10  = font(10)
F11  = font(11)
F12  = font(12)
F13  = font(13)
F14  = font(14)
F15  = font(15)
F16  = font(16)
F18  = font(18)
F22  = font(22)
F28  = font(28)

# ── Drawing helpers ───────────────────────────────────────────────────────────

def rounded_rect(draw, xy, r, fill, outline=None, width=1):
    x0,y0,x1,y1 = xy
    r = min(r, (x1-x0)//2, max(1,(y1-y0)//2))
    if x1-x0 < 2 or y1-y0 < 2:
        draw.rectangle([x0,y0,x1,y1], fill=fill)
        return
    draw.rectangle([x0+r,y0,x1-r,y1], fill=fill)
    draw.rectangle([x0,y0+r,x1,y1-r], fill=fill)
    draw.ellipse([x0,y0,x0+2*r,y0+2*r], fill=fill)
    draw.ellipse([x1-2*r,y0,x1,y0+2*r], fill=fill)
    draw.ellipse([x0,y1-2*r,x0+2*r,y1], fill=fill)
    draw.ellipse([x1-2*r,y1-2*r,x1,y1], fill=fill)
    if outline:
        draw.arc([x0,y0,x0+2*r,y0+2*r], 180,270,fill=outline,width=width)
        draw.arc([x1-2*r,y0,x1,y0+2*r], 270,360,fill=outline,width=width)
        draw.arc([x0,y1-2*r,x0+2*r,y1], 90,180,fill=outline,width=width)
        draw.arc([x1-2*r,y1-2*r,x1,y1], 0,90,fill=outline,width=width)
        draw.line([x0+r,y0,x1-r,y0],fill=outline,width=width)
        draw.line([x0+r,y1,x1-r,y1],fill=outline,width=width)
        draw.line([x0,y0+r,x0,y1-r],fill=outline,width=width)
        draw.line([x1,y0+r,x1,y1-r],fill=outline,width=width)

def card(draw, xy, fill=SURFACE, outline=BORDER):
    rounded_rect(draw, xy, 8, fill, outline)

def pill(draw, xy, text, bg, fg, fnt=F11):
    x0,y0,x1,y1 = xy
    rounded_rect(draw, xy, (y1-y0)//2, bg)
    cx = (x0+x1)//2
    cy = (y0+y1)//2
    tw,th = draw.textlength(text, font=fnt), fnt.size
    draw.text((cx-tw/2, cy-th/2), text, fill=fg, font=fnt)

def dot(draw, cx, cy, r, color):
    draw.ellipse([cx-r,cy-r,cx+r,cy+r], fill=color)

def hbar(draw, x,y,w,h, pct, bg=BORDER, fg=INDIGO, r=3):
    """Horizontal progress bar."""
    rounded_rect(draw, [x,y,x+w,y+h], r, bg)
    fill_w = max(r*2, int(w*pct/100))
    rounded_rect(draw, [x,y,x+fill_w,y+h], r, fg)

def score_color(s):
    if s >= 80: return GREEN
    if s >= 50: return AMBER
    return ROSE

# ── Base frame (topbar + sidebar always present) ──────────────────────────────

def draw_base(draw):
    # Background
    draw.rectangle([0,0,W,H], fill=BG)

    # ── Topbar ──
    draw.rectangle([0,0,W,46], fill=SURFACE)
    draw.line([0,46,W,46], fill=BORDER, width=1)

    # Logo icon
    rounded_rect(draw, [16,8,42,38], 8, INDIGO)
    draw.text((22,14), "📦", fill=(255,255,255), font=F16)

    # Logo text
    draw.text((50,14), "Catalog Enrichment", fill=TEXT, font=F15)
    draw.text((218,16), "/ Pipeline", fill=TEXT3, font=F13)

    # Nav links
    draw.text((W-300,15), "API Docs", fill=TEXT2, font=F13)
    draw.text((W-220,15), "ReDoc", fill=TEXT2, font=F13)

    # Ollama status pill
    rounded_rect(draw, [W-150,10,W-12,36], 13, SURFACE2, BORDER)
    dot(draw, W-138, 23, 4, TEAL)
    draw.text((W-130,14), "Ollama · llama3.2", fill=TEXT2, font=F11)

    # ── Left sidebar ──
    card(draw, [12,54,288,520])

    # Sidebar header
    draw.text((24,66), "⚡  Run Pipeline", fill=TEXT, font=F14)

    # File chips
    draw.text((24,96), "Supplier files detected", fill=TEXT2, font=F11)
    chips = [
        ("📊", "supplier_a.csv",  "CSV",  (209,250,229),(  6, 95, 70)),
        ("📋", "supplier_b.json", "JSON", (219,234,254),( 30, 58,138)),
        ("📄", "supplier_c.txt",  "TXT",  (254,243,199),(146, 64, 14)),
    ]
    for i,(icon,name,ext,ibg,ifg) in enumerate(chips):
        y = 114 + i*28
        rounded_rect(draw, [24,y,276,y+22], 5, SURFACE2, BORDER)
        rounded_rect(draw, [28,y+3,48,y+19], 4, ibg)
        draw.text((32,y+5), icon, fill=ifg, font=F11)
        draw.text((54,y+5), name, fill=TEXT, font=F11)
        rounded_rect(draw, [238,y+5,270,y+17], 3, BORDER)
        draw.text((244,y+6), ext, fill=TEXT3, font=F10)

    # Divider
    draw.line([24,202,276,202], fill=BORDER, width=1)

    # Mock toggle
    draw.text((24,212), "Mock LLM mode", fill=TEXT, font=F12)
    draw.text((24,228), "Run without Ollama — instant stubs", fill=TEXT3, font=F10)
    # Toggle off
    rounded_rect(draw, [240,212,278,230], 9, BORDER)
    dot(draw, 250, 221, 7, SURFACE)

    # Run button
    rounded_rect(draw, [24,246,276,270], 6, INDIGO)
    draw.text((90,253), "▶  Run Enrichment Pipeline", fill=(255,255,255), font=F13)

    # Recent jobs label
    draw.text((24,340), "🕑  Recent Jobs", fill=TEXT, font=F14)

    # Quick guide card
    card(draw, [12,440,288,518], fill=INDIGOLT, outline=(199,210,254))
    draw.text((24,452), "💡  Quick Guide", fill=INDIGO, font=F13)
    steps = ["1. Ollama running (green dot)", "2. Upload or use sample files",
             "3. Click Run Pipeline","4. View charts in Overview tab"]
    for i,s in enumerate(steps):
        draw.text((24,470+i*12), s, fill=TEXT2, font=F10)

def draw_sidebar_jobs(draw, jobs):
    """Draw recent jobs list in sidebar."""
    for i,(jid,status,skus,avg) in enumerate(jobs):
        y = 358+i*22
        if y > 430: break
        scol = {'complete':GREEN,'running':(29,78,216),'queued':TEXT3,'failed':ROSE}[status]
        sbg  = {'complete':GREENLT,'running':(219,234,254),'queued':SURFACE2,'failed':(255,241,242)}[status]
        pill(draw, [24,y,90,y+16], status, sbg, scol, F10)
        draw.text((96,y+2), jid, fill=TEXT3, font=F10)
        if skus:
            draw.text((170,y+2), f"{skus} SKUs · avg {avg}", fill=TEXT2, font=F10)

# ── Frame builder functions ────────────────────────────────────────────────────

def frame_idle():
    img = Image.new('RGB', (W,H), BG)
    d   = ImageDraw.Draw(img)
    draw_base(d)
    draw_sidebar_jobs(d, [])

    # Main panel — empty state
    card(d, [296,54,W-12,H-12])
    cx, cy = (296+W-12)//2, (54+H-12)//2
    d.text((cx-20,cy-50), "🗂️", fill=TEXT3, font=F28)
    d.text((cx-130,cy+2), "Your enriched catalog will appear here", fill=TEXT2, font=F14)
    d.text((cx-160,cy+24),"Run the pipeline using the controls on the left.", fill=TEXT3, font=F12)
    d.text((cx-170,cy+40),"Results load automatically once all 8 agents complete.", fill=TEXT3, font=F12)
    return img


def frame_running(step, pct, msg, elapsed):
    img = Image.new('RGB', (W,H), BG)
    d   = ImageDraw.Draw(img)
    draw_base(d)
    draw_sidebar_jobs(d, [("c74367d7","running",None,None)])

    # Override run button to show spinner state
    rounded_rect(d, [24,246,276,270], 6, (99,91,199))
    d.text((80,253), "⏳  Running pipeline…", fill=(255,255,255), font=F13)

    # Progress section in sidebar
    d.text((24,280), msg, fill=TEXT2, font=F11)
    d.text((256,280), f"{pct}%", fill=INDIGO, font=F11)

    # 8 step dots
    for i in range(8):
        x = 24+i*32
        col = INDIGO if i < step else (SURFACE if i==step else BORDER)
        rounded_rect(d, [x,296,x+28,300], 2, col)

    # Progress bar
    hbar(d, 24,306,252,5, pct, BORDER, INDIGO)

    # Step label
    step_names = ["Ingest","Normalize","Deduplicate","Map Schema",
                  "Fill Gaps","Describe","Score","Report"]
    label = f"Step {step+1}/8 — {step_names[min(step,7)]}…"
    d.text((24,316), label, fill=TEXT3, font=F10)
    d.text((220,316), f"{elapsed}s", fill=TEXT3, font=F10)

    # Main panel
    card(d, [296,54,W-12,H-12])
    cx = (296+W-12)//2
    d.text((cx-140,200), "⚡  Pipeline running…", fill=TEXT, font=F18)
    d.text((cx-180,232), "Agents are processing your supplier feeds in real time.", fill=TEXT2, font=F13)

    # Agent progress list
    agents = [
        ("IngestionAgent",       step>0),
        ("NormalizationAgent",   step>1),
        ("DeduplicationAgent",   step>2),
        ("SchemaMappingAgent",   step>3),
        ("GapResolutionAgent",   step>4),
        ("DescriptionGenAgent",  step>5),
        ("QualityScoringAgent",  step>6),
        ("ReportingAgent",       step>7),
    ]
    for i,(name,done) in enumerate(agents):
        y = 270+i*28
        x = 320
        col  = TEAL if done else (INDIGO if i==step else TEXT3)
        icon = "✓" if done else ("●" if i==step else "○")
        d.text((x,y), icon, fill=col, font=F13)
        d.text((x+20,y), name, fill=TEXT if done else TEXT3, font=F12)
        if done:
            d.text((x+200,y), "complete", fill=TEAL, font=F11)
        elif i==step:
            d.text((x+200,y), "running…", fill=INDIGO, font=F11)

    return img


def frame_results():
    img = Image.new('RGB', (W,H), BG)
    d   = ImageDraw.Draw(img)
    draw_base(d)
    draw_sidebar_jobs(d, [("c74367d7","complete",18,87.8)])

    # Reset run button
    rounded_rect(d, [24,246,276,270], 6, INDIGO)
    d.text((90,253), "▶  Run Enrichment Pipeline", fill=(255,255,255), font=F13)

    # Main panel
    card(d, [296,54,W-12,H-12])

    # KPI tiles
    kpis = [("18","Total SKUs",""), ("87.8","Avg Score","c-indigo"),
            ("18","Score ≥ 80","c-teal"), ("0","Duplicates",""), ("0","Need Review","")]
    kpi_cols = [(TEXT,TEXT2),(INDIGO,TEXT2),(TEAL,TEXT2),(TEXT,TEXT2),(TEXT,TEXT2)]
    for i,(val,lbl,_) in enumerate(kpis):
        x = 308+i*148
        rounded_rect(d, [x,62,x+140,96], 7, SURFACE, BORDER)
        d.text((x+12,66), val, fill=kpi_cols[i][0], font=F22)
        d.text((x+12,88), lbl, fill=TEXT3, font=F10)

    # Tab bar
    tabs = ["📊 Overview","📋 Products","👤 Review","📄 Report"]
    for i,t in enumerate(tabs):
        x = 308+i*110
        col = INDIGO if i==0 else TEXT3
        d.text((x,106), t, fill=col, font=F13)
        if i==0:
            d.line([x,122,x+80,122], fill=INDIGO, width=2)
    d.line([308,124,W-20,124], fill=BORDER, width=1)

    # ── 6 mini charts in 3×2 grid ──
    gx, gy = 308, 132
    gw, gh = (W-12-gx)//3 - 8, 150

    chart_data = [
        ("🏆 Top 5 Quality Scores",   "top5"),
        ("📈 Score Distribution",      "dist"),
        ("🏭 Supplier Avg Score",       "supplier"),
        ("🕸️ Field Completeness",       "radar"),
        ("⚡ Before vs After",          "before_after"),
        ("🗂️ SKUs by Category",         "category"),
    ]

    for i,(title,kind) in enumerate(chart_data):
        col = i%3
        row = i//3
        x0 = gx + col*(gw+8)
        y0 = gy + row*(gh+8)
        x1, y1 = x0+gw, y0+gh

        rounded_rect(d, [x0,y0,x1,y1], 7, SURFACE2, BORDER)
        d.text((x0+8,y0+7), title, fill=TEXT2, font=F10)

        cx0,cy0,cx1,cy1 = x0+8,y0+22,x1-8,y1-8
        cw = cx1-cx0
        ch = cy1-cy0

        if kind=="top5":
            products = [
                ("Running Shoes",92,GREEN),("Yoga Pants",92,GREEN),
                ("Slim Fit Jeans",92,GREEN),("Bamboo Board",90,GREEN),("Mens Jeans",88,GREEN),
            ]
            for j,(name,score,col2) in enumerate(products):
                by = cy0+j*(ch//5)+2
                bh = ch//5-3
                # label
                d.text((cx0,by+1), name[:14], fill=TEXT2, font=F10)
                # bar
                bx = cx0+70
                bw = int((cw-74)*score/100)
                rounded_rect(d,[bx,by,bx+bw,by+bh],3,col2)
                d.text((bx+bw+3,by+1),str(score),fill=col2,font=F10)

        elif kind=="dist":
            # Doughnut simulation
            cx2,cy2 = (cx0+cx1)//2,(cy0+cy1)//2
            r=min(cw,ch)//2-8
            # outer
            d.ellipse([cx2-r,cy2-r,cx2+r,cy2+r], fill=(220,220,220))
            # slices (all green = 100%)
            d.pieslice([cx2-r,cy2-r,cx2+r,cy2+r], -90, 270, fill=GREEN)
            d.pieslice([cx2-r,cy2-r,cx2+r,cy2+r], 170, 200, fill=AMBER)
            # inner hole
            d.ellipse([cx2-r//2,cy2-r//2,cx2+r//2,cy2+r//2], fill=SURFACE2)
            d.text((cx2-8,cy2-7),"18",fill=TEXT,font=F12)
            # legend
            d.ellipse([cx0,cy1-14,cx0+8,cy1-6],fill=GREEN)
            d.text((cx0+10,cy1-15),"High",fill=TEXT3,font=F10)
            d.ellipse([cx0+40,cy1-14,cx0+48,cy1-6],fill=AMBER)
            d.text((cx0+50,cy1-15),"Mid",fill=TEXT3,font=F10)

        elif kind=="supplier":
            bars = [("SUPPLIER_A",87.5,INDIGO,8),("SUPPLIER_B",88.4,TEAL,5),("SUPPLIER_C",87.2,AMBER,5)]
            bw2  = (cw-20)//(len(bars))
            for j,(name,val,col2,n) in enumerate(bars):
                bx = cx0+j*bw2+4
                bh2= int(ch*(val/100))
                by = cy1-bh2
                rounded_rect(d,[bx,by,bx+bw2-6,cy1],4,col2)
                d.text((bx,cy0+2),name.replace("SUPPLIER_","S"),fill=TEXT3,font=F10)
                d.text((bx,by-12),str(val),fill=col2,font=F10)

        elif kind=="radar":
            # Spider chart outline
            cx2,cy2=(cx0+cx1)//2,(cy0+cy1)//2
            r=min(cw,ch)//2-10
            n=10
            pts_outer=[(cx2+r*math.cos(2*math.pi*k/n-math.pi/2),
                        cy2+r*math.sin(2*math.pi*k/n-math.pi/2)) for k in range(n)]
            scores=[100,100,94,100,89,72,61,56,44,100]
            pts_data=[(cx2+r*(s/100)*math.cos(2*math.pi*k/n-math.pi/2),
                       cy2+r*(s/100)*math.sin(2*math.pi*k/n-math.pi/2)) for k,s in enumerate(scores)]
            # grid rings
            for ring in [0.25,0.5,0.75,1.0]:
                rpts=[(cx2+r*ring*math.cos(2*math.pi*k/n-math.pi/2),
                       cy2+r*ring*math.sin(2*math.pi*k/n-math.pi/2)) for k in range(n)]
                d.polygon(rpts, outline=(210,210,210))
            # spoke lines
            for px,py in pts_outer:
                d.line([cx2,cy2,px,py], fill=(210,210,210), width=1)
            # data polygon
            d.polygon(pts_data, fill=(79,70,229,60), outline=INDIGO)
            labels=["Name","Cat","Price","Desc","Color","Mat","Dim","Wt","Brand","SEO"]
            for k,(px,py) in enumerate(pts_outer):
                d.text((px-8,py-5),labels[k],fill=TEXT3,font=F10)

        elif kind=="before_after":
            fields=["Price","Desc","Cat","Color","Mat"]
            before=[72,17,72,78,33]
            after =[94,100,100,89,78]
            bw3=(cw-10)//(len(fields))
            for j,(f,bef,aft) in enumerate(zip(fields,before,after)):
                bx=cx0+j*bw3+2
                # before bar
                bh_b=int(ch*bef/100)
                rounded_rect(d,[bx,cy1-bh_b,bx+bw3//2-2,cy1],3,(168,162,158,180))
                # after bar
                bh_a=int(ch*aft/100)
                rounded_rect(d,[bx+bw3//2,cy1-bh_a,bx+bw3-3,cy1],3,INDIGO)
                d.text((bx+2,cy1+1),f,fill=TEXT3,font=F10)

        elif kind=="category":
            cats=[("Electronics",8,INDIGO),("Apparel",5,TEAL),
                  ("Home & Kitchen",3,AMBER),("Sports",2,ROSE)]
            bw4=(cw-8)//(len(cats))
            mx=max(c[1] for c in cats)
            for j,(name,cnt,col2) in enumerate(cats):
                bx=cx0+j*bw4+4
                bh4=int(ch*cnt/mx)
                rounded_rect(d,[bx,cy1-bh4,bx+bw4-6,cy1],3,col2)
                d.text((bx,cy1+1),name[:5],fill=TEXT3,font=F10)
                d.text((bx+2,cy1-bh4-12),str(cnt),fill=col2,font=F10)

    # Enrichment summary strip
    sy = gy + 2*(gh+8) + 4
    if sy+38 < H-12:
        rounded_rect(d, [gx,sy,W-20,sy+36], 7, SURFACE2, BORDER)
        items = [("🌐","4 translated"),("✏️","18 descriptions"),
                 ("🔧","15 auto-filled"),("🔗","0 duplicates"),
                 ("⚠️","5 flags"),("🏷️","18 SEO tags")]
        for i,(icon,lbl) in enumerate(items):
            x = gx+10+i*((W-40-gx)//6)
            d.text((x,sy+4),icon,fill=TEXT2,font=F12)
            d.text((x,sy+19),lbl,fill=TEXT2,font=F10)

    return img


# ── Assemble frames ────────────────────────────────────────────────────────────

frames = []
durations = []

def add(img, ms):
    frames.append(img)
    durations.append(ms)

# Scene 1: idle dashboard (2s)
for _ in range(4):
    add(frame_idle(), 500)

# Scene 2: pipeline starting + progress through 8 steps
step_msgs = [
    "Step 1/8 — Ingesting supplier files…",
    "Step 2/8 — Normalising 18 products…",
    "Step 3/8 — Deduplicating…",
    "Step 4/8 — Mapping to schema…",
    "Step 5/8 — Resolving gaps with LLM…",
    "Step 6/8 — Generating descriptions…",
    "Step 7/8 — Scoring products…",
    "Step 8/8 — Writing output files…",
]
step_pcts = [10, 25, 40, 53, 65, 80, 91, 97]
elapsed = 0
for i,(msg,pct) in enumerate(zip(step_msgs,step_pcts)):
    elapsed += [1,2,2,1,3,15,3,1][i]
    add(frame_running(i, pct, msg, elapsed), 600)

# Step complete
add(frame_running(8, 100, "Complete ✓", elapsed+1), 800)

# Scene 3: results dashboard (hold for 5s)
for _ in range(10):
    add(frame_results(), 500)

# Scene 4: hold on results 2 more seconds then loop
for _ in range(4):
    add(frame_results(), 500)

# ── Save ──────────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
frames[0].save(
    OUT,
    save_all=True,
    append_images=frames[1:],
    duration=durations,
    loop=0,          # loop forever
    optimize=True,
)
print(f"✔  Saved {len(frames)} frames → {os.path.abspath(OUT)}")
size_kb = os.path.getsize(OUT)//1024
print(f"   File size: {size_kb} KB")
