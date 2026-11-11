"""
main_videos.py — Daily videos data pull.

Pulls last 7 days of ad-level insights for video ads,
categorises by ad set, picks top 10 per category by SPEND,
fetches fresh preview iframe URLs (same as statics),
saves to data/videos_daily.json.
Primary efficiency metric: 1D Click blended ROAS.
"""

import json
import os
import yaml
from datetime import datetime
from meta_api import fetch_ad_insights, fetch_previews_bulk, categorise_ads, consolidate_by_creative
from taxonomy import summarise_unmapped, category_orders, drop_ignored, ad_format

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "videos_daily.json")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def is_video(ad):
    """Thin wrapper kept for compatibility. Uses the shared gap-free classifier in taxonomy.py."""
    return ad_format(ad) == "video"


def run():
    cfg = load_config()
    lookback = cfg.get("daily_lookback_days", 7)
    top_n = cfg.get("top_n_per_category", 10)
    min_spend = cfg.get("min_spend", 500)
    card_min = cfg.get("card_min_spend", min_spend)   # ad CARDS need at least this much spend
    categories = cfg["videos_categories"]

    print(f"[Videos] Pulling last {lookback} days of ad insights...")
    rows = fetch_ad_insights(lookback_days=lookback)
    print(f"[Videos] Raw ad rows returned: {len(rows)}")

    # 1. Every row gets exactly ONE format (static / video), so nothing can fall between the two dashboards.
    fmt_rows = [a for a in rows if ad_format(a) == "video"]

    # 2. Categorise the RAW rows (ad set name, then creative name).
    by_cat_rows = categorise_ads(fmt_rows, categories)

    # Categories flagged `ignore: true` in config.yaml are dropped on purpose (e.g. shorts).
    by_cat_rows, ignored = drop_ignored(by_cat_rows, categories)
    if ignored:
        print(f"[Videos] Ignored {len(ignored)} ad rows (Rs {sum(a['spend'] for a in ignored):,.0f}) per config ignore rules")

    # 3. THEN merge same-named creatives inside each category. Merging first (the old order) pooled a
    #    creative's spend from EVERY category into whichever ad set spent most, so a creative re-used in
    #    a new (e.g. winter) ad set was counted in its old category and was missing from the new one.
    by_cat = {}
    below_min = {"ads": 0, "spend": 0.0}
    for cat, cat_rows in by_cat_rows.items():
        merged = consolidate_by_creative(cat_rows)
        kept = [a for a in merged if a["spend"] >= min_spend]
        below_min["ads"] += len(merged) - len(kept)
        below_min["spend"] += sum(a["spend"] for a in merged if a["spend"] < min_spend)
        if kept:
            by_cat[cat] = kept
    counted = sum(len(v) for v in by_cat.values())

    # Ads that match no category used to vanish silently. Keep a summary so the
    # dashboard can show them, and log them so a missing keyword is obvious.
    unmapped = summarise_unmapped(by_cat.get("Other", []))
    if unmapped["total_ads"]:
        print(f"[Videos] WARNING {unmapped['total_ads']} ads (Rs {unmapped['total_spend']:,.0f}) matched no category: "
              + ", ".join(s["name"] for s in unmapped["ad_sets"][:5]))
    order_of = category_orders(categories, cfg.get("group_order"))
    group_emoji_of = {c["group"]: c["group_emoji"] for c in categories.values()
                      if c.get("group") and c.get("group_emoji")}
    group_summary_of = {c["group"]: True for c in categories.values()
                        if c.get("group") and c.get("group_summary")}

    # Top N per category by SPEND (same as statics)
    top_ads = {}
    all_top_ids = []
    hidden = {"ads": 0, "spend": 0.0}      # counted in totals, but too small to get a card
    for cat, ads in by_cat.items():
        if cat == "Other":
            continue
        small = [a for a in ads if a["spend"] < card_min]
        hidden["ads"] += len(small)
        hidden["spend"] += sum(a["spend"] for a in small)
        eligible = [a for a in ads if a["spend"] >= card_min]
        ranked = sorted(eligible, key=lambda x: x["spend"], reverse=True)[:top_n]
        top_ads[cat] = ranked
        all_top_ids.extend([a["ad_id"] for a in ranked if a["ad_id"]])

    # Fetch preview iframe URLs (same API call as statics)
    print(f"[Videos] Fetching preview URLs for {len(all_top_ids)} ads...")
    preview_map = fetch_previews_bulk(all_top_ids)

    for cat, ads in top_ads.items():
        for ad in ads:
            ad["preview_url"] = preview_map.get(ad["ad_id"], "")

    # Category summary — blended ROAS as efficiency metric
    summary = {}
    for cat, ads in top_ads.items():
        all_cat_ads = by_cat.get(cat, [])
        emoji = categories.get(cat, {}).get("emoji", "")
        total_spend = sum(a["spend"] for a in all_cat_ads)
        total_cv = sum(a["conv_value"] for a in all_cat_ads)
        blended_roas = total_cv / total_spend if total_spend > 0 else 0
        qualified = [a for a in ads if a["purchases"] >= 2]
        best_roas = max(qualified, key=lambda x: x["roas"])["roas"] if qualified else 0
        summary[cat] = {
            "emoji": emoji,
            "group": categories.get(cat, {}).get("group", ""),
            "group_emoji": group_emoji_of.get(categories.get(cat, {}).get("group", ""), ""),
            "order": order_of.get(cat, 10 ** 6),
            "total_ads": len(all_cat_ads),
            "total_purchases": int(sum(a.get("purchases", 0) for a in all_cat_ads)),
            "group_summary": group_summary_of.get(categories.get(cat, {}).get("group", ""), False),
            "total_spend": total_spend,
            "blended_roas": blended_roas,
            "best_roas": best_roas,
            "top_ads": ads,
        }

    output = {
        "generated_at": datetime.utcnow().isoformat(),
        "lookback_days": lookback,
        "categories": summary,
        "unmapped": unmapped,
        "min_spend": min_spend,
        "card_min_spend": card_min,
        "hidden_by_card_floor": {"ads": hidden["ads"], "spend": round(hidden["spend"], 2)},
        # Where every ad row went, so a "missing ad" can be traced instead of guessed at.
        "audit": {
            "rows_fetched": len(rows),
            "rows_this_format": len(fmt_rows),
            "creatives_counted": counted,
            "below_min_spend": {"ads": below_min["ads"], "spend": round(below_min["spend"], 2)},
            "ignored": {"ad_rows": len(ignored), "spend": round(sum(a["spend"] for a in ignored), 2)},
            "unmapped": {"ads": unmapped["total_ads"], "spend": round(unmapped["total_spend"], 2)},
        },
    }

    print(f"[Videos] FUNNEL  fetched {len(rows)} rows -> {len(fmt_rows)} video rows -> {counted} counted creatives "
          f"(dropped below Rs{min_spend:,.0f}: {below_min['ads']}) | cards need Rs{card_min:,.0f}+ "
          f"({hidden['ads']} counted ads hidden) | ignored {len(ignored)} | unmapped {unmapped['total_ads']}")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[Videos] Saved → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
