"""
generate_monthly.py — Builds the monthly deck.

Two-tab deck (Statics / Videos), each with:
  Section A — Top Performers per category (by spend, ★ best ROAS)
  Section B — Bottom 10 Performers per category (by ROAS, min ₹10K spend)
Sends Slack notification with link.
All creatives consolidated across campaigns before ranking.
Only creatives launched in that calendar month are shown.
"""

import json
import os
import re
import yaml
import requests
from datetime import datetime, date, timedelta
from taxonomy import ordered_categories, GroupHeaders, slack_group_lines, slack_group_lines_from_ads, unmapped_banner

BASE_DIR     = os.path.dirname(__file__)
CONFIG_PATH  = os.path.join(BASE_DIR, "config.yaml")
DOCS_DIR     = os.path.join(BASE_DIR, "docs", "monthly")
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
    for month in ["August2026-","September2026-","October2026-","November2026-",
                   "July2026-","June2026-","May2026-","Apr2026-","March2026-"]:
        n = n.replace(month, "")
    for tag in ["-Static-Creative-Flatlay-Internal","-Static-Model-Internal",
                "-Static-Product-Romance-Internal","-Video-UGC-PA-","-Video-UGC-","-Video-"]:
        n = n.replace(tag, " · ")
    n = re.sub(r"-\d{2}\.\d{2}\.\d{4}$", "", n)
    return n[:52]


def ad_card(ad, cfg, is_best=False, is_worst_roas=False, is_bottom=False):
    """
    is_best       — ★ green badge (best ROAS in top performers)
    is_worst_roas — ⚠ subtle badge in top performers section
    is_bottom     — red card in the bottom 10 section
    """
    sn = short_name(ad["ad_name"])
    roas_col = rc(ad["roas"], cfg)
    preview  = ad.get("preview_url", "")

    preview_html = (
        f'<div class="mpw"><iframe src="{preview}" scrolling="yes" allow="autoplay" loading="lazy"></iframe></div>'
        if preview
        else '<div class="mpw no-prev">—</div>'
    )

    if is_bottom:
        badge = '<div class="badge wbadge">⚠ Low Performer</div>'
        style = "border:1px solid rgba(239,68,68,0.45);background:#180f0f;"
    elif is_best:
        badge = '<div class="badge sbadge">★ Best ROAS</div>'
        style = "border:1px solid rgba(34,197,94,0.35);background:#0f1a0f;"
    elif is_worst_roas:
        badge = '<div class="badge wbadge">⚠ Lowest</div>'
        style = "border:1px solid rgba(239,68,68,0.4);background:#180f0f;"
    else:
        badge = ""
        style = "border:1px solid #252525;background:#161616;"

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


def build_format_tab(categories, cfg, format_label):
    """Build HTML for one format tab (statics or videos)."""
    categories = ordered_categories(categories)
    gh = GroupHeaders(categories)
    sections_html = ""
    min_purch = 3 if format_label == "Statics" else 2

    for cat, cat_data in categories.items():
        emoji    = cat_data.get("emoji", "")
        top_ads  = cat_data.get("top_ads", [])
        worst_ads = cat_data.get("worst_ads", [])
        if not top_ads and not worst_ads:
            continue

        total_spend   = cat_data.get("total_spend", 0)
        blended_roas  = cat_data.get("blended_roas", 0)
        min_spend_w   = cat_data.get("min_spend_worst", 10000)

        # ── TOP PERFORMERS ──
        qualified     = [a for a in top_ads if a["purchases"] >= min_purch]
        best_ad       = max(qualified, key=lambda x: x["roas"]) if qualified else None
        worst_in_top  = min(qualified, key=lambda x: x["roas"]) if qualified else None
        shown         = set()
        top_cards     = ""

        for ad in top_ads:
            is_best = best_ad and ad["ad_name"] == best_ad["ad_name"]
            is_w    = worst_in_top and ad["ad_name"] == worst_in_top["ad_name"] and not is_best
            top_cards += ad_card(ad, cfg, is_best, is_w, False)
            shown.add(ad["ad_name"])

        # If best ROAS wasn't in top N by spend, append it
        if best_ad and best_ad["ad_name"] not in shown:
            top_cards += ad_card(best_ad, cfg, True, False, False)
            shown.add(best_ad["ad_name"])

        # ── BOTTOM 10 ──
        bottom_cards = ""
        if worst_ads:
            for ad in worst_ads:
                bottom_cards += ad_card(ad, cfg, False, False, True)

        bottom_section = ""
        if bottom_cards:
            bottom_section = f'''
            <div class="sub-hdr worst-hdr">
              <span>⚠ Lowest Performers — min {fmt(min_spend_w)} spend · ranked by ROAS</span>
              <span>{len(worst_ads)} ads</span>
            </div>
            <div class="cgrid">{bottom_cards}</div>'''

        sections_html += gh.before(cat_data)
        sections_html += f'''<div class="sec">
          <div class="sec-hdr">
            <div>
              <div class="sec-t">{emoji} {cat}</div>
              <div class="sec-s">{cat_data["total_ads"]} creatives · {fmt(total_spend)} · {blended_roas:.2f}x blended ROAS</div>
            </div>
          </div>
          <div class="sub-hdr top-hdr">
            <span>★ Top Performers — ranked by spend</span>
            <span>{len(top_ads)} ads</span>
          </div>
          <div class="cgrid">{top_cards}</div>
          {bottom_section}
        </div>'''

    return sections_html


def build():
    cfg = load_config()
    pages_base = cfg.get("pages_base_url", "")

    today = date.today()
    first_this = today.replace(day=1)
    last_prev  = first_this - timedelta(days=1)
    month_str  = last_prev.strftime("%Y-%m")
    month_label = last_prev.strftime("%B %Y")

    s_path = os.path.join(BASE_DIR, "data", f"statics_monthly_{month_str}.json")
    v_path = os.path.join(BASE_DIR, "data", f"videos_monthly_{month_str}.json")

    statics_data = json.load(open(s_path)) if os.path.exists(s_path) else None
    videos_data  = json.load(open(v_path)) if os.path.exists(v_path) else None

    s_cats = (statics_data or {}).get("categories", {})
    v_cats = (videos_data  or {}).get("categories", {})
    since  = (statics_data or {}).get("date_since", "")
    until  = (statics_data or {}).get("date_until", "")
    month_prefix = (statics_data or {}).get("month_prefix", "")

    total_s = sum(c["total_spend"] for c in s_cats.values())
    total_v = sum(c["total_spend"] for c in v_cats.values())

    all_s_ads = [a for c in s_cats.values() for a in c.get("top_ads", [])]
    all_v_ads = [a for c in v_cats.values() for a in c.get("top_ads", [])]
    best_static     = max(all_s_ads, key=lambda x: x["roas"]) if all_s_ads else None
    best_video      = max(all_v_ads, key=lambda x: x["roas"]) if all_v_ads else None
    top_static_spend = max(all_s_ads, key=lambda x: x["spend"]) if all_s_ads else None

    # Worst of the month (across all categories)
    all_s_worst = [a for c in s_cats.values() for a in c.get("worst_ads", [])]
    worst_static = min(all_s_worst, key=lambda x: x["roas"]) if all_s_worst else None

    s_tab = build_format_tab(s_cats, cfg, "Statics")
    v_tab = build_format_tab(v_cats, cfg, "Videos")

    # Highlights
    def hl(label, val, sub):
        return f'<div class="hl-card"><div class="hl-l">{label}</div><div class="hl-v">{val}</div><div class="hl-n">{sub}</div></div>'

    highlights = ""
    if best_static:
        highlights += hl("🏆 Best Static ROAS", f"{best_static['roas']:.2f}x", short_name(best_static["ad_name"]))
    if top_static_spend:
        highlights += hl("💸 Top Static Spend", fmt(top_static_spend["spend"]), short_name(top_static_spend["ad_name"]))
    if best_video:
        highlights += hl("🎬 Best Video ROAS", f"{best_video['roas']:.2f}x", short_name(best_video["ad_name"]))
    if worst_static:
        highlights += hl("⚠ Worst Static ROAS", f"{worst_static['roas']:.2f}x", short_name(worst_static["ad_name"]))

    total_s_worst = sum(len(c.get("worst_ads",[])) for c in s_cats.values())
    total_v_worst = sum(len(c.get("worst_ads",[])) for c in v_cats.values())

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>XYXX Monthly Report · {month_label}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:#0c0c0c;color:#e8e8e8;font-family:"Inter",sans-serif;}}
.cover{{padding:44px 32px;border-bottom:1px solid #1e1e1e;}}
.cover-eye{{font-size:11px;letter-spacing:0.15em;text-transform:uppercase;color:#555;margin-bottom:8px;}}
.cover-title{{font-size:32px;font-weight:700;letter-spacing:-0.02em;line-height:1.1;margin-bottom:5px;}}
.cover-sub{{font-size:12px;color:#555;margin-bottom:24px;}}
.cover-stats{{display:flex;gap:28px;flex-wrap:wrap;padding-top:20px;border-top:1px solid #1e1e1e;}}
.cs-l{{font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#444;margin-bottom:3px;}}
.cs-v{{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;}}
.nav-links{{display:flex;gap:10px;margin-top:18px;}}
.nav-link{{font-size:11px;color:#555;text-decoration:none;padding:4px 10px;border:1px solid #2a2a2a;border-radius:5px;}}
.nav-link:hover{{color:#aaa;border-color:#555;}}
.highlights{{display:flex;gap:14px;flex-wrap:wrap;padding:20px 32px;border-bottom:1px solid #1e1e1e;background:#0f0f0f;}}
.hl-card{{background:#161616;border:1px solid #252525;border-radius:10px;padding:12px 16px;min-width:170px;}}
.hl-l{{font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#555;margin-bottom:5px;}}
.hl-v{{font-size:20px;font-weight:700;margin-bottom:2px;}}
.hl-n{{font-size:10px;color:#555;line-height:1.3;}}
.format-tabs{{display:flex;padding:0 32px;border-bottom:1px solid #1e1e1e;background:#0f0f0f;}}
.ftab{{padding:13px 20px;font-size:13px;font-weight:600;color:#555;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.2s;}}
.ftab:hover{{color:#aaa;}}
.ftab.active{{color:#e8e8e8;border-bottom-color:#3b82f6;}}
.tab-pane{{display:none;padding:26px 32px;}}
.tab-pane.active{{display:block;}}
.sec{{margin-bottom:40px;}}
.sec-hdr{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:10px;padding-bottom:9px;border-bottom:1px solid #1e1e1e;}}
.sec-t{{font-size:15px;font-weight:600;}}
.sec-s{{font-size:11px;color:#555;font-family:"JetBrains Mono",monospace;}}
.sub-hdr{{display:flex;justify-content:space-between;align-items:center;font-size:11px;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;padding:8px 0;margin-bottom:10px;}}
.top-hdr{{color:#6b7280;border-bottom:1px dashed #1e1e1e;}}
.worst-hdr{{color:#f87171;border-bottom:1px dashed rgba(239,68,68,0.3);margin-top:20px;}}
.cgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;}}
.mc{{border-radius:10px;overflow:hidden;display:flex;flex-direction:column;position:relative;}}
.badge{{position:absolute;top:7px;left:7px;z-index:10;padding:2px 6px;border-radius:3px;font-size:9px;font-weight:700;}}
.sbadge{{background:rgba(34,197,94,0.9);color:#000;}}
.wbadge{{background:rgba(239,68,68,0.9);color:#fff;}}
.mpw{{background:#111;height:750px;overflow:hidden;display:flex;align-items:flex-start;justify-content:center;}}
.mpw iframe{{border:none;width:100%;height:750px;display:block;}}
.no-prev{{height:750px;display:flex;align-items:center;justify-content:center;color:#333;font-size:10px;}}
.mm{{padding:9px 11px 11px;}}
.mn{{font-size:11px;font-weight:600;color:#e0e0e0;line-height:1.3;margin-bottom:5px;}}
.mmet{{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:3px;padding-top:6px;border-top:1px solid #1e1e1e;font-family:"JetBrains Mono",monospace;font-size:9.5px;color:#888;}}
.footer{{padding:18px 32px;border-top:1px solid #1a1a1a;font-size:10px;color:#333;font-family:"JetBrains Mono",monospace;}}
</style>
</head>
<body>
<div class="cover">
  <div class="cover-eye">XYXX Crew · Creative Intelligence</div>
  <div class="cover-title">Monthly Report<br>{month_label}</div>
  <div class="cover-sub">
    {since} → {until} · 1D Click Attribution · Consolidated across campaigns ·
    Only {month_prefix.rstrip("-")} launches
  </div>
  <div class="cover-stats">
    <div><div class="cs-l">Statics Spend</div><div class="cs-v">{fmt(total_s)}</div></div>
    <div><div class="cs-l">Videos Spend</div><div class="cs-v">{fmt(total_v)}</div></div>
    <div><div class="cs-l">Total</div><div class="cs-v">{fmt(total_s+total_v)}</div></div>
    <div><div class="cs-l">Lowest tracked</div><div class="cs-v">{total_s_worst}S / {total_v_worst}V</div></div>
  </div>
  <div class="nav-links">
    <a class="nav-link" href="../index.html">📷 Statics Dashboard</a>
    <a class="nav-link" href="../videos.html">📹 Videos Dashboard</a>
    <a class="nav-link" href="index.html">📅 All Monthly Reports</a>
  </div>
</div>

<div class="highlights">{highlights}</div>

<div class="format-tabs">
  <div class="ftab active" onclick="switchFormat(0)">📷 Statics</div>
  <div class="ftab" onclick="switchFormat(1)">📹 Videos</div>
</div>

<div class="tab-pane active" id="fmt0">{s_tab}</div>
<div class="tab-pane" id="fmt1">{v_tab}</div>

<div class="footer">
  XYXX Crew · {month_label} · Consolidated by creative · Min ₹10K spend for lowest performers ·
  Auto-generated by GitHub Actions
</div>
<script>
function switchFormat(i){{
  document.querySelectorAll(".ftab").forEach((t,j)=>t.classList.toggle("active",i===j));
  document.querySelectorAll(".tab-pane").forEach((t,j)=>t.classList.toggle("active",i===j));
}}
</script>
</body></html>'''

    os.makedirs(DOCS_DIR, exist_ok=True)
    report_path = os.path.join(DOCS_DIR, f"{month_str}.html")
    with open(report_path, "w") as f:
        f.write(html)
    print(f"[Monthly] Report → {report_path}")

    build_monthly_index(cfg)

    report_url = f"{pages_base}/monthly/{month_str}.html"
    send_slack(month_label, total_s, total_v, best_static, best_video,
               top_static_spend, worst_static, report_url)
    return report_path


def build_monthly_index(cfg):
    pages_base = cfg.get("pages_base_url", "")
    reports = sorted(
        [f for f in os.listdir(DOCS_DIR) if f.endswith(".html") and f != "index.html"],
        reverse=True
    )
    links = "".join(
        f'<li><a href="{pages_base}/monthly/{r}">{r.replace(".html","")}</a></li>'
        for r in reports
    )
    idx = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><title>XYXX Monthly Reports</title>
<style>body{{background:#0c0c0c;color:#e8e8e8;font-family:Inter,sans-serif;padding:40px;}}
h1{{font-size:20px;margin-bottom:20px;}}ul{{list-style:none;}}li{{margin-bottom:10px;}}
a{{color:#60a5fa;text-decoration:none;font-size:14px;}}a:hover{{text-decoration:underline;}}</style>
</head><body><h1>XYXX · Monthly Reports</h1><ul>{links}</ul></body></html>'''
    with open(os.path.join(DOCS_DIR, "index.html"), "w") as f:
        f.write(idx)


def send_slack(month_label, statics_spend, videos_spend,
               best_static, best_video, top_spend, worst_static, report_url):
    if not SLACK_WEBHOOK:
        print("[Monthly] No Slack webhook, skipping.")
        return

    bs = f"{short_name(best_static['ad_name'])[:42]} — {best_static['roas']:.2f}x" if best_static else "N/A"
    bv = f"{short_name(best_video['ad_name'])[:42]} — {best_video['roas']:.2f}x"   if best_video  else "N/A"
    ts = f"{short_name(top_spend['ad_name'])[:42]} — {fmt(top_spend['spend'])}"     if top_spend   else "N/A"
    ws = f"{short_name(worst_static['ad_name'])[:42]} — {worst_static['roas']:.2f}x" if worst_static else "N/A"

    payload = {
        "text": f"📅 XYXX Monthly Report · {month_label}",
        "blocks": [
            {"type":"header","text":{"type":"plain_text","text":f"📅 XYXX Monthly Creative Report · {month_label}"}},
            {"type":"section","fields":[
                {"type":"mrkdwn","text":f"*Statics Spend*\n{fmt(statics_spend)}"},
                {"type":"mrkdwn","text":f"*Videos Spend*\n{fmt(videos_spend)}"},
            ]},
            {"type":"section","fields":[
                {"type":"mrkdwn","text":f"*🏆 Best Static ROAS*\n{bs}"},
                {"type":"mrkdwn","text":f"*🎬 Best Video ROAS*\n{bv}"},
            ]},
            {"type":"section","fields":[
                {"type":"mrkdwn","text":f"*💸 Top Static Spend*\n{ts}"},
                {"type":"mrkdwn","text":f"*⚠ Worst Static ROAS*\n{ws}"},
            ]},
            {"type":"actions","elements":[{
                "type":"button","text":{"type":"plain_text","text":"View Monthly Deck →"},
                "url":report_url,"style":"primary"
            }]}
        ]
    }
    try:
        r = requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[Monthly] Slack sent → {report_url}")
    except Exception as e:
        print(f"[Monthly] Slack failed: {e}")


if __name__ == "__main__":
    build()
