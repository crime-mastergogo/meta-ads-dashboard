"""
main_month.py — Current-month tracking data (feeds the "<Month>" tab on the Statics and Videos dashboards).

Nothing in here names a month. Both months are worked out from today's date (IST), so on 1 December
the tab becomes "December", with "November" as the previous-month view, and so on, forever.

  current view  : ads uploaded THIS month, spend from the 1st to yesterday
  previous view : ads uploaded LAST month, spend from the 1st of last month to yesterday
                  (how they are doing now, including this month's spend)
  previous_only : the same, but counting only spend made inside last month itself

One insights call (time_increment=monthly) serves both views. A second, optional call lists the ads
uploaded this month and last, so uploads that never delivered a single rupee can be counted too.
If that second call fails the tab still works; it just cannot show "uploaded" and "no spend yet".

Writes data/month_statics.json and data/month_videos.json.
"""
import hashlib
import json
import os
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime

import yaml

import contentmap as cm
import monthview as mv
from meta_api import (fetch_ad_insights_monthly, fetch_ads_by_name_prefix, fetch_previews_bulk,
                      categorise_ads, consolidate_by_creative)
from taxonomy import ad_format, category_orders, drop_ignored, redact

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
OUTPUT_DIR = os.path.join(BASE_DIR, "data")

CATALOG_MARKERS = ("dpa", "carousel", "catalog", "collection")   # evergreen ads: no month prefix by design


def write_status(ok, error=None):
    """data/month_status.json: lets the dashboard show WHY the month tab is empty, not just the Actions log."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, "month_status.json"), "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "error": error, "at": datetime.utcnow().isoformat()}, f)


# ── tier movement: a small snapshot of every creative's tier is saved each day and compared with the previous day ──
CODE = {"scaling": "s", "early": "e", "proving": "p", "notworking": "n"}
LABEL = {v: k for k, v in CODE.items()}
HISTORY_KEEP = 3                      # days of snapshots kept (names are stored as 10-character hashes, so the files stay small)
MOVE_LIST_MAX = 40


def name_hash(name):
    return hashlib.blake2s(name.lower().encode("utf-8"), digest_size=5).hexdigest()


def load_history(kind):
    return (cm.load_json(os.path.join(OUTPUT_DIR, "month_history_" + kind + ".json")) or {}).get("snapshots") or {}


def save_history(kind, snaps, today_s, today_snap):
    snaps = dict(snaps)
    snaps[today_s] = today_snap
    keep = sorted(snaps)[-HISTORY_KEEP:]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, "month_history_" + kind + ".json"), "w", encoding="utf-8") as f:
        json.dump({"snapshots": {d: snaps[d] for d in keep}}, f, separators=(",", ":"))


def latest_prior(snaps, today_s):
    """The newest snapshot from an EARLIER day (a second run on the same day never compares with itself)."""
    older = [d for d in snaps if d < today_s]
    return (max(older), snaps[max(older)]) if older else (None, None)


def pick_prior_view(prior, key, ym_key, scope):
    """Yesterday's view of the same month and window type (same key first; across keys when the month rolled over)."""
    if not prior:
        return None
    v = prior.get(key)
    if v and v.get("ym") == ym_key and v.get("scope") == scope:
        return v
    for v in prior.values():
        if v.get("ym") == ym_key and v.get("scope") == scope:
            return v
    return None


def movement(tier_of, prior_date, prior_view):
    """Who changed tier since the previous snapshot. {"since": None} when there is nothing to compare with yet."""
    if not prior_view:
        return {"since": None}
    prev = prior_view["t"]
    lists = defaultdict(list)
    for name, (tier, cat, spend, roas) in tier_of.items():
        new, old = CODE[tier], prev.get(name_hash(name))
        entry = {"n": name, "c": cat, "s": int(round(spend)), "r": round(roas, 2), "from": LABEL.get(old), "to": tier}
        if old is None:
            lists["started"].append(entry)
        elif old == new:
            continue
        elif new == "s":
            lists["newly_scaling"].append(entry)
        elif new == "e" and old in ("p", "n"):
            lists["newly_early"].append(entry)
        elif new == "n":
            lists["slipped"].append(entry)
        elif old == "s":
            lists["cooled"].append(entry)
        elif old == "n" and new == "p":
            lists["recovered"].append(entry)
    for k in lists:
        lists[k].sort(key=lambda e: -e["s"])
    return {"since": prior_date, "counts": {k: len(v) for k, v in lists.items()},
            "lists": {k: v[:MOVE_LIST_MAX] for k, v in lists.items()}}


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ym_key(ym):
    return f"{ym[0]}-{ym[1]:02d}"


def build_view(key, windows, fmt, fmt_rows, uploads, categories, cat_meta, cfgm, today, ctx=None):
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

    ups = uploads.get("previous" if key == "previous_only" else key) if uploads is not None else None
    if ups is not None:
        ups = [u for u in ups if ad_format(u) == fmt]
        names = {u["ad_name"] for u in ups}
        spending = {a["ad_name"] for ads in by_cat.values() for a in ads if mv.parse_vintage(a["ad_name"]) == ym}
        if len(names) < len(spending):
            # Every spending creative was uploaded, so the upload count can never be below the spending count.
            # If it is, the name search came back incomplete: show no upload numbers rather than wrong ones.
            print(f"[Month] WARNING upload list looks incomplete for {view['name']} ({len(names)} found, "
                  f"{len(spending)} are spending). Hiding the 'uploaded' / 'no spend yet' numbers.")
        else:
            view["uploaded"] = {"count": len(names), "no_spend": len(names - spending)}
            up_by_cat, _ig = drop_ignored(categorise_ads(ups, categories), categories)
            counts = {cat: len({u["ad_name"] for u in lst}) for cat, lst in up_by_cat.items() if cat != "Other"}
            # the "No spend yet" tier: uploaded, but nothing delivered
            name_cat = {}
            for ucat, ulist in up_by_cat.items():
                for u in ulist:
                    name_cat.setdefault(u["ad_name"], ucat)
            idle = sorted(names - spending)
            analysis["tier_ads"]["nospend"] = [{"n": n, "c": "Unmapped" if name_cat.get(n, "Other") == "Other" else name_cat[n]} for n in idle[:400]]
            mix_by = {r["category"]: r for r in analysis["tier_mix"]}
            for ucat, n_idle in Counter("Unmapped" if name_cat.get(n, "Other") == "Other" else name_cat[n] for n in idle).items():
                row = mix_by.get(ucat)
                if row is None:
                    meta = cat_meta.get(ucat, {})
                    row = {"category": ucat, "group": meta.get("group", ""), "emoji": meta.get("emoji", ""),
                           "order": meta.get("order", 10 ** 6), "spend": 0, "counts": {k: 0 for k in ("scaling", "early", "proving", "notworking")}}
                    analysis["tier_mix"].append(row)
                row["counts"]["nospend"] = n_idle
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

    # Concept tags (Type / Theme / Messaging / Hook) from the Google Sheet, for videos: winners by concept,
    # for ALL ads with spend in the window and for the ones uploaded in this month.
    ctx = ctx or {}
    if fmt == "video" and ctx.get("tags"):                       # concept tags on the rows of the "click a tier" lists
        for tier_list in analysis["tier_ads"].values():
            for row in tier_list:
                t = ctx["tags"].get(cm.ad_key(row["n"]))
                if t:
                    row["t"] = t
    if fmt == "video" and ctx.get("tags"):
        winner = ctx["winner_spend"]
        view["content"] = {
            "all": cm.breakdown(cm.aggregate_creatives(by_cat), ctx["tags"], winner),
            "new": cm.breakdown(cm.aggregate_creatives(by_cat, ym, mv.parse_vintage), ctx["tags"], winner),
        }
    elif fmt == "video" and ctx.get("error"):
        view["content_error"] = ctx["error"]

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
        # Fail loudly (red step, reason shown on the dashboard) instead of ending quietly with a green tick.
        # The existing data files are left untouched, so the tab keeps showing the last good data.
        raise RuntimeError(f"Meta returned no insight rows for {prev['since']} to {prev['until']}")

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

    tags_doc = cm.load_json(os.path.join(OUTPUT_DIR, "ad_tags.json"))
    tag_status = cm.load_json(os.path.join(OUTPUT_DIR, "tags_status.json"))
    ct = cfg.get("content_tags") or {}
    ctx = {"tags": (tags_doc or {}).get("tags") or None, "winner_spend": float(ct.get("winner_spend", 10000)),
           "error": (tag_status or {}).get("error") if tag_status and not tag_status.get("ok") else None}
    print("[Month] Concept tags: " + (str(len(ctx["tags"])) + " ads from the sheet" if ctx["tags"] else "none available"))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for fmt, kind, cats_key in (("static", "statics", "statics_categories"), ("video", "videos", "videos_categories")):
        categories = cfg[cats_key]
        order_of = category_orders(categories, cfg.get("group_order"))
        cat_meta = {n: {"group": c.get("group", ""), "emoji": c.get("emoji", ""), "order": order_of.get(n, 10 ** 6)}
                    for n, c in categories.items()}
        fmt_rows = [r for r in rows if ad_format(r) == fmt]
        views = {key: build_view(key, windows, fmt, fmt_rows, uploads, categories, cat_meta, cfgm, today, ctx)
                 for key in ("current", "previous", "previous_only")}

        # movement since the previous day's snapshot, then save today's snapshot
        snaps = load_history(kind)
        prior_date, prior = latest_prior(snaps, today.isoformat())
        today_snap = {}
        for key, v in views.items():
            a = v.get("analysis")
            tier_of = a.pop("tier_of", None) if a else None
            if not tier_of:
                continue
            scope = "only" if key == "previous_only" else "to_date"
            v["movement"] = movement(tier_of, prior_date, pick_prior_view(prior, key, v["ym"], scope))
            today_snap[key] = {"ym": v["ym"], "scope": scope, "t": {name_hash(n): CODE[t[0]] for n, t in tier_of.items()}}
        save_history(kind, snaps, today.isoformat(), today_snap)

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
    write_status(True)


def main():
    """Run, and on ANY failure record the reason where the dashboard can show it. Exits 1 so the Actions
    step is marked failed (it is continue-on-error, so the rest of the daily run still happens)."""
    try:
        run()
        return 0
    except Exception as e:                                   # noqa: BLE001 - we want every failure recorded
        print(redact(traceback.format_exc()))
        write_status(False, redact(f"{type(e).__name__}: {e}")[:600])
        return 1


if __name__ == "__main__":
    sys.exit(main())
