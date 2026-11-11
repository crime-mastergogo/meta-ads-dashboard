"""
generate_videos.py — Builds docs/videos.html.

Identical structure to statics dashboard:
- Top performers per category by spend
- ★ Best 1D Click ROAS badge
- ⚠ Lowest 1D Click ROAS badge
- Playable preview iframes (MOBILE_FEED_STANDARD format)
- Blended 1D Click ROAS shown per category
"""

import json
import os
import yaml
import re
from taxonomy import ordered_categories, GroupHeaders, slack_group_lines, slack_group_lines_from_ads, unmapped_banner, floor_note, overview_groups, overview_tab_html, group_rollup

BASE_DIR = os.path.dirname(__file__)
INPUT_PATH = os.path.join(BASE_DIR, "data", "videos_daily.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "docs", "videos.html")
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def rc(roas, cfg):
    t = cfg["roas_thresholds"]
    if roas >= t["good"]: return "#22c55e"
    if roas >= t["ok"]:   return "#86efac"
    if roas >= t["warn"]: return "#f59e0b"
    return "#ef4444"


def fmt(v):
    if v >= 100000: return f"₹{v/100000:.1f}L"
    if v >= 1000:   return f"₹{v/1000:.0f}K"
    return f"₹{int(v)}"


def short_name(n):
    for month in ["August2026-","September2026-","July2026-","June2026-","May2026-","Apr2026-"]:
        n = n.replace(month, "")
    for tag in ["-Video-UGC-PA-","-Video-UGC-","-Video-Lofi-","-Video-"]:
        n = n.replace(tag, " · ")
    n = re.sub(r"-\d{2}\.\d{2}\.\d{4}$", "", n)
    return n[:55]


def video_card(ad, cfg, is_best=False, is_worst=False):
    sn = short_name(ad["ad_name"])
    roas_col = rc(ad["roas"], cfg)
    preview = ad.get("preview_url", "")

    preview_html = (
        f'<div class="mpw video-preview"><iframe src="{preview}" scrolling="no" allow="autoplay" loading="lazy"></iframe></div>'
        if preview
        else '<div class="mpw no-prev">Preview unavailable</div>'
    )

    badge = ""
    style = "border:1px solid #252525;background:#161616;"
    if is_best:
        badge = '<div class="badge sbadge">★ Best ROAS</div>'
        style = "border:1px solid rgba(34,197,94,0.35);background:#0f1a0f;"
    elif is_worst:
        badge = '<div class="badge wbadge">⚠ Lowest</div>'
        style = "border:1px solid rgba(239,68,68,0.4);background:#180f0f;"

    return f'''<div class="mc" style="{style}">
      {badge}
      {preview_html}
      <div class="mm">
        <div class="mn">{sn}</div>
        <div class="mmet">
          <span><b>Amount Spent</b><strong>{fmt(ad["spend"])}</strong></span>
          <span><b>Blended ROAS</b><em style="color:{roas_col}">{ad["roas"]:.2f}x</em></span>
          <span><b>Purchase Value</b><strong>{fmt(ad["conv_value"])}</strong></span>
          <span><b>Units Sold</b><strong>{ad["purchases"]:,}</strong></span>
        </div>
      </div>
    </div>'''


def build(data, cfg):
    generated = data.get("generated_at", "")[:10]
    lookback = data.get("lookback_days", 7)
    categories = ordered_categories(data.get("categories", {}))
    gh_overview, gh_all = GroupHeaders(categories), GroupHeaders(categories)

    total_spend = sum(c["total_spend"] for c in categories.values())
    total_ads = sum(c["total_ads"] for c in categories.values())

    # Tab 1: Overview — top 3 + best + worst per category
    tab1 = floor_note(data) + unmapped_banner(data.get("unmapped"))
    for cat, cat_data in categories.items():
        emoji = cat_data.get("emoji", "")
        top_ads = cat_data.get("top_ads", [])
        if not top_ads:
            continue

        qualified = [a for a in top_ads if a["purchases"] >= 2]
        best = max(qualified, key=lambda x: x["roas"]) if qualified else None
        worst = min(qualified, key=lambda x: x["roas"]) if qualified else None

        shown = set()
        cards = ""
        for ad in top_ads[:3]:
            is_best = best and ad["ad_name"] == best["ad_name"]
            is_worst = worst and ad["ad_name"] == worst["ad_name"] and not is_best
            cards += video_card(ad, cfg, is_best, is_worst)
            shown.add(ad["ad_name"])

        if best and best["ad_name"] not in shown:
            cards += video_card(best, cfg, is_best=True, is_worst=False)
            shown.add(best["ad_name"])
        if worst and worst["ad_name"] not in shown:
            cards += video_card(worst, cfg, is_best=False, is_worst=True)

        tab1 += gh_overview.before(cat_data)
        tab1 += f'''<div class="sec">
          <div class="sec-hdr">
            <div>
              <div class="sec-t">{emoji} {cat}</div>
              <div class="sec-s">{cat_data["total_ads"]} ads · {fmt(cat_data["total_spend"])} · {cat_data["blended_roas"]:.2f}x blended ROAS · best {cat_data["best_roas"]:.2f}x</div>
            </div>
          </div>
          <div class="cgrid">{cards}</div>
        </div>'''

    # Tab 2: All top-10 per category
    filter_btns = '<button class="filter-btn active" onclick="filterCat(this,\'all\')">All</button>'
    for cat in categories:
        safe = cat.replace("'", "\\'")
        filter_btns += f'<button class="filter-btn" onclick="filterCat(this,\'{safe}\')">{cat}</button>'

    tab2 = floor_note(data) + f'<div class="filter-bar">{filter_btns}</div>'
    for cat, cat_data in categories.items():
        top_ads = cat_data.get("top_ads", [])
        if not top_ads:
            continue
        qualified = [a for a in top_ads if a["purchases"] >= 2]
        best = max(qualified, key=lambda x: x["roas"]) if qualified else None
        worst = min(qualified, key=lambda x: x["roas"]) if qualified else None
        cards = ""
        for ad in top_ads:
            is_best = best and ad["ad_name"] == best["ad_name"]
            is_worst = worst and ad["ad_name"] == worst["ad_name"] and not is_best
            cards += video_card(ad, cfg, is_best, is_worst)
        safe_cat = cat.replace('"', '&quot;')
        tab2 += gh_all.before(cat_data)
        tab2 += f'''<div class="sec" data-cat="{safe_cat}">
          <div class="sec-hdr">
            <div class="sec-t">{cat_data.get("emoji","")} {cat}</div>
            <div class="sec-s">Top {len(top_ads)} by spend · {fmt(cat_data["total_spend"])} · {cat_data["blended_roas"]:.2f}x ROAS</div>
          </div>
          <div class="cgrid">{cards}</div>
        </div>'''

    # Winter Overview (any group flagged `group_summary` in config.yaml) gets its own tab after Overview.
    ov_group = (overview_groups(categories) or [None])[0]
    ov_html = overview_tab_html(categories, ov_group, lambda ad, kind: video_card(ad, cfg, is_best=(kind == "best")),
                                2, cfg, data.get("card_min_spend")) if ov_group else ""
    tab_labels = ["📊 Overview"]
    if ov_html:
        tab_labels.append(f"{group_rollup(categories)[ov_group]['emoji']} {ov_group}")
    tab_labels += ["🎬 All Videos"]
    tabs_html = "".join(f'<div class="tab{" active" if i == 0 else ""}" onclick="switchTab({i})">{lbl}</div>'
                        for i, lbl in enumerate(tab_labels))
    winter_pane = f'<div class="tab-content" id="tabOverviewGroup">{ov_html}</div>' if ov_html else ""

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>XYXX Videos Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:#0c0c0c;color:#e8e8e8;font-family:"Inter",sans-serif;}}
.hdr{{padding:20px 28px 0;border-bottom:1px solid #1e1e1e;}}
.hdr-row{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:14px;}}
.hdr-title{{font-size:20px;font-weight:700;letter-spacing:-0.02em;}}
.hdr-sub{{font-size:11px;color:#555;font-family:"JetBrains Mono",monospace;}}
.stats{{display:flex;gap:28px;padding:14px 28px;border-bottom:1px solid #1e1e1e;flex-wrap:wrap;}}
.stat-l{{font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#444;margin-bottom:2px;}}
.stat-v{{font-size:18px;font-weight:600;font-variant-numeric:tabular-nums;}}
.tabs{{display:flex;padding:0 28px;}}
.tab{{padding:10px 18px;font-size:12px;font-weight:500;color:#555;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.2s;}}
.tab:hover{{color:#aaa;}}
.tab.active{{color:#e8e8e8;border-bottom-color:#3b82f6;}}
.tab-content{{display:none;padding:24px 28px;}}
.tab-content.active{{display:block;}}
.sec{{margin-bottom:36px;}}
.sec-hdr{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid #1e1e1e;}}
.sec-t{{font-size:15px;font-weight:600;}}
.sec-s{{font-size:11px;color:#555;font-family:"JetBrains Mono",monospace;}}
.cgrid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;}}
.mc{{border-radius:10px;overflow:hidden;display:flex;flex-direction:column;position:relative;}}
.badge{{position:absolute;top:7px;left:7px;z-index:10;padding:2px 6px;border-radius:3px;font-size:9px;font-weight:700;}}
.sbadge{{background:rgba(34,197,94,0.9);color:#000;}}
.wbadge{{background:rgba(239,68,68,0.9);color:#fff;}}
.mpw{{background:#111;overflow:hidden;display:flex;align-items:flex-start;justify-content:center;}}
.video-preview{{height:750px;}}
.video-preview iframe{{border:none;width:100%;height:750px;display:block;overflow:hidden;}}
.no-prev{{height:750px;display:flex;align-items:center;justify-content:center;color:#333;font-size:10px;}}
.mm{{padding:9px 11px 11px;}}
.mn{{font-size:11px;font-weight:600;color:#e0e0e0;line-height:1.3;margin-bottom:5px;}}
.mmet{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 12px;padding-top:9px;border-top:1px solid #1e1e1e;font-family:"JetBrains Mono",monospace;font-size:9.5px;color:#888;}}
.mmet span{{display:flex;flex-direction:column;gap:4px;min-width:0;padding:8px 9px;box-sizing:border-box;border:1px solid #242424;border-radius:6px;background:#111;}}
.mmet b{{font-family:"Inter",sans-serif;font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:#777;white-space:normal;line-height:1.2;overflow-wrap:break-word;}}
.mmet em{{font-style:normal;font-size:12px;font-weight:700;line-height:1.2;overflow-wrap:anywhere;}}
.mmet strong{{font-size:12px;font-weight:700;color:#e8e8e8;}}
@media (max-width: 1100px){{.cgrid{{grid-template-columns:repeat(2,minmax(0,1fr));}}}}
@media (max-width: 700px){{.cgrid{{grid-template-columns:1fr;}}}}
.filter-bar{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px;}}
.filter-btn{{padding:5px 12px;border-radius:16px;border:1px solid #2a2a2a;background:transparent;color:#666;font-size:11px;cursor:pointer;font-family:"Inter",sans-serif;transition:all 0.15s;}}
.filter-btn.active{{background:#3b82f6;border-color:#3b82f6;color:#fff;}}
.nav-links{{display:flex;gap:10px;}}
.nav-link{{font-size:11px;color:#555;text-decoration:none;padding:4px 10px;border:1px solid #2a2a2a;border-radius:5px;}}
.nav-link:hover{{color:#aaa;border-color:#555;}}
</style>
</head>
<body>
<div class="hdr">
  <div class="hdr-row">
    <div>
      <div class="hdr-title">XYXX · Videos Dashboard</div>
      <div class="hdr-sub">Last {lookback} days · Ranked by spend · 1D Click ROAS · Updated {generated}</div>
    </div>
    <div class="nav-links">
      <a class="nav-link" href="index.html">📷 Statics</a>
      <a class="nav-link" href="weekly/">📊 Weekly</a>
      <a class="nav-link" href="monthly/">📅 Monthly</a>
    </div>
  </div>
  <div class="tabs">{tabs_html}</div>
</div>
<div class="stats">
  <div><div class="stat-l">Total Spend</div><div class="stat-v">{fmt(total_spend)}</div></div>
  <div><div class="stat-l">Video Ads</div><div class="stat-v">{total_ads}</div></div>
  <div><div class="stat-l">Categories</div><div class="stat-v">{len(categories)}</div></div>
  <div><div class="stat-l">Ranked by</div><div class="stat-v" style="font-size:13px;padding-top:3px">Spend · 1D Click ROAS</div></div>
</div>
<div class="tab-content active" id="tab0">{tab1}</div>
{winter_pane}
<div class="tab-content" id="tab1">{tab2}</div>
<script>
function switchTab(i){{document.querySelectorAll(".tab").forEach((t,j)=>t.classList.toggle("active",i===j));document.querySelectorAll(".tab-content").forEach((t,j)=>t.classList.toggle("active",i===j));}}
function filterCat(btn,cat){{document.querySelectorAll(".filter-btn").forEach(b=>b.classList.remove("active"));btn.classList.add("active");document.querySelectorAll("#tab1 .sec").forEach(s=>{{s.style.display=(cat==="all"||s.dataset.cat===cat)?"block":"none";}});}}
</script>
</body></html>'''

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"[Videos Dashboard] Written → {OUTPUT_PATH}")


def run():
    cfg = load_config()
    if not os.path.exists(INPUT_PATH):
        print(f"[Videos Dashboard] No data at {INPUT_PATH}, skipping.")
        return
    with open(INPUT_PATH) as f:
        data = json.load(f)
    build(data, cfg)


if __name__ == "__main__":
    run()
