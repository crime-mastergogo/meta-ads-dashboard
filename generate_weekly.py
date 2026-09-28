"""
generate_weekly.py — Builds weekly visual deck + sends Slack.

Full deck at docs/weekly/YYYY-WW.html with:
  Tab 1: Statics — top performers per category, 750px previews, ★/⚠ badges
  Tab 2: Videos  — identical structure, playable previews, ranked by spend
Slack notification links to this deck.
"""

import json
import os
import re
import yaml
import requests
from datetime import datetime, date, timedelta

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
STATICS_PATH = os.path.join(BASE_DIR, "data", "statics_daily.json")
VIDEOS_PATH  = os.path.join(BASE_DIR, "data", "videos_daily.json")
DOCS_DIR = os.path.join(BASE_DIR, "docs", "weekly")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def fmt(v):
    if v >= 10000000: return f"₹{v/10000000:.2f}Cr"
    if v >= 100000:   return f"₹{v/100000:.1f}L"
    if v >= 1000:     return f"₹{v/1000:.0f}K"
    return f"₹{int(v)}"


def rc(roas, cfg):
    t = cfg["roas_thresholds"]
    if roas >= t["good"]: return "#22c55e"
    if roas >= t["ok"]:   return "#86efac"
    if roas >= t["warn"]: return "#f59e0b"
    return "#ef4444"


def short_name(n):
    for month in ["August2026-","September2026-","July2026-","June2026-","May2026-","Apr2026-"]:
        n = n.replace(month, "")
    for tag in ["-Static-Creative-Flatlay-Internal","-Static-Model-Internal",
                "-Static-Product-Romance-Internal","-Video-UGC-PA-","-Video-UGC-","-Video-"]:
        n = n.replace(tag, " · ")
    n = re.sub(r"-\d{2}\.\d{2}\.\d{4}$", "", n)
    return n[:52]


def ad_card(ad, cfg, is_best=False, is_worst=False, preview_height=750):
    """Weekly card with a format-specific, fixed-height Meta preview."""
    sn = short_name(ad["ad_name"])
    roas_col = rc(ad["roas"], cfg)
    preview = ad.get("preview_url", "")

    preview_html = (
        f'<div class="mpw" style="height:{preview_height}px">'
        f'<iframe src="{preview}" scrolling="no" allow="autoplay" loading="lazy" '
        f'style="height:{preview_height}px;overflow:hidden"></iframe></div>'
        if preview
        else f'<div class="mpw no-prev" style="height:{preview_height}px">—</div>'
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
          <span>{fmt(ad["spend"])}</span>
          <span style="color:{roas_col}">{ad["roas"]:.2f}x</span>
          <span>{fmt(ad["conv_value"])}</span>
          <span>{ad["purchases"]:,}</span>
        </div>
      </div>
    </div>'''


def build_tab(categories, cfg, min_purchases=2, preview_height=750):
    """Build overview + all-ads sections for one format (statics or videos)."""
    # Overview: top 3 + best ROAS + worst per category
    overview = ""
    for cat, cat_data in categories.items():
        emoji = cat_data.get("emoji", "")
        top_ads = cat_data.get("top_ads", [])
        if not top_ads:
            continue
        qualified = [a for a in top_ads if a["purchases"] >= min_purchases]
        best  = max(qualified, key=lambda x: x["roas"]) if qualified else None
        worst = min(qualified, key=lambda x: x["roas"]) if qualified else None

        shown = set()
        cards = ""
        for ad in top_ads[:3]:
            is_best  = best  and ad["ad_name"] == best["ad_name"]
            is_worst = worst and ad["ad_name"] == worst["ad_name"] and not is_best
            cards += ad_card(ad, cfg, is_best, is_worst, preview_height)
            shown.add(ad["ad_name"])
        if best and best["ad_name"] not in shown:
            cards += ad_card(best, cfg, True, False, preview_height)
            shown.add(best["ad_name"])
        if worst and worst["ad_name"] not in shown:
            cards += ad_card(worst, cfg, False, True, preview_height)

        blended = cat_data.get("blended_roas", 0)
        overview += f'''<div class="sec">
          <div class="sec-hdr">
            <div>
              <div class="sec-t">{emoji} {cat}</div>
              <div class="sec-s">{cat_data["total_ads"]} ads · {fmt(cat_data["total_spend"])} · {blended:.2f}x blended ROAS</div>
            </div>
          </div>
          <div class="cgrid">{cards}</div>
        </div>'''

    # All ads section with filter
    filter_btns = '<button class="filter-btn active" onclick="filterCat(this,\'all\',this.closest(\'.tab-pane\'))">All</button>'
    for cat in categories:
        safe = cat.replace("'", "\\'")
        filter_btns += f'<button class="filter-btn" onclick="filterCat(this,\'{safe}\',this.closest(\'.tab-pane\'))">{cat}</button>'

    all_ads_html = f'<div class="filter-bar">{filter_btns}</div>'
    for cat, cat_data in categories.items():
        top_ads = cat_data.get("top_ads", [])
        if not top_ads: continue
        qualified = [a for a in top_ads if a["purchases"] >= min_purchases]
        best  = max(qualified, key=lambda x: x["roas"]) if qualified else None
        worst = min(qualified, key=lambda x: x["roas"]) if qualified else None
        cards = ""
        for ad in top_ads:
            is_best  = best  and ad["ad_name"] == best["ad_name"]
            is_worst = worst and ad["ad_name"] == worst["ad_name"] and not is_best
            cards += ad_card(ad, cfg, is_best, is_worst, preview_height)
        safe_cat = cat.replace('"', '&quot;')
        all_ads_html += f'''<div class="sec" data-cat="{safe_cat}">
          <div class="sec-hdr">
            <div class="sec-t">{cat_data.get("emoji","")} {cat}</div>
            <div class="sec-s">Top {len(top_ads)} by spend · {fmt(cat_data["total_spend"])} · {cat_data.get("blended_roas",0):.2f}x ROAS</div>
          </div>
          <div class="cgrid">{cards}</div>
        </div>'''

    return overview, all_ads_html


def build():
    cfg = load_config()
    pages_base = cfg.get("pages_base_url", "")
    now = datetime.utcnow()
    week_str = now.strftime("%Y-W%W")
    date_str = now.strftime("%d %b %Y")

    statics_data, videos_data = None, None
    if os.path.exists(STATICS_PATH):
        with open(STATICS_PATH) as f: statics_data = json.load(f)
    if os.path.exists(VIDEOS_PATH):
        with open(VIDEOS_PATH) as f: videos_data = json.load(f)

    s_cats = (statics_data or {}).get("categories", {})
    v_cats = (videos_data  or {}).get("categories", {})
    lookback = (statics_data or {}).get("lookback_days", 7)

    # Show the previous complete reporting window, based on the data timestamp.
    generated_value = (
        (statics_data or {}).get("generated_at")
        or (videos_data or {}).get("generated_at")
        or datetime.utcnow().isoformat()
    )
    try:
        data_date = datetime.fromisoformat(str(generated_value).replace("Z", "+00:00")).date()
    except ValueError:
        data_date = datetime.utcnow().date()
    period_end = data_date - timedelta(days=1)
    period_start = period_end - timedelta(days=max(int(lookback), 1) - 1)
    period_label = f"{period_start.strftime('%d %b %Y')} – {period_end.strftime('%d %b %Y')}"

    total_s = sum(c["total_spend"] for c in s_cats.values())
    total_v = sum(c["total_spend"] for c in v_cats.values())

    s_overview, s_all = build_tab(s_cats, cfg, min_purchases=3, preview_height=680)
    v_overview, v_all = build_tab(v_cats, cfg, min_purchases=2, preview_height=750)

    # Best performers for Slack
    all_s_ads = [a for c in s_cats.values() for a in c.get("top_ads",[])]
    all_v_ads = [a for c in v_cats.values() for a in c.get("top_ads",[])]
    best_static = max(all_s_ads, key=lambda x: x["roas"]) if all_s_ads else None
    best_video  = max(all_v_ads, key=lambda x: x["roas"]) if all_v_ads else None
    top_static_spend = max(all_s_ads, key=lambda x: x["spend"]) if all_s_ads else None

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>XYXX Weekly Report · {week_str}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:#0c0c0c;color:#e8e8e8;font-family:"Inter",sans-serif;}}
.cover{{padding:40px 32px;border-bottom:1px solid #1e1e1e;}}
.cover-eye{{font-size:11px;letter-spacing:0.15em;text-transform:uppercase;color:#555;margin-bottom:8px;}}
.cover-title{{font-size:30px;font-weight:700;letter-spacing:-0.02em;margin-bottom:4px;}}
.cover-sub{{font-size:12px;color:#555;margin-bottom:24px;}}
.cover-stats{{display:flex;gap:28px;flex-wrap:wrap;padding-top:20px;border-top:1px solid #1e1e1e;}}
.cs-l{{font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#444;margin-bottom:3px;}}
.cs-v{{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;}}
.nav-links{{display:flex;gap:10px;margin-top:16px;}}
.nav-link{{font-size:11px;color:#555;text-decoration:none;padding:4px 10px;border:1px solid #2a2a2a;border-radius:5px;}}
.nav-link:hover{{color:#aaa;}}
.format-tabs{{display:flex;padding:0 32px;border-bottom:1px solid #1e1e1e;background:#0f0f0f;}}
.ftab{{padding:14px 22px;font-size:13px;font-weight:600;color:#555;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.2s;}}
.ftab:hover{{color:#aaa;}}
.ftab.active{{color:#e8e8e8;border-bottom-color:#3b82f6;}}
.tab-pane{{display:none;}}
.tab-pane.active{{display:block;}}
.inner-tabs{{display:flex;padding:0 32px;border-bottom:1px solid #1e1e1e;}}
.itab{{padding:10px 16px;font-size:11px;font-weight:500;color:#555;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.2s;}}
.itab:hover{{color:#aaa;}}
.itab.active{{color:#e8e8e8;border-bottom-color:#6366f1;}}
.inner-content{{display:none;padding:24px 32px;}}
.inner-content.active{{display:block;}}
.sec{{margin-bottom:32px;}}
.sec-hdr{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:10px;padding-bottom:9px;border-bottom:1px solid #1e1e1e;}}
.sec-t{{font-size:14px;font-weight:600;}}
.sec-s{{font-size:11px;color:#555;font-family:"JetBrains Mono",monospace;}}
.cgrid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;}}
@media (max-width:1100px){{.cgrid{{grid-template-columns:repeat(2,minmax(0,1fr));}}}}
@media (max-width:700px){{.cgrid{{grid-template-columns:1fr;}}}}
.mc{{border-radius:10px;overflow:hidden;display:flex;flex-direction:column;position:relative;}}
.badge{{position:absolute;top:7px;left:7px;z-index:10;padding:2px 6px;border-radius:3px;font-size:9px;font-weight:700;}}
.sbadge{{background:rgba(34,197,94,0.9);color:#000;}}
.wbadge{{background:rgba(239,68,68,0.9);color:#fff;}}
.mpw{{background:#111;overflow:hidden;display:flex;align-items:flex-start;justify-content:center;}}
.mpw iframe{{border:none;width:100%;display:block;overflow:hidden;}}
.no-prev{{display:flex;align-items:center;justify-content:center;color:#333;font-size:10px;}}
.mm{{padding:9px 11px 11px;}}
.mn{{font-size:11px;font-weight:600;color:#e0e0e0;line-height:1.3;margin-bottom:5px;}}
.mmet{{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:3px;padding-top:6px;border-top:1px solid #1e1e1e;font-family:"JetBrains Mono",monospace;font-size:9.5px;color:#888;}}
.filter-bar{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;}}
.filter-btn{{padding:5px 12px;border-radius:16px;border:1px solid #2a2a2a;background:transparent;color:#666;font-size:11px;cursor:pointer;font-family:"Inter",sans-serif;transition:all 0.15s;}}
.filter-btn.active{{background:#3b82f6;border-color:#3b82f6;color:#fff;}}
.footer{{padding:18px 32px;border-top:1px solid #1a1a1a;font-size:10px;color:#333;font-family:"JetBrains Mono",monospace;}}
</style>
</head>
<body>
<div class="cover">
  <div class="cover-eye">XYXX Crew · Weekly Creative Report</div>
  <div class="cover-title">{week_str}</div>
  <div class="cover-sub">Period {period_label} · 1D Click Attribution · Updated {date_str}</div>
  <div class="cover-stats">
    <div><div class="cs-l">Statics Spend</div><div class="cs-v">{fmt(total_s)}</div></div>
    <div><div class="cs-l">Videos Spend</div><div class="cs-v">{fmt(total_v)}</div></div>
    <div><div class="cs-l">Total</div><div class="cs-v">{fmt(total_s+total_v)}</div></div>
  </div>
  <div class="nav-links">
    <a class="nav-link" href="../index.html">📷 Statics Dashboard</a>
    <a class="nav-link" href="../videos.html">📹 Videos Dashboard</a>
    <a class="nav-link" href="index.html">📅 All Weekly Reports</a>
  </div>
</div>

<div class="format-tabs">
  <div class="ftab active" onclick="switchFormat(0)">📷 Statics</div>
  <div class="ftab" onclick="switchFormat(1)">📹 Videos</div>
</div>

<!-- STATICS PANE -->
<div class="tab-pane active" id="fmt0">
  <div class="inner-tabs">
    <div class="itab active" onclick="switchInner(0,0)">Overview</div>
    <div class="itab" onclick="switchInner(0,1)">All Ads</div>
  </div>
  <div class="inner-content active" id="s-inner0">{s_overview}</div>
  <div class="inner-content" id="s-inner1">{s_all}</div>
</div>

<!-- VIDEOS PANE -->
<div class="tab-pane" id="fmt1">
  <div class="inner-tabs">
    <div class="itab active" onclick="switchInner(1,0)">Overview</div>
    <div class="itab" onclick="switchInner(1,1)">All Videos</div>
  </div>
  <div class="inner-content active" id="v-inner0">{v_overview}</div>
  <div class="inner-content" id="v-inner1">{v_all}</div>
</div>

<div class="footer">XYXX Crew · {week_str} · Auto-generated by GitHub Actions · 1D Click</div>
<script>
function switchFormat(i){{
  document.querySelectorAll(".ftab").forEach((t,j)=>t.classList.toggle("active",i===j));
  document.querySelectorAll(".tab-pane").forEach((t,j)=>t.classList.toggle("active",i===j));
}}
function switchInner(fmt,i){{
  var prefix=fmt===0?"s":"v";
  var pane=document.getElementById("fmt"+fmt);
  pane.querySelectorAll(".itab").forEach((t,j)=>t.classList.toggle("active",i===j));
  pane.querySelectorAll(".inner-content").forEach((t,j)=>t.classList.toggle("active",i===j));
}}
function filterCat(btn,cat,pane){{
  pane.querySelectorAll(".filter-btn").forEach(b=>b.classList.remove("active"));
  btn.classList.add("active");
  pane.querySelectorAll(".sec[data-cat]").forEach(s=>{{
    s.style.display=(cat==="all"||s.dataset.cat===cat)?"block":"none";
  }});
}}
</script>
</body></html>'''

    os.makedirs(DOCS_DIR, exist_ok=True)
    report_path = os.path.join(DOCS_DIR, f"{week_str}.html")
    with open(report_path, "w") as f:
        f.write(html)
    print(f"[Weekly] Report saved → {report_path}")

    build_weekly_index(cfg)

    report_url = f"{pages_base}/weekly/{week_str}.html"
    if os.environ.get("SKIP_WEEKLY_SLACK", "").lower() not in {"1", "true", "yes"}:
        send_slack(cfg, week_str, total_s, total_v, best_static, best_video,
                   top_static_spend, report_url)
    else:
        print("[Weekly] Slack notification skipped (SKIP_WEEKLY_SLACK enabled).")
    return report_path


def build_weekly_index(cfg):
    reports = sorted(
        [f for f in os.listdir(DOCS_DIR) if f.endswith(".html") and f != "index.html"],
        reverse=True
    )
    pages_base = cfg.get("pages_base_url", "")
    links = "".join(
        f'<li><a href="{pages_base}/weekly/{r}">{r.replace(".html","")}</a></li>'
        for r in reports
    )
    idx = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><title>XYXX Weekly Reports</title>
<style>body{{background:#0c0c0c;color:#e8e8e8;font-family:Inter,sans-serif;padding:40px;}}
h1{{font-size:20px;margin-bottom:20px;}}ul{{list-style:none;}}li{{margin-bottom:10px;}}
a{{color:#60a5fa;text-decoration:none;font-size:14px;}}a:hover{{text-decoration:underline;}}</style>
</head><body><h1>XYXX · Weekly Reports</h1><ul>{links}</ul></body></html>'''
    with open(os.path.join(DOCS_DIR, "index.html"), "w") as f:
        f.write(idx)


def send_slack(cfg, week_str, statics_spend, videos_spend,
               best_static, best_video, top_spend, report_url):
    if not SLACK_WEBHOOK:
        print("[Weekly] No Slack webhook, skipping.")
        return

    bs = f"{short_name(best_static['ad_name'])[:40]} — {best_static['roas']:.2f}x" if best_static else "N/A"
    bv = f"{short_name(best_video['ad_name'])[:40]} — {best_video['roas']:.2f}x" if best_video else "N/A"
    ts = f"{short_name(top_spend['ad_name'])[:40]} — {fmt(top_spend['spend'])}" if top_spend else "N/A"

    payload = {
        "text": f"📊 XYXX Weekly Report · {week_str}",
        "blocks": [
            {"type":"header","text":{"type":"plain_text","text":f"📊 XYXX Weekly Creative Report · {week_str}"}},
            {"type":"section","fields":[
                {"type":"mrkdwn","text":f"*Statics Spend*\n{fmt(statics_spend)}"},
                {"type":"mrkdwn","text":f"*Videos Spend*\n{fmt(videos_spend)}"},
            ]},
            {"type":"section","fields":[
                {"type":"mrkdwn","text":f"*🏆 Best Static ROAS*\n{bs}"},
                {"type":"mrkdwn","text":f"*🎬 Best Video ROAS*\n{bv}"},
            ]},
            {"type":"section","text":{"type":"mrkdwn","text":f"*💸 Top Static Spend*\n{ts}"}},
            {"type":"actions","elements":[{
                "type":"button","text":{"type":"plain_text","text":"View Weekly Deck →"},
                "url":report_url,"style":"primary"
            }]}
        ]
    }
    try:
        r = requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[Weekly] Slack sent → {report_url}")
    except Exception as e:
        print(f"[Weekly] Slack failed: {e}")


if __name__ == "__main__":
    build()
