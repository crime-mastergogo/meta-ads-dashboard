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
from taxonomy import summarise_unmapped, category_orders, drop_ignored

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "videos_daily.json")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def is_video(ad):
    adset = ad["adset_name"].lower()
    name = ad["ad_name"].lower()

    # Reject obvious non-video creative types even when they sit inside a
    # video-named ad set (e.g. DPA/carousel/static/catalog creatives).
    non_video_markers = [
        "dpa", "carousel", "static", "image", "catalog", "collection",
    ]
    if any(marker in name for marker in non_video_markers):
        return False

    # Existing naming conventions remain the primary video signals.
    if "video" in adset:
        return True
    if any(kw in name for kw in ["-video-", "_video_", "ugc-pa", "ugc-diy", "ugc-dark"]):
        return True
    return False


def run():
    cfg = load_config()
    lookback = cfg.get("daily_lookback_days", 7)
    top_n = cfg.get("top_n_per_category", 10)
    min_spend = cfg.get("min_spend", 500)
    categories = cfg["videos_categories"]

    print(f"[Videos] Pulling last {lookback} days of ad insights...")
    all_ads = fetch_ad_insights(lookback_days=lookback)
    print(f"[Videos] Raw ads: {len(all_ads)}")
    all_ads = consolidate_by_creative(all_ads)
    print(f"[Videos] After consolidation: {len(all_ads)} unique creatives")

    videos = [a for a in all_ads if is_video(a) and a["spend"] >= min_spend]
    print(f"[Videos] After filter: {len(videos)} video ads")

    by_cat = categorise_ads(videos, categories)

    # Categories flagged `ignore: true` in config.yaml are dropped on purpose (e.g. shorts).
    by_cat, ignored = drop_ignored(by_cat, categories)
    if ignored:
        print(f"[Videos] Ignored {len(ignored)} ads (Rs {sum(a['spend'] for a in ignored):,.0f}) per config ignore rules")

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
    for cat, ads in by_cat.items():
        if cat == "Other":
            continue
        ranked = sorted(ads, key=lambda x: x["spend"], reverse=True)[:top_n]
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
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[Videos] Saved → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
