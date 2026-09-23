"""
generate_statics.py — Builds docs/index.html (statics dashboard).
Reads data/statics_daily.json, outputs the 3-tab HTML dashboard.
"""

import json
import os
import yaml
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
INPUT_PATH = os.path.join(BASE_DIR, "data", "statics_daily.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "docs", "index.html")
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
    import re
    for tag in ["-Static-Creative-Flatlay-Internal", "-Static-Model-Internal",
                "-Static-Product-Romance-Internal", "August2026-", "September2026-",
                "July2026-", "June2026-", "May2026-", "Apr2026-"]:
        n = n.replace(tag, "")
    n = re.sub(r"-\d{2}\.\d{2}\.\d{4}$", "", n)
    return n[:55]


def ad_card(ad, cfg, show_preview=True):
    sn = short_name(ad["ad_name"])
    roas_col = rc(ad["roas"], cfg)
    preview_html = ""
    if show_preview and ad.get("preview_url"):
        preview_html = f'''<div class="mpw static-preview">
          <iframe src="{ad['preview_url']}" scrolling="no" allow="autoplay" loading="lazy"></iframe>
        </div>'''
    else:
        preview_html = '<div class="mpw no-prev">Preview loads on open</div>'

    return f'''<div class="mc">
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
    categories = data.get("categories", {})

    # Summary stats
    total_spend = sum(c["total_spend"] for c in categories.values())
    total_ads = sum(c["total_ads"] for c in categories.values())

    # Tab 1: Category overview (top 3 + best ROAS + worst per category)
    tab1 = ""
    for cat, cat_data in categories.items():
        emoji = cat_data.get("emoji", "")
        top_ads = cat_data.get("top_ads", [])
        if not top_ads:
            continue

        # Best ROAS card (with min 3 purchases)
        qualified = [a for a in top_ads if a["purchases"] >= 3]
        best_roas_ad = max(qualified, key=lambda x: x["roas"]) if qualified else None
        worst_roas_ad = min(qualified, key=lambda x: x["roas"]) if qualified else None

        # Show top 3 by spend always
        shown_names = set()
        cards_html = ""
        for ad in top_ads[:3]:
            is_best = best_roas_ad and ad["ad_name"] == best_roas_ad["ad_name"]
            is_worst = worst_roas_ad and ad["ad_name"] == worst_roas_ad["ad_name"] and not is_best
            badge = ""
            style = "border:1px solid #252525;background:#161616;"
            if is_best:
                badge = '<div class="badge sbadge">★ Best ROAS</div>'
                style = "border:1px solid rgba(34,197,94,0.35);background:#0f1a0f;"
            elif is_worst:
                badge = '<div class="badge wbadge">⚠ Lowest</div>'
                style = "border:1px solid rgba(239,68,68,0.4);background:#180f0f;"
            card = ad_card(ad, cfg, True)
            prefix = "<div class='mc'>"
            cards_html += f'<div class="mc" style="{style}">{badge}{card[len(prefix):]}'
            shown_names.add(ad["ad_name"])

        # Add best ROAS if not in top 3
        if best_roas_ad and best_roas_ad["ad_name"] not in shown_names:
            card = ad_card(best_roas_ad, cfg, True)
            prefix = "<div class='mc'>"
            cards_html += f'<div class="mc" style="border:1px solid rgba(34,197,94,0.35);background:#0f1a0f;"><div class="badge sbadge">★ Best ROAS</div>{card[len(prefix):]}'
            shown_names.add(best_roas_ad["ad_name"])

        # Add worst if not in shown
        if worst_roas_ad and worst_roas_ad["ad_name"] not in shown_names:
            card = ad_card(worst_roas_ad, cfg, True)
            prefix = "<div class='mc'>"
            cards_html += f'<div class="mc" style="border:1px solid rgba(239,68,68,0.4);background:#180f0f;"><div class="badge wbadge">⚠ Lowest</div>{card[len(prefix):]}'

        tab1 += f'''<div class="sec">
          <div class="sec-hdr">
            <div>
              <div class="sec-t">{emoji} {cat}</div>
              <div class="sec-s">{cat_data["total_ads"]} ads · {fmt(cat_data["total_spend"])} · {cat_data["blended_roas"]:.2f}x blended ROAS</div>
            </div>
          </div>
          <div class="cgrid">{cards_html}</div>
        </div>'''

    # Tab 2: All top-10 per category with filter
    filter_btns = '<button class="filter-btn active" onclick="filterCat(this,\'all\')">All</button>'
    for cat in categories:
        safe = cat.replace("'", "\\'")
        filter_btns += f'<button class="filter-btn" onclick="filterCat(this,\'{safe}\')">{cat}</button>'

    tab2 = f'<div class="filter-bar">{filter_btns}</div>'
    for cat, cat_data in categories.items():
        top_ads = cat_data.get("top_ads", [])
        if not top_ads:
            continue
        cards_html = "".join(ad_card(a, cfg, True) for a in top_ads)
        safe_cat = cat.replace('"', '&quot;')
        tab2 += f'''<div class="sec" data-cat="{safe_cat}">
          <div class="sec-hdr">
            <div class="sec-t">{cat_data.get("emoji","")} {cat}</div>
            <div class="sec-s">Top {len(top_ads)} by spend · {fmt(cat_data["total_spend"])}</div>
          </div>
          <div class="cgrid">{cards_html}</div>
        </div>'''

    # Tab 3: Claude creative analysis
    ad_data_for_ai = []
    for cat, cat_data in categories.items():
        for ad in cat_data.get("top_ads", []):
            ad_data_for_ai.append({
                "category": cat,
                "name": short_name(ad["ad_name"]),
                "spend": round(ad["spend"]),
                "roas": round(ad["roas"], 2),
                "purchases": ad["purchases"],
            })
    ad_data_json = json.dumps(ad_data_for_ai)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>XYXX Statics Dashboard</title>
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
.mc{{border-radius:10px;overflow:hidden;display:flex;flex-direction:column;position:relative;border:1px solid #252525;background:#161616;}}
.badge{{position:absolute;top:7px;left:7px;z-index:10;padding:2px 6px;border-radius:3px;font-size:9px;font-weight:700;}}
.sbadge{{background:rgba(34,197,94,0.9);color:#000;}}
.wbadge{{background:rgba(239,68,68,0.9);color:#fff;}}
.mpw{{background:#111;overflow:hidden;display:flex;align-items:flex-start;justify-content:center;}}
.static-preview{{height:680px;}}
.static-preview iframe{{border:none;width:100%;height:680px;display:block;overflow:hidden;}}
.no-prev{{height:680px;display:flex;align-items:center;justify-content:center;color:#333;font-size:10px;}}
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
.analysis-wrap{{background:#111;border-radius:10px;border:1px solid #1e1e1e;padding:20px;min-height:300px;}}
.run-btn{{padding:9px 18px;background:#3b82f6;border:none;border-radius:7px;color:#fff;font-size:12px;font-weight:600;cursor:pointer;font-family:"Inter",sans-serif;margin-bottom:16px;}}
.run-btn:disabled{{background:#1e3a5f;color:#4b7ab5;cursor:not-allowed;}}
.analysis-out{{font-size:12.5px;line-height:1.7;color:#c8d4c8;white-space:pre-wrap;}}
.analysis-out h3{{color:#e8e8e8;font-size:14px;margin:14px 0 6px;border-bottom:1px solid #1e1e1e;padding-bottom:5px;}}
.analysis-out strong{{color:#86efac;}}
.nav-links{{display:flex;gap:12px;align-items:center;}}
.nav-link{{font-size:11px;color:#555;text-decoration:none;padding:4px 10px;border:1px solid #2a2a2a;border-radius:5px;}}
.nav-link:hover{{color:#aaa;border-color:#555;}}
</style>
</head>
<body>
<div class="hdr">
  <div class="hdr-row">
    <div>
      <div class="hdr-title">XYXX · Statics Dashboard</div>
      <div class="hdr-sub">Last {lookback} days · 1D Click · Updated {generated}</div>
    </div>
    <div class="nav-links">
      <a class="nav-link" href="videos.html">📹 Videos</a>
      <a class="nav-link" href="weekly/">📊 Weekly Reports</a>
    </div>
  </div>
  <div class="tabs">
    <div class="tab active" onclick="switchTab(0)">📊 Overview</div>
    <div class="tab" onclick="switchTab(1)">🎨 All Ads</div>
    <div class="tab" onclick="switchTab(2)">🧠 Analysis</div>
  </div>
</div>
<div class="stats">
  <div><div class="stat-l">Total Spend</div><div class="stat-v">{fmt(total_spend)}</div></div>
  <div><div class="stat-l">Ads Tracked</div><div class="stat-v">{total_ads}</div></div>
  <div><div class="stat-l">Categories</div><div class="stat-v">{len(categories)}</div></div>
  <div><div class="stat-l">Attribution</div><div class="stat-v" style="font-size:14px;padding-top:2px">1D Click</div></div>
  <div><div class="stat-l">Period</div><div class="stat-v" style="font-size:14px;padding-top:2px">Last {lookback}d</div></div>
</div>
<div class="tab-content active" id="tab0">{tab1}</div>
<div class="tab-content" id="tab1">{tab2}</div>
<div class="tab-content" id="tab2">
  <button class="run-btn" id="runBtn" onclick="runAnalysis()">Run Creative Analysis</button>
  <div class="analysis-wrap" id="analysisOut">
    <div style="color:#555;font-style:italic">Click "Run Creative Analysis" to analyse ad performance against Meta best practices and Indian market standards.</div>
  </div>
</div>
<script>
var AD_DATA={ad_data_json};
function switchTab(i){{document.querySelectorAll(".tab").forEach((t,j)=>t.classList.toggle("active",i===j));document.querySelectorAll(".tab-content").forEach((t,j)=>t.classList.toggle("active",i===j));}}
function filterCat(btn,cat){{document.querySelectorAll(".filter-btn").forEach(b=>b.classList.remove("active"));btn.classList.add("active");document.querySelectorAll("#tab1 .sec").forEach(s=>{{s.style.display=(cat==="all"||s.dataset.cat===cat)?"block":"none";}});}}
async function runAnalysis(){{
  var btn=document.getElementById("runBtn");
  var out=document.getElementById("analysisOut");
  btn.disabled=true;btn.textContent="Analysing...";
  out.innerHTML='<div style="color:#555;font-style:italic">Analysing...</div>';
  var prompt=`You are a senior performance creative strategist for Indian D2C menswear. Analyse these August 2026 static ads for XYXX Crew running on Meta (Facebook/Instagram), 1D click attribution.\\n\\nAD DATA:\\n${{JSON.stringify(AD_DATA,null,2)}}\\n\\nProvide:\\n## Category Patterns\\nFor each category: what creative hooks work vs fail, copy patterns, India market context.\\n## Cross-Category Insights\\nFormulas working across categories, AI-generated vs Internal, Flatlay vs Model.\\n## September Recommendations\\nTop 5 specific creative recommendations. Be data-driven and India-market aware.`;
  try{{
    var resp=await fetch("https://api.anthropic.com/v1/messages",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{model:"claude-sonnet-4-6",max_tokens:1000,messages:[{{role:"user",content:prompt}}]}})}});
    var d=await resp.json();
    var text=d.content&&d.content[0]?d.content[0].text:"No response.";
    text=text.replace(/^## (.+)$/gm,"<h3>$1</h3>").replace(/\\*\\*(.+?)\\*\\*/g,"<strong>$1</strong>").replace(/^- /gm,"• ");
    out.innerHTML='<div class="analysis-out">'+text+'</div>';
  }}catch(e){{out.innerHTML='<div style="color:#f87171">Error: '+e.message+'</div>';}}
  btn.disabled=false;btn.textContent="Run Creative Analysis";
}}
</script>
</body></html>'''

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"[Statics Dashboard] Written to {OUTPUT_PATH}")


def run():
    cfg = load_config()
    if not os.path.exists(INPUT_PATH):
        print(f"[Statics Dashboard] No data file at {INPUT_PATH}, skipping.")
        return
    with open(INPUT_PATH) as f:
        data = json.load(f)
    build(data, cfg)
    send_daily_slack(data, cfg)


def _is_video_ad(ad):
    """Match the same video identification rules used by main_videos.py."""
    adset = ad.get("adset_name", "").lower()
    name = ad.get("ad_name", "").lower()
    if "video" in adset:
        return True
    return any(kw in name for kw in ["-video-", "_video_", "ugc-pa", "ugc-diy", "ugc-dark"])


def _daily_section(ads, categories_cfg, min_spend, label, qualification_spend):
    """Build Slack metrics from yesterday's ad-level data."""
    from meta_api import categorise_ads
    filtered = [a for a in ads if a.get("spend", 0) >= min_spend]
    by_cat = categorise_ads(filtered, categories_cfg)

    total_spend = sum(a.get("spend", 0) for a in filtered)
    total_cv = sum(a.get("conv_value", 0) for a in filtered)
    blended_roas = total_cv / total_spend if total_spend > 0 else 0

    # Best ROAS is only meaningful above the reporting-period spend threshold.
    qualified = [a for a in filtered if a.get("spend", 0) >= qualification_spend]
    best = max(qualified, key=lambda x: x.get("roas", 0)) if qualified else None
    top_spend = max(filtered, key=lambda x: x.get("spend", 0)) if filtered else None

    best_line = (
        f"{short_name(best['ad_name'])[:42]} — {best['roas']:.2f}x · {fmt(best['spend'])} spend"
        if best else f"No {label.lower()} crossed {fmt(qualification_spend)} spend"
    )
    top_line = (
        f"{short_name(top_spend['ad_name'])[:42]} — {fmt(top_spend['spend'])} · {top_spend['roas']:.2f}x ROAS"
        if top_spend else "N/A"
    )

    cat_lines = []
    for cat, cat_data in list(by_cat.items())[:5]:
        if cat == "Other":
            continue
        emoji = categories_cfg.get(cat, {}).get("emoji", "")
        cat_spend = sum(a.get("spend", 0) for a in cat_data)
        cat_cv = sum(a.get("conv_value", 0) for a in cat_data)
        cat_roas = cat_cv / cat_spend if cat_spend > 0 else 0
        cat_lines.append(f"{emoji} {cat[:22]} · {fmt(cat_spend)} · {cat_roas:.2f}x")

    return {
        "total_spend": total_spend,
        "blended_roas": blended_roas,
        "best_line": best_line,
        "top_line": top_line,
        "cat_text": "\n".join(cat_lines) or "N/A",
    }



def _send_slack_fallback(data, cfg, error_message):
    """Send a clearly labelled fallback Slack update using existing 7-day data."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        print("[Daily Slack] No webhook configured, skipping fallback.")
        return

    pages_base = cfg.get("pages_base_url", "")
    categories = data.get("categories", {})
    lookback = data.get("lookback_days", 7)
    generated = data.get("generated_at", "")[:10] or datetime.utcnow().date().isoformat()

    total_spend = sum(c.get("total_spend", 0) for c in categories.values())
    total_cv = sum(
        sum(a.get("conv_value", 0) for a in c.get("top_ads", []))
        for c in categories.values()
    )
    blended_roas = total_cv / total_spend if total_spend > 0 else 0

    all_ads = [a for c in categories.values() for a in c.get("top_ads", [])]
    best = max(all_ads, key=lambda x: x.get("roas", 0)) if all_ads else None
    top_spend = max(all_ads, key=lambda x: x.get("spend", 0)) if all_ads else None

    best_line = (
        f"{short_name(best['ad_name'])[:42]} — {best['roas']:.2f}x ROAS"
        if best else "N/A"
    )
    top_line = (
        f"{short_name(top_spend['ad_name'])[:42]} — {fmt(top_spend['spend'])} · "
        f"{top_spend.get('roas', 0):.2f}x ROAS"
        if top_spend else "N/A"
    )

    cat_lines = []
    for cat, cat_data in list(categories.items())[:5]:
        emoji = cat_data.get("emoji", "")
        cat_lines.append(
            f"{emoji} {cat[:22]} · {fmt(cat_data.get('total_spend', 0))} · "
            f"{cat_data.get('blended_roas', 0):.2f}x"
        )
    cat_text = "\n".join(cat_lines) or "N/A"

    payload = {
        "text": f"📅 XYXX Daily Update · {generated} · 7-day fallback",
        "blocks": [
            {"type": "header", "text": {
                "type": "plain_text",
                "text": f"📅 XYXX Daily Update · {generated}"
            }},
            {"type": "section", "text": {"type": "mrkdwn", "text":
                f"⚠️ *Daily Meta pull was rate-limited.* Showing the existing *last {lookback} days* dashboard data instead. Yesterday-only metrics will resume automatically on the next successful run."
            }},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Statics Spend (last {lookback}d)*\n{fmt(total_spend)}"},
                {"type": "mrkdwn", "text": f"*Blended ROAS*\n{blended_roas:.2f}x"},
            ]},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*🏆 Best ROAS*\n{best_line}"},
                {"type": "mrkdwn", "text": f"*💸 Top Spender*\n{top_line}"},
            ]},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*By Category:*\n{cat_text}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text":
                "🎥 *Videos — unavailable in fallback because the additional Meta pull was rate-limited.*"
            }},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Statics Dashboard →"},
                 "url": f"{pages_base}/index.html"},
                {"type": "button", "text": {"type": "plain_text", "text": "Videos Dashboard →"},
                 "url": f"{pages_base}/videos.html"},
            ]},
        ],
    }

    try:
        import requests as _req
        r = _req.post(webhook, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[Daily Slack] Fallback sent for {generated} (last {lookback}d)")
    except Exception as e:
        print(f"[Daily Slack] Fallback failed: {e}")

def send_daily_slack(data, cfg):
    """Send one combined Slack update using yesterday-only Meta data."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        print("[Daily Slack] No webhook configured, skipping.")
        return

    pages_base = cfg.get("pages_base_url", "")
    daily_min_spend = cfg.get("slack", {}).get("daily_min_spend", 10000)
    base_min_spend = cfg.get("min_spend", 500)

    print("[Daily Slack] Pulling yesterday's ad insights...")
    try:
        # Import only when the Slack/Meta pull is actually needed. This keeps
        # the dashboard generator runnable in workflows that do not expose
        # META_ACCESS_TOKEN.
        from meta_api import fetch_ad_insights, consolidate_by_creative, categorise_ads
        daily_ads = fetch_ad_insights(lookback_days=1)
        daily_ads = consolidate_by_creative(daily_ads)
    except Exception as e:
        # Meta can temporarily rate-limit the additional yesterday-only pull.
        # Do not lose the Slack notification in that case: fall back to the
        # already-successful 7-day dashboard data and label it clearly.
        print(f"[Daily Slack] Daily Meta pull failed: {e}")
        _send_slack_fallback(data, cfg, str(e))
        return

    # Split yesterday's data into statics and videos, while keeping the existing
    # 7-day dashboard data completely untouched.
    static_ads = [a for a in daily_ads if not _is_video_ad(a)]
    video_ads = [a for a in daily_ads if _is_video_ad(a)]

    static_metrics = _daily_section(
        static_ads,
        cfg.get("statics_categories", {}),
        base_min_spend,
        "Statics",
        daily_min_spend,
    )
    video_metrics = _daily_section(
        video_ads,
        cfg.get("videos_categories", {}),
        base_min_spend,
        "Videos",
        daily_min_spend,
    )

    # Meta's date_range(1) represents yesterday; use the actual data date when available.
    generated = (
        daily_ads[0].get("date_until", "")
        if daily_ads else (datetime.utcnow().date()).isoformat()
    )

    payload = {
        "text": f"📅 XYXX Daily Update · {generated}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"📅 XYXX Daily Update · {generated}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*📊 STATICS — Yesterday*"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Spend*\n{fmt(static_metrics['total_spend'])}"},
                    {"type": "mrkdwn", "text": f"*Blended ROAS*\n{static_metrics['blended_roas']:.2f}x"},
                ],
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*🏆 Best ROAS (≥ {fmt(daily_min_spend)})*\n{static_metrics['best_line']}"},
                    {"type": "mrkdwn", "text": f"*💸 Top Spender*\n{static_metrics['top_line']}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*By Category:*\n{static_metrics['cat_text']}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*🎥 VIDEOS — Yesterday*"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Spend*\n{fmt(video_metrics['total_spend'])}"},
                    {"type": "mrkdwn", "text": f"*Blended ROAS*\n{video_metrics['blended_roas']:.2f}x"},
                ],
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*🏆 Best ROAS (≥ {fmt(daily_min_spend)})*\n{video_metrics['best_line']}"},
                    {"type": "mrkdwn", "text": f"*💸 Top Spender*\n{video_metrics['top_line']}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*By Category:*\n{video_metrics['cat_text']}"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Statics Dashboard →"},
                        "url": f"{pages_base}/index.html",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Videos Dashboard →"},
                        "url": f"{pages_base}/videos.html",
                    },
                ],
            },
        ],
    }

    try:
        import requests as _req
        r = _req.post(webhook, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[Daily Slack] Sent for {generated}")
    except Exception as e:
        print(f"[Daily Slack] Failed: {e}")


if __name__ == "__main__":
    run()

