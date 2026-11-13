"""
main_month.py — Current-month tracking data (feeds the "<Month>" tab on the Statics and Videos dashboards).

Nothing in here names a month. Both months are worked out from today's date (IST), so on 1 December
the tab becomes "December", with "November" as the previous-month view, and so on, forever.

  current view  : ads uploaded THIS month, spend from the 1st to yesterday
  previous view : ads uploaded LAST month, spend from the 1st of last month to yesterday
                  (how they are doing now, including this month's spend)

One insights call (time_increment=monthly) serves both views. A second, optional call lists the ads
uploaded this month and last, so uploads that never delivered a single rupee can be counted too.
If that second call fails the tab still works; it just cannot show "uploaded" and "no spend yet".

Writes data/month_statics.json and data/month_videos.json.
"""
import json
import os
from collections import defaultdict
from datetime import datetime

import yaml

import monthview as mv
from meta_api import (fetch_ad_insights_monthly, fetch_ads_by_name_prefix, fetch_previews_bulk,
                      categorise_ads, consolidate_by_creative)
from taxonomy import ad_format, category_orders, drop_ignored

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
OUTPUT_DIR = os.path.join(BASE_DIR, "data")

CATALOG_MARKERS = ("dpa", "carousel", "catalog", "collection")   # evergreen ads: no month prefix by design


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ym_key(ym):
    return f"{ym[0]}-{ym[1]:02d}"


def build_view(key, windows, fmt, fmt_rows, uploads, categories, cat_meta, cfgm, today):
    win = windows[key]
    ym = win["ym"]
    cur_has_data = windows["current"]["until"] is not None
    view = {
        "key": key, "ym": ym_key(ym), "name": mv.month_name(ym), "since": win["since"].isoformat(),
        "until": win["until"].isoformat() if win["until"] else None,
        "example_prefix": mv.name_prefixes(ym)[0],
        "has_data": win["until"] is not None, "uploaded": None, "analysis": None, "no_prefix": None,
    }
    if not view["has_data"]:
        return view

    months = {ym_key(ym)}
    if key == "previous" and cur_has_data:      # last month's uploads keep spending into this month
        months.add(ym_key(windows["current"]["ym"]))
    rows = [r for r in fmt_rows if r["month"] in months]

    # same order as the daily pipeline: categorise the raw rows, THEN merge same-named creatives inside each category
    by_cat_rows, _ignored = drop_ignored(categorise_ads(rows, categories), categories)
    by_cat = {cat: consolidate_by_creative(rs) for cat, rs in by_cat_rows.items()}
    analysis = mv.analyse(by_cat, ym, cfgm, cat_meta, today)

    ups = uploads.get(key) if uploads is not None else None
    if ups is not None:
        ups = [u for u in ups if ad_format(u) == fmt]
        names = {u["ad_name"] for u in ups}
        spending = {a["ad_name"] for ads in by_cat.values() for a in ads if mv.parse_vintage(a["ad_name"]) == ym}
        view["uploaded"] = {"count": len(names), "no_spend": len(names - spending)}
        up_by_cat, _ig = drop_ignored(categorise_ads(ups, categories), categories)
        counts = {cat: len({u["ad_name"] for u in lst}) for cat, lst in up_by_cat.items() if cat != "Other"}
        seen = set()
        for r in analysis["categories"]:
            r["uploaded"] = counts.get(r["category"], 0)
            seen.add(r["category"])
        for cat, n in counts.items():             # categories with uploads but no spend at all
            if cat not in seen:
                meta = cat_meta.get(cat, {})
                analysis["categories"].append({
                    "category": cat, "group": meta.get("group", ""), "emoji": meta.get("emoji", ""),
                    "order": meta.get("order", 10 ** 6), "uploaded": n, "spending": 0, "spend": 0, "roas": 0,
                    "older_roas": None, "best": None, "best_all": None})
    view["analysis"] = analysis

    # ads without a month prefix are not counted in the tab; say so instead of hiding it
    npx = defaultdict(float)
    for r in rows:
        n = r["ad_name"]
        if mv.parse_vintage(n) is None and not any(m in n.lower() for m in CATALOG_MARKERS):
            npx[n] += r["spend"]
    big = {n: s for n, s in npx.items() if s >= cfgm["card_min_spend"]}
    view["no_prefix"] = {"ads": len(big), "spend": round(sum(big.values()), 2),
                         "examples": sorted(big, key=lambda n: -big[n])[:3]}
    return view


def run():
    cfg = load_config()
    cfgm = mv.month_cfg(cfg)
    today = mv.today_ist()
    windows = mv.month_windows(today)
    cur, prev = windows["current"], windows["previous"]
    print(f"[Month] Run date {today} | current {mv.month_name(cur['ym'])} "
          f"({'1st to ' + str(cur['until']) if cur['until'] else 'no data yet'}) | "
          f"previous {mv.month_name(prev['ym'])} ({prev['since']} to {prev['until']})")

    rows = fetch_ad_insights_monthly(prev["since"].isoformat(), prev["until"].isoformat())
    if not rows:
        print("[Month] No insight rows returned. Keeping the existing data files untouched.")
        return

    uploads = None
    try:
        uploads = {}
        for key in ("current", "previous"):
            if windows[key]["until"] is None:
                uploads[key] = []
                continue
            ym = windows[key]["ym"]
            ads = fetch_ads_by_name_prefix(mv.name_prefixes(ym))
            uploads[key] = [a for a in ads if mv.parse_vintage(a["ad_name"]) == ym]   # month AND year must match
    except Exception as e:                                                         # never block the tab on this
        print(f"[Month] WARNING upload counts unavailable ({e}). The tab will not show 'uploaded' / 'no spend yet'.")
        uploads = None

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for fmt, kind, cats_key in (("static", "statics", "statics_categories"), ("video", "videos", "videos_categories")):
        categories = cfg[cats_key]
        order_of = category_orders(categories, cfg.get("group_order"))
        cat_meta = {n: {"group": c.get("group", ""), "emoji": c.get("emoji", ""), "order": order_of.get(n, 10 ** 6)}
                    for n, c in categories.items()}
        fmt_rows = [r for r in rows if ad_format(r) == fmt]
        views = {key: build_view(key, windows, fmt, fmt_rows, uploads, categories, cat_meta, cfgm, today)
                 for key in ("current", "previous")}

        cards = [c for v in views.values() if v.get("analysis") for c in v["analysis"]["cards"]]
        ids = [c["ad_id"] for c in cards if c.get("ad_id")]
        print(f"[Month/{kind}] Fetching preview URLs for {len(ids)} cards...")
        previews = fetch_previews_bulk(ids) if ids else {}
        for c in cards:
            c["preview_url"] = previews.get(c.get("ad_id"), "")

        out = {"generated_at": datetime.utcnow().isoformat(), "today": today.isoformat(), "views": views}
        path = os.path.join(OUTPUT_DIR, f"month_{kind}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        for key, v in views.items():
            a = v.get("analysis")
            if a:
                s = a["summary"]
                print(f"[Month/{kind}] {key:8s} {v['name']:10s} spending {s['spending']:>4} creatives | spend Rs {s['spend']:>11,.0f} "
                      f"| ROAS {s['roas']:.2f}x | uploaded {v['uploaded']['count'] if v['uploaded'] else 'n/a'}")
            else:
                print(f"[Month/{kind}] {key:8s} {v['name']:10s} no data yet")
        print(f"[Month/{kind}] Saved to {path}")


if __name__ == "__main__":
    run()
