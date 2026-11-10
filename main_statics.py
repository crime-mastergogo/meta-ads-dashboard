"""
main_statics.py — Daily statics data pull.

Pulls last 7 days of ad-level insights for static ads,
categorises by ad set, picks top N per category,
fetches fresh preview URLs, saves to data/statics_daily.json.
"""

import json
import os
import yaml
from datetime import datetime
from meta_api import fetch_ad_insights, fetch_previews_bulk, categorise_ads, consolidate_by_creative
from taxonomy import summarise_unmapped

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "statics_daily.json")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def is_static(ad):
    """Static ads: not Video in adset name, not UGC video creative."""
    adset = ad["adset_name"].lower()
    name = ad["ad_name"].lower()
    # Exclude video ad sets
    if "video" in adset:
        return False
    # Exclude UGC/video ad names
    if any(kw in name for kw in ["-video-", "_video_", "ugc-pa", "ugc-diy", "ugc-dark"]):
        return False
    return True


def run():
    cfg = load_config()
    lookback = cfg.get("daily_lookback_days", 7)
    top_n = cfg.get("top_n_per_category", 10)
    min_spend = cfg.get("min_spend", 500)
    categories = cfg["statics_categories"]

    print(f"[Statics] Pulling last {lookback} days of ad insights...")
    all_ads = fetch_ad_insights(lookback_days=lookback)
    print(f"[Statics] Raw ads returned: {len(all_ads)}")
    all_ads = consolidate_by_creative(all_ads)
    print(f"[Statics] After consolidation: {len(all_ads)} unique creatives")

    # Filter to statics only, min spend
    statics = [a for a in all_ads if is_static(a) and a["spend"] >= min_spend]
    print(f"[Statics] After filters: {len(statics)} ads")

    # Categorise
    by_cat = categorise_ads(statics, categories)

    # Ads that match no category used to vanish silently. Keep a summary so the
    # dashboard can show them, and log them so a missing keyword is obvious.
    unmapped = summarise_unmapped(by_cat.get("Other", []))
    if unmapped["total_ads"]:
        print(f"[Statics] WARNING {unmapped['total_ads']} ads (Rs {unmapped['total_spend']:,.0f}) matched no category: "
              + ", ".join(s["name"] for s in unmapped["ad_sets"][:5]))
    order_of = {name: i for i, name in enumerate(categories)}
    group_emoji_of = {c["group"]: c["group_emoji"] for c in categories.values()
                      if c.get("group") and c.get("group_emoji")}

    # Top N per category by spend
    top_ads = {}
    all_top_ids = []
    for cat, ads in by_cat.items():
        if cat == "Other":
            continue
        ranked = sorted(ads, key=lambda x: x["spend"], reverse=True)[:top_n]
        top_ads[cat] = ranked
        all_top_ids.extend([a["ad_id"] for a in ranked if a["ad_id"]])

    # Fetch fresh preview URLs for all top ads
    print(f"[Statics] Fetching preview URLs for {len(all_top_ids)} ads...")
    preview_map = fetch_previews_bulk(all_top_ids)

    # Attach preview URLs to ads
    for cat, ads in top_ads.items():
        for ad in ads:
            ad["preview_url"] = preview_map.get(ad["ad_id"], "")

    # Category summary stats
    summary = {}
    for cat, ads in top_ads.items():
        all_cat_ads = by_cat.get(cat, [])
        emoji = categories.get(cat, {}).get("emoji", "")
        summary[cat] = {
            "emoji": emoji,
            "group": categories.get(cat, {}).get("group", ""),
            "group_emoji": group_emoji_of.get(categories.get(cat, {}).get("group", ""), ""),
            "order": order_of.get(cat, 10 ** 6),
            "total_ads": len(all_cat_ads),
            "total_spend": sum(a["spend"] for a in all_cat_ads),
            "blended_roas": (
                sum(a["conv_value"] for a in all_cat_ads) /
                sum(a["spend"] for a in all_cat_ads)
                if sum(a["spend"] for a in all_cat_ads) > 0 else 0
            ),
            "top_ads": ads,
        }

    output = {
        "generated_at": datetime.utcnow().isoformat(),
        "lookback_days": lookback,
        "categories": summary,
        "unmapped": unmapped,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[Statics] Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
