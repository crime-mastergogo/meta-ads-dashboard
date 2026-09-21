from pathlib import Path

GEN = Path("generate_weekly.py")
CFG = Path("config.yaml")
OLD = "https://crime-mastergogo.github.io/xyxx-meta-ads-dashboard"
NEW = "https://crime-mastergogo.github.io/meta-ads-dashboard"

if not GEN.exists() or not CFG.exists():
    raise SystemExit("Put this script in the repository root beside generate_weekly.py and config.yaml.")

for p in (GEN, CFG):
    b = p.with_suffix(p.suffix + ".weeklyfix-backup")
    if not b.exists():
        b.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

g = GEN.read_text(encoding="utf-8")
c = CFG.read_text(encoding="utf-8")
changes = []

if OLD in g:
    g = g.replace(OLD, NEW)
    changes.append("Updated old URL in generate_weekly.py")
if OLD in c:
    c = c.replace(OLD, NEW)
    changes.append("Updated old URL in config.yaml")

if "from datetime import datetime, timedelta" not in g:
    g = g.replace("from datetime import datetime", "from datetime import datetime, timedelta", 1)

old = """    generated = data.get("generated_at", "")[:10]
    lookback = data.get("lookback_days", 7)"""
new = """    generated = data.get("generated_at", "")[:10]
    lookback = data.get("lookback_days", 7)
    generated_date = datetime.strptime(generated, "%Y-%m-%d").date()
    period_end = generated_date - timedelta(days=1)
    period_start = period_end - timedelta(days=lookback - 1)
    period_label = (
        f"{period_start.strftime('%d %b %Y')} – "
        f"{period_end.strftime('%d %b %Y')}"
    )"""
if old in g and "period_label = (" not in g:
    g = g.replace(old, new, 1)
    changes.append("Added actual reporting date range")

g = g.replace(
    "Last 7 days rolling · 1D Click Attribution · Updated {generated}",
    "Period {period_label} · 1D Click Attribution · Updated {generated}"
)

g = g.replace(
    "def ad_card(ad, cfg, is_best=False, is_worst=False):",
    "def ad_card(ad, cfg, is_best=False, is_worst=False, preview_height=750):",
    1
)
g = g.replace(
    'f\'<div class="mpw"><iframe src="{preview}" scrolling="yes" allow="autoplay" loading="lazy"></iframe></div>\'',
    'f\'<div class="mpw" style="height:{preview_height}px"><iframe src="{preview}" scrolling="no" allow="autoplay" loading="lazy" style="height:{preview_height}px;overflow:hidden"></iframe></div>\''
)
g = g.replace(
    "else '<div class=\"mpw no-prev\">—</div>'",
    "else f'<div class=\"mpw no-prev\" style=\"height:{preview_height}px\">—</div>'"
)
g = g.replace(
    "def build_tab(categories, cfg, min_purchases=2):",
    "def build_tab(categories, cfg, min_purchases=2, preview_height=750):",
    1
)
g = g.replace("cards += ad_card(ad, cfg, is_best, is_worst)",
              "cards += ad_card(ad, cfg, is_best, is_worst, preview_height)")
g = g.replace("cards += ad_card(best, cfg, True, False)",
              "cards += ad_card(best, cfg, True, False, preview_height)")
g = g.replace("cards += ad_card(worst, cfg, False, True)",
              "cards += ad_card(worst, cfg, False, True, preview_height)")
g = g.replace("s_overview, s_all = build_tab(s_cats, cfg, min_purchases=3)",
              "s_overview, s_all = build_tab(s_cats, cfg, min_purchases=3, preview_height=680)")
g = g.replace("v_overview, v_all = build_tab(v_cats, cfg, min_purchases=2)",
              "v_overview, v_all = build_tab(v_cats, cfg, min_purchases=2, preview_height=750)")

g = g.replace(".mpw{{background:#111;height:750px;overflow:hidden;display:flex;align-items:flex-start;justify-content:center;}}",
              ".mpw{{background:#111;overflow:hidden;display:flex;align-items:flex-start;justify-content:center;}}")
g = g.replace(".mpw iframe{{border:none;width:100%;height:750px;display:block;}}",
              ".mpw iframe{{border:none;width:100%;display:block;overflow:hidden;}}")
g = g.replace(".no-prev{{height:750px;display:flex;align-items:center;justify-content:center;color:#333;font-size:10px;}}",
              ".no-prev{{display:flex;align-items:center;justify-content:center;color:#333;font-size:10px;}}")
g = g.replace(".cgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;}}",
              ".cgrid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;}}@media (max-width:1100px){{.cgrid{{grid-template-columns:repeat(2,minmax(0,1fr));}}}}@media (max-width:700px){{.cgrid{{grid-template-columns:1fr;}}}}")

GEN.write_text(g, encoding="utf-8")
CFG.write_text(c, encoding="utf-8")

print("Fixed weekly report files.")
for x in changes:
    print("✓", x)
print("✓ weekly date range / iframe / 3-column layout applied where matching code was present")
print("✓ backups created with .weeklyfix-backup suffix")
