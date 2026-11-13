"""
Current-month tracking: upload-month badges, the "<Month>" tab, per-category "best this month".

Nothing here is tied to a particular month. Every function takes the run date, and the month is
worked out from it, so December follows November and January follows December with no edits.

The upload month of an ad comes from its name prefix: "November2026-...", "Nov2026-...",
"Sept2026_..." (month name or 3-letter abbreviation, then the 4-digit year). Month AND year are
matched together, so November 2026 is never confused with November 2027.

Dependency-free apart from taxonomy.py (formatting helpers).
"""
import calendar
import json
import os
import re
from datetime import date, datetime, timedelta
from html import escape

from taxonomy import fmt_inr, ordered_categories

TODAY = None          # tests pin the date here; None = the real date in IST


def today_ist():
    """The run date in IST (the cron fires at 03:30 UTC = 09:00 IST, so this equals the UTC date too)."""
    return TODAY or (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()


# ── thresholds (config.yaml -> month_tab, every key optional) ────────────────

DEFAULTS = {
    "card_min_spend": 1000,         # month-tab cards need this much spend (new ads are small)
    "min_purchases": 2,             # purchases needed before a creative is ranked by ROAS
    "scaling_spend": 10000,
    "scaling_roas": 2.0,
    "early_spend": 1000,
    "early_purchases": 2,
    "early_roas": 2.0,
    "notworking_spend": 3000,
    "notworking_roas": 1.5,
    "best_overall_min_spend": 5000,  # "best overall" compares against ads at least this big
    "max_cards": 6,
    "max_decisions": 10,
}


def month_cfg(cfg):
    out = dict(DEFAULTS)
    out.update((cfg or {}).get("month_tab") or {})
    return out


# ── upload month from the ad name ────────────────────────────────────────────

_MONTH_WORDS = {}
for _i in range(1, 13):
    _name = calendar.month_name[_i].lower()
    _MONTH_WORDS[_name] = _i
    _MONTH_WORDS[_name[:3]] = _i
_MONTH_WORDS["sept"] = 9
_VINTAGE_RE = re.compile(r"^\s*([A-Za-z]{3,9})(\d{4})(?=[-_ .]|$)")


def parse_vintage(ad_name):
    """(year, month) from 'November2026-...' / 'Nov2026-...' / 'Sept2026_...'; None when absent
    (for example the evergreen 'Pros-TSQ-DPA-...' catalog ads)."""
    m = _VINTAGE_RE.match(ad_name or "")
    if not m:
        return None
    month = _MONTH_WORDS.get(m.group(1).lower())
    return (int(m.group(2)), month) if month else None


def prev_month(ym):
    y, m = ym
    return (y - 1, 12) if m == 1 else (y, m - 1)


def month_name(ym):
    return calendar.month_name[ym[1]]


def month_first(ym):
    return date(ym[0], ym[1], 1)


def name_prefixes(ym):
    """Every spelling of 'Month+Year' used in ad names, for searching Meta by name."""
    full = f"{calendar.month_name[ym[1]]}{ym[0]}"
    abbr = f"{calendar.month_abbr[ym[1]]}{ym[0]}"
    out = [full]
    if abbr != full:
        out.append(abbr)
    if ym[1] == 9:
        out.append(f"Sept{ym[0]}")
    return out


def month_windows(today):
    """
    The two views. Data always ends YESTERDAY (Meta's current day is partial).

      current  : this month, from the 1st to yesterday. Has no data on the 1st (until is None).
      previous : last month's uploads, counted from the 1st of last month to yesterday, so it shows
                 how they are doing NOW, including this month's spend.
    """
    until = today - timedelta(days=1)
    cur = (today.year, today.month)
    prev = prev_month(cur)
    cur_has_data = month_first(cur) <= until
    return {
        "current": {"ym": cur, "since": month_first(cur), "until": until if cur_has_data else None},
        "previous": {"ym": prev, "since": month_first(prev), "until": until},
    }


def vintage_label(v, today=None):
    """'JUL', or 'JUL 25' when the ad is from a different year to today."""
    if not v:
        return ""
    today = today or today_ist()
    return calendar.month_abbr[v[1]].upper() + ("" if v[0] == today.year else f" {str(v[0])[2:]}")


def vintage_chip(ad_name, today=None):
    """Small upload-month badge for the top-right of an ad card. Blue = uploaded this month."""
    v = parse_vintage(ad_name)
    if not v:
        return ""
    today = today or today_ist()
    bg, fg = ("#1d4ed8", "#ffffff") if v == (today.year, today.month) else ("#262626", "#cbd5e1")
    return (f'<div title="Uploaded {month_name(v)} {v[0]}" style="position:absolute;top:7px;right:7px;z-index:10;'
            f'padding:2px 6px;border-radius:3px;font-size:9px;font-weight:700;background:{bg};color:{fg};">'
            f'{vintage_label(v, today)}</div>')


# ── tiers ────────────────────────────────────────────────────────────────────

TIER_KEYS = ["scaling", "early", "proving", "notworking"]
TIERS = {
    "scaling":    {"name": "Scaling",       "color": "#22c55e"},
    "early":      {"name": "Early winner",  "color": "#86efac"},
    "proving":    {"name": "Still proving", "color": "#94a3b8"},
    "notworking": {"name": "Not working",   "color": "#ef4444"},
    "nospend":    {"name": "No spend yet",  "color": "#3f3f46"},
}
TIER_BADGES = {   # label, background, text colour for the card badge
    "scaling":    ("SCALING", "#22c55e", "#000000"),
    "early":      ("EARLY WINNER", "#86efac", "#000000"),
    "proving":    ("STILL PROVING", "#94a3b8", "#000000"),
    "notworking": ("NOT WORKING", "#ef4444", "#000000"),
}


def tier_rules(c):
    return {
        "scaling": f'{fmt_inr(c["scaling_spend"])}+ spend and ROAS {c["scaling_roas"]:g}x or better',
        "early": f'{fmt_inr(c["early_spend"])}\u2013{fmt_inr(c["scaling_spend"])} spend, {c["early_purchases"]}+ purchases, ROAS {c["early_roas"]:g}x or better',
        "proving": "Not clear either way yet",
        "notworking": f'{fmt_inr(c["notworking_spend"])}+ spend with ROAS under {c["notworking_roas"]:g}x, or no purchases',
        "nospend": "Uploaded, but Meta has not delivered it",
    }


def classify(spend, roas, purchases, c):
    if spend >= c["scaling_spend"] and roas >= c["scaling_roas"]:
        return "scaling"
    if spend >= c["early_spend"] and purchases >= c["early_purchases"] and roas >= c["early_roas"]:
        return "early"
    if spend >= c["notworking_spend"] and (purchases == 0 or roas < c["notworking_roas"]):
        return "notworking"
    return "proving"


# ── analysis of ONE window ───────────────────────────────────────────────────

def analyse(by_cat, ym, cfgm, cat_meta=None, today=None):
    """
    by_cat : {category: [ads]} for ONE time window, ads already merged by creative name INSIDE each
             category (the same shape the daily pipeline builds)
    ym     : (year, month) of the uploads in focus

    Returns the numbers behind one view: summary tiles, tiers, a per-category scoreboard,
    best creatives and the 'needs a decision' list.
    """
    cat_meta = cat_meta or {}
    today = today or today_ist()
    all_spend = sum(a["spend"] for ads in by_cat.values() for a in ads)
    older_spend = older_cv = 0.0
    creatives, cats = {}, {}

    for cat, ads in by_cat.items():
        r = cats.setdefault(cat, {"spend": 0.0, "cv": 0.0, "n": 0, "o_spend": 0.0, "o_cv": 0.0,
                                  "best": None, "best_all": None})
        for a in ads:
            v = parse_vintage(a["ad_name"])
            if v == ym:
                r["spend"] += a["spend"]
                r["cv"] += a["conv_value"]
                r["n"] += 1
                if a["spend"] >= cfgm["card_min_spend"] and a["purchases"] >= cfgm["min_purchases"]:
                    if r["best"] is None or a["roas"] > r["best"]["roas"]:
                        r["best"] = a
                c = creatives.setdefault(a["ad_name"], {"spend": 0.0, "cv": 0.0, "purchases": 0,
                                                        "top": a, "cat": cat, "cat_spend": 0.0})
                c["spend"] += a["spend"]
                c["cv"] += a["conv_value"]
                c["purchases"] += a["purchases"]
                if a["spend"] > c["cat_spend"]:       # filed under the category it spent most in
                    c["cat_spend"], c["cat"], c["top"] = a["spend"], cat, a
            elif v is not None and v < ym:
                r["o_spend"] += a["spend"]
                r["o_cv"] += a["conv_value"]
                older_spend += a["spend"]
                older_cv += a["conv_value"]
            if v is not None and a["spend"] >= cfgm["best_overall_min_spend"] and a["purchases"] >= cfgm["min_purchases"]:
                if r["best_all"] is None or a["roas"] > r["best_all"]["roas"]:
                    r["best_all"] = a

    tiers = {k: {"count": 0, "spend": 0.0} for k in TIER_KEYS}
    cards, decisions = [], []
    for name, c in creatives.items():
        roas = c["cv"] / c["spend"] if c["spend"] else 0.0
        tier = classify(c["spend"], roas, c["purchases"], cfgm)
        tiers[tier]["count"] += 1
        tiers[tier]["spend"] += c["spend"]
        card = {"ad_id": c["top"].get("ad_id", ""), "ad_name": name, "adset_name": c["top"].get("adset_name", ""),
                "spend": round(c["spend"], 2), "roas": round(roas, 4), "conv_value": round(c["cv"], 2),
                "purchases": c["purchases"], "category": c["cat"], "tier": tier}
        if c["spend"] >= cfgm["card_min_spend"] and c["purchases"] >= cfgm["min_purchases"]:
            cards.append(card)
        if tier == "notworking":
            decisions.append(card)
    cards.sort(key=lambda x: -x["roas"])
    decisions.sort(key=lambda x: -x["spend"])

    spend = sum(c["spend"] for c in creatives.values())
    cv = sum(c["cv"] for c in creatives.values())
    rows = []
    for cat, r in cats.items():
        if cat == "Other" or r["n"] == 0:
            continue
        meta = cat_meta.get(cat, {})
        best, ba = r["best"], r["best_all"]
        rows.append({
            "category": cat, "group": meta.get("group", ""), "emoji": meta.get("emoji", ""),
            "order": meta.get("order", 10 ** 6), "uploaded": None, "spending": r["n"],
            "spend": round(r["spend"], 2), "roas": round(r["cv"] / r["spend"], 4) if r["spend"] else 0,
            "older_roas": round(r["o_cv"] / r["o_spend"], 4) if r["o_spend"] else None,
            "best": {"roas": round(best["roas"], 4), "ad_name": best["ad_name"], "spend": best["spend"]} if best else None,
            "best_all": ({"roas": round(ba["roas"], 4), "label": vintage_label(parse_vintage(ba["ad_name"]), today),
                          "is_view": parse_vintage(ba["ad_name"]) == ym, "ad_name": ba["ad_name"]} if ba else None),
        })
    rows.sort(key=lambda x: -x["spend"])
    return {
        "summary": {"spending": len(creatives), "spend": round(spend, 2),
                    "share": round(spend / all_spend * 100, 1) if all_spend else 0,
                    "roas": round(cv / spend, 4) if spend else 0,
                    "older_roas": round(older_cv / older_spend, 4) if older_spend else None,
                    "units": int(sum(c["purchases"] for c in creatives.values()))},
        "tiers": {k: {"count": v["count"], "spend": round(v["spend"], 2)} for k, v in tiers.items()},
        "categories": rows,
        "cards": cards[:cfgm["max_cards"]],
        "decisions": decisions[:cfgm["max_decisions"]],
        "all_spend": round(all_spend, 2),
    }


def category_month_info(ads, ym, cfgm, today=None):
    """Per-category 'this month' numbers for the daily/weekly 7-day data (stored on each category)."""
    today = today or today_ist()
    cur = [a for a in ads if parse_vintage(a["ad_name"]) == ym]
    total = sum(a["spend"] for a in ads)
    spend = sum(a["spend"] for a in cur)
    q = [a for a in cur if a["spend"] >= cfgm["card_min_spend"] and a["purchases"] >= cfgm["min_purchases"]]
    best = max(q, key=lambda a: a["roas"]) if q else None
    allq = [a for a in ads if parse_vintage(a["ad_name"]) and a["spend"] >= cfgm["best_overall_min_spend"]
            and a["purchases"] >= cfgm["min_purchases"]]
    ba = max(allq, key=lambda a: a["roas"]) if allq else None
    return {
        "name": month_name(ym), "abbr": calendar.month_abbr[ym[1]].upper(), "ads": len(cur),
        "spend": round(spend, 2), "share": round(spend / total * 100, 1) if total else 0,
        "best": best,
        "best_all": ({"roas": round(ba["roas"], 4), "label": vintage_label(parse_vintage(ba["ad_name"]), today),
                      "is_view": parse_vintage(ba["ad_name"]) == ym, "ad_name": ba["ad_name"]} if ba else None),
    }


# ── data file ────────────────────────────────────────────────────────────────

def load_month_data(kind, base_dir):
    """data/month_<kind>.json, or None when the pull has not produced it yet."""
    path = os.path.join(base_dir, "data", f"month_{kind}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("views") else None
    except (OSError, ValueError):
        return None


# ── rendering helpers ────────────────────────────────────────────────────────

def _roas_color(roas, cfg):
    t = (cfg or {}).get("roas_thresholds") or {"good": 2.5, "ok": 2.0, "warn": 1.5}
    if roas >= t["good"]:
        return "#22c55e"
    if roas >= t["ok"]:
        return "#86efac"
    if roas >= t["warn"]:
        return "#f59e0b"
    return "#ef4444"


def wrap_card(card_html, label, bg, fg, outline=None):
    """Put a badge top-left of an existing ad card (works for statics and videos cards alike)."""
    ring = f"outline:1px solid {outline};outline-offset:-1px;border-radius:10px;" if outline else ""
    return (f'<div style="display:grid;position:relative;min-width:0;{ring}">'
            f'<div style="position:absolute;top:7px;left:7px;z-index:11;padding:2px 6px;border-radius:3px;'
            f'font-size:9px;font-weight:700;background:{bg};color:{fg};">{escape(label)}</div>{card_html}</div>')


def month_best_card(card_html):
    return wrap_card(card_html, "\u2605 BEST THIS MONTH", "#1d4ed8", "#ffffff", outline="#3b82f6")


def category_month_strip(cat_data):
    """One line above a category's cards: best overall vs best this month, and the month's share."""
    m = cat_data.get("month")
    if not m:
        return ""
    chip = lambda txt, cur: (f'<span style="padding:2px 6px;font-size:9px;font-weight:700;margin-left:4px;border-radius:3px;'
                             f'background:{"#1d4ed8" if cur else "#262626"};color:{"#ffffff" if cur else "#cbd5e1"};">{escape(txt)}</span>')
    parts = ['<span style="padding:2px 6px;font-size:9px;font-weight:700;letter-spacing:.06em;background:#1d4ed8;color:#ffffff;border-radius:3px;">NEW</span>']
    ba, best = m.get("best_all"), m.get("best")
    parts.append('<span style="color:#9a9a9a;">Best overall: ' + (f'<b style="color:#e8e8e8;">{ba["roas"]:.2f}x</b>{chip(ba["label"], ba["is_view"])}' if ba else "\u2014") + "</span>")
    parts.append(f'<span style="color:#9a9a9a;">Best {escape(m["name"])}: ' + (f'<b style="color:#e8e8e8;">{best["roas"]:.2f}x</b>{chip(m["abbr"], True)}' if best else "none yet") + "</span>")
    parts.append(f'<span style="color:#9a9a9a;">{escape(m["name"])} uploads: <b style="color:#e8e8e8;">{m["share"]:.0f}%</b> of this category\'s spend</span>')
    return ('<div style="display:flex;gap:10px 22px;flex-wrap:wrap;align-items:center;margin:0 0 14px;padding:9px 14px;'
            'border:1px solid #1d4ed8;background:#0d1526;border-radius:8px;font-size:12px;">' + "".join(parts) + "</div>")


# ── the month tab ────────────────────────────────────────────────────────────

def _tile(label, value, sub, color=None):
    return ('<div style="flex:1 1 150px;background:#121212;border:1px solid #252525;border-radius:10px;padding:14px 16px;">'
            f'<div style="font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#9a9a9a;">{label}</div>'
            f'<div style="font-size:23px;font-weight:700;margin-top:4px;{"color:" + color + ";" if color else ""}">{value}</div>'
            f'<div style="font-size:11px;color:#9a9a9a;margin-top:2px;">{sub}</div></div>')


_TH = 'style="padding:10px 14px;font-weight:600;text-align:left;"'


def _fmt_day(d):
    return f'{d.day} {calendar.month_abbr[d.month]}' if isinstance(d, date) else str(d)


def view_html(v, card_fn, cfg):
    """One view (current or previous month)."""
    name = escape(v["name"])
    if not v.get("has_data"):
        return (f'<div style="padding:46px 20px;text-align:center;color:#9a9a9a;font-size:13px;line-height:1.7;">'
                f'<b style="color:#e8e8e8;font-size:15px;">No data for {name} yet</b><br>The month has only just started. '
                f'Check back tomorrow, or switch to last month to see how those uploads are doing now.</div>')
    a = v.get("analysis") or {}
    s = a.get("summary") or {}
    up = v.get("uploaded")
    c = month_cfg(cfg)
    since = date.fromisoformat(v["since"]); until = date.fromisoformat(v["until"])
    if v["key"] == "current":
        scope = f'<b style="color:#ddd;">Month to date: {_fmt_day(since)} \u2013 {_fmt_day(until)}.</b> '
    else:
        scope = (f'<b style="color:#ddd;">How {name} uploads are doing now:</b> spend counted from {_fmt_day(since)} '
                 f'to {_fmt_day(until)}, including this month. ')
    note = (f'<div style="margin:0 0 18px;padding:10px 14px;border:1px solid #252525;background:#121212;border-radius:8px;'
            f'font-size:12px;color:#9a9a9a;line-height:1.6;">{scope}Only ads uploaded in {name} (read from the ad name, e.g. '
            f'{escape(v["example_prefix"])}-\u2026). Cards need <b style="color:#ddd;">\u20b9{c["card_min_spend"]:,.0f}+ spend</b>; '
            f'smaller ads still count in every total.</div>')
    if not s.get("spending"):
        return note + (f'<div style="padding:30px 20px;text-align:center;color:#9a9a9a;font-size:13px;">'
                       f'No {name} uploads with spend in this window yet.</div>')

    tiles = '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:28px;">'
    if up:
        tiles += _tile(f"Uploaded in {name}", f'{up["count"]:,}', "all creatives created this month")
    older = f'older content: {s["older_roas"]:.2f}x' if s.get("older_roas") else "no older content to compare"
    tiles += _tile("Spending", f'{s["spending"]:,}', (f'{s["spending"] / up["count"] * 100:.0f}% of uploads have spend' if up and up["count"] else "creatives with spend"))
    tiles += _tile("Spend", fmt_inr(s["spend"]), f'{s["share"]:.0f}% of all spend')
    tiles += _tile("Blended ROAS", f'{s["roas"]:.2f}x', older, _roas_color(s["roas"], cfg))
    tiles += _tile("Units sold", f'{s["units"]:,}', f"from {name} uploads") + "</div>"
    if not up:
        tiles = tiles.replace("</div>", "</div>", 1)

    # funnel
    tiers = a["tiers"]
    segs = [(k, tiers[k]["count"], tiers[k]["spend"]) for k in TIER_KEYS]
    if up:
        segs.append(("nospend", up["no_spend"], 0.0))
    rules = tier_rules(c)
    bar = "".join(f'<div style="flex:{max(n, 0)} 1 0;background:{TIERS[k]["color"]};"></div>' for k, n, _ in segs if n > 0)
    legend = "".join(
        f'<div style="flex:1 1 180px;background:#121212;border:1px solid #252525;border-radius:8px;padding:12px 14px;">'
        f'<div style="display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;"><span style="width:10px;height:10px;border-radius:2px;background:{TIERS[k]["color"]};display:inline-block;"></span>{TIERS[k]["name"]}</div>'
        f'<div style="font-size:20px;font-weight:700;margin-top:6px;">{n:,} <span style="font-size:12px;font-weight:500;color:#9a9a9a;">\u00b7 {fmt_inr(sp)}</span></div>'
        f'<div style="font-size:11px;color:#9a9a9a;margin-top:4px;line-height:1.5;">{rules[k]}</div></div>'
        for k, n, sp in segs)
    funnel = (f'<div style="font-size:15px;font-weight:600;">Are they working?</div>'
              f'<div style="font-size:11px;color:#9a9a9a;margin:3px 0 12px;">Every {name} upload, sorted by how it is doing so far</div>'
              f'<div style="display:flex;height:30px;border-radius:6px;overflow:hidden;gap:2px;margin-bottom:14px;">{bar}</div>'
              f'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:34px;">{legend}</div>')

    # scoreboard
    body = ""
    for r in a["categories"]:
        idle = r["spending"] == 0                    # uploaded, but nothing has spent yet
        nc = "#9a9a9a" if idle else _roas_color(r["roas"], cfg)
        spend_txt = "\u2014" if idle else fmt_inr(r["spend"])
        roas_txt = "\u2014" if idle else "%.2fx" % r["roas"]
        older = f'{r["older_roas"]:.2f}x' if r.get("older_roas") else "\u2014"
        best = f'{r["best"]["roas"]:.2f}x' if r.get("best") else "\u2014"
        ba = r.get("best_all")
        best_all = (f'{ba["roas"]:.2f}x <span style="padding:2px 6px;font-size:9px;font-weight:700;margin-left:4px;border-radius:3px;'
                    f'background:{"#1d4ed8" if ba["is_view"] else "#262626"};color:{"#ffffff" if ba["is_view"] else "#cbd5e1"};">{escape(ba["label"])}</span>') if ba else "\u2014"
        up_txt = f'{r["uploaded"]:,}' if r.get("uploaded") is not None else "\u2014"
        body += (f'<tr style="border-top:1px solid #1d1d1d;"><td style="padding:10px 14px;font-weight:600;">{r.get("emoji", "")} {escape(r["category"])}</td>'
                 f'<td style="padding:10px 14px;color:#ccc;">{up_txt}</td><td style="padding:10px 14px;color:#ccc;">{r["spending"]:,}</td>'
                 f'<td style="padding:10px 14px;color:#ddd;">{spend_txt}</td>'
                 f'<td style="padding:10px 14px;font-weight:700;color:{nc};">{roas_txt}</td>'
                 f'<td style="padding:10px 14px;color:#ccc;">{older}</td><td style="padding:10px 14px;color:#ddd;">{best}</td>'
                 f'<td style="padding:10px 14px;color:#ddd;">{best_all}</td></tr>')
    score = (f'<div style="font-size:15px;font-weight:600;">{name} vs older content, by category</div>'
             f'<div style="font-size:11px;color:#9a9a9a;margin:3px 0 12px;">Is this month\'s creative keeping up with what already works? Older = uploaded in earlier months, same window.</div>'
             '<div style="background:#121212;border:1px solid #252525;border-radius:10px;overflow-x:auto;margin-bottom:34px;">'
             '<table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:860px;"><thead>'
             f'<tr style="color:#9a9a9a;font-size:10px;text-transform:uppercase;letter-spacing:.06em;"><th {_TH}>Category</th><th {_TH}>Uploaded</th><th {_TH}>Spending</th>'
             f'<th {_TH}>Spend</th><th {_TH}>{name[:3]} ROAS</th><th {_TH}>Older ROAS</th><th {_TH}>Best {name[:3]} creative</th><th {_TH}>Best overall</th></tr></thead>'
             f'<tbody>{body}</tbody></table></div>')

    # best cards
    cards = "".join(card_fn(x, x["tier"]) for x in a["cards"])
    best = (f'<div style="font-size:15px;font-weight:600;">Best of {name}</div>'
            f'<div style="font-size:11px;color:#9a9a9a;margin:3px 0 12px;">Highest ROAS among {name} uploads with at least {c["min_purchases"]} purchases, across categories</div>'
            + (f'<div class="cgrid" style="margin-bottom:34px;">{cards}</div>' if cards else
               f'<div style="padding:18px;border:1px dashed #333;border-radius:8px;color:#9a9a9a;font-size:12px;margin-bottom:34px;">No {name} upload has {c["min_purchases"]}+ purchases at \u20b9{c["card_min_spend"]:,.0f}+ spend yet.</div>'))

    # decisions
    drows = ""
    for d in a["decisions"]:
        why = "No purchases" if d["purchases"] == 0 else "ROAS under %gx" % c["notworking_roas"]
        drows += (
            f'<tr style="border-top:1px solid #1d1d1d;"><td style="padding:10px 14px;color:#ddd;">{escape(d["ad_name"][:90])}</td>'
            f'<td style="padding:10px 14px;color:#ccc;">{escape(d["category"])}</td><td style="padding:10px 14px;color:#ddd;">{fmt_inr(d["spend"])}</td>'
            f'<td style="padding:10px 14px;font-weight:700;color:#fca5a5;">{d["roas"]:.2f}x</td><td style="padding:10px 14px;color:#ccc;">{d["purchases"]:,}</td>'
            f'<td style="padding:10px 14px;color:#fca5a5;">{why} at {fmt_inr(d["spend"])} spend</td></tr>')
    decide = ""
    if drows:
        decide = (f'<div style="font-size:15px;font-weight:600;">Needs a decision</div>'
                  f'<div style="font-size:11px;color:#9a9a9a;margin:3px 0 12px;">{name} uploads with {fmt_inr(c["notworking_spend"])}+ spend and weak ROAS or no purchases: candidates to cut or rework</div>'
                  '<div style="background:#121212;border:1px solid #252525;border-radius:10px;overflow-x:auto;margin-bottom:24px;">'
                  '<table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:860px;"><thead>'
                  f'<tr style="color:#9a9a9a;font-size:10px;text-transform:uppercase;letter-spacing:.06em;"><th {_TH}>Creative</th><th {_TH}>Category</th><th {_TH}>Spend</th>'
                  f'<th {_TH}>ROAS</th><th {_TH}>Units sold</th><th {_TH}>Why it is flagged</th></tr></thead><tbody>{drows}</tbody></table></div>')

    npx = v.get("no_prefix") or {}
    warn = ""
    if npx.get("ads"):
        warn = (f'<div style="font-size:11px;color:#9a9a9a;margin-top:8px;">{npx["ads"]} ads ({fmt_inr(npx["spend"])}) have no month prefix in their name '
                f'and are not counted here, e.g. {escape(", ".join(npx.get("examples", [])[:2]))}</div>')
    return note + tiles + funnel + score + best + decide + warn


def month_tab_html(month_data, card_fn, cfg):
    """The whole tab: a Current / Previous switch and both views. Falls back to the previous
    view on the 1st of the month, when the new month has no data yet."""
    views = month_data["views"]
    cur, prev = views["current"], views["previous"]
    show = "cur" if cur.get("has_data") else "prev"
    btn = lambda which, v, tag: (f'<button class="filter-btn{" active" if show == which else ""}" id="mvb-{which}" '
                                 f'onclick="mvShow(\'{which}\')">{escape(v["name"])} ({tag})</button>')
    switch = f'<div class="filter-bar" style="margin-bottom:18px;">{btn("cur", cur, "this month")}{btn("prev", prev, "last month")}</div>'
    stamp = (f'<div style="font-size:11px;color:#9a9a9a;margin:-6px 0 16px;">Updated {escape(str(month_data.get("generated_at", ""))[:10])}</div>')
    script = ('<script>function mvShow(w){document.getElementById("mv-cur").style.display=w==="cur"?"block":"none";'
              'document.getElementById("mv-prev").style.display=w==="prev"?"block":"none";'
              'document.getElementById("mvb-cur").classList.toggle("active",w==="cur");'
              'document.getElementById("mvb-prev").classList.toggle("active",w==="prev");}</script>')
    return (switch + stamp
            + f'<div id="mv-cur" style="display:{"block" if show == "cur" else "none"};">{view_html(cur, card_fn, cfg)}</div>'
            + f'<div id="mv-prev" style="display:{"block" if show == "prev" else "none"};">{view_html(prev, card_fn, cfg)}</div>'
            + script)


# ── weekly report block ──────────────────────────────────────────────────────

def weekly_month_block(data, cfg):
    """'Best of this month' block for the top of a weekly format tab. Uses the same week window as the rest of the report."""
    snap = (data or {}).get("month_snapshot")
    cats = ordered_categories((data or {}).get("categories", {}))
    if not snap or not snap.get("summary", {}).get("spending"):
        return ""
    s, t, name = snap["summary"], snap["tiers"], escape(snap["name"])
    good = t["scaling"]["count"] + t["early"]["count"]
    older = f'older content: {s["older_roas"]:.2f}x' if s.get("older_roas") else "no older content to compare"
    tiles = ('<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px;">'
             + _tile("Share of weekly spend", f'{s["share"]:.0f}%', f"going to {name} uploads")
             + _tile(f"{name} ROAS", f'{s["roas"]:.2f}x', older, _roas_color(s["roas"], cfg))
             + _tile("Scaling or early winners", f"{good:,}", f'of {s["spending"]:,} spending uploads', "#86efac")
             + _tile("Not working", f'{t["notworking"]["count"]:,}', f'{fmt_inr(t["notworking"]["spend"])} spent, worth a look', "#fca5a5") + "</div>")
    rows = ""
    for cat, cd in cats.items():
        m = cd.get("month")
        if not m or not (m.get("best") or m.get("best_all")) or not cd.get("top_ads"):
            continue
        ba, best = m.get("best_all"), m.get("best")
        gap, gc = "\u2014", "#9a9a9a"
        if ba and best:
            d = best["roas"] - ba["roas"]
            gap, gc = ("leads", "#86efac") if d >= -0.005 else (f"{d:+.2f}x", "#fca5a5" if d < -0.3 else "#fcd34d")
        ba_roas = ("%.2fx" % ba["roas"]) if ba else "\u2014"
        best_roas = ("%.2fx" % best["roas"]) if best else "none yet"
        ba_view = bool(ba and ba["is_view"])
        ba_label = escape(ba["label"]) if ba else ""
        best_spend = fmt_inr(best["spend"]) if best else "\u2014"
        rows += (f'<tr style="border-top:1px solid #1d1d1d;"><td style="padding:10px 14px;font-weight:600;">{cd.get("emoji", "")} {escape(cat)}</td>'
                 f'<td style="padding:10px 14px;font-weight:700;color:#ddd;">{ba_roas}</td>'
                 f'<td style="padding:10px 14px;"><span style="padding:2px 6px;font-size:9px;font-weight:700;border-radius:3px;'
                 f'background:{"#1d4ed8" if ba_view else "#262626"};color:{"#ffffff" if ba_view else "#cbd5e1"};">{ba_label}</span></td>'
                 f'<td style="padding:10px 14px;font-weight:700;color:#ddd;">{best_roas}</td>'
                 f'<td style="padding:10px 14px;color:#ccc;">{best_spend}</td>'
                 f'<td style="padding:10px 14px;font-weight:700;color:{gc};">{gap}</td>'
                 f'<td style="padding:10px 14px;color:#ccc;">{m["share"]:.0f}%</td></tr>')
    table = ""
    if rows:
        table = ('<div style="background:#121212;border:1px solid #252525;border-radius:10px;overflow-x:auto;margin-bottom:26px;">'
                 '<table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:900px;"><thead>'
                 f'<tr style="color:#9a9a9a;font-size:10px;text-transform:uppercase;letter-spacing:.06em;"><th {_TH}>Category</th><th {_TH}>Best overall this week</th><th {_TH}>Uploaded</th>'
                 f'<th {_TH}>Best {name} upload</th><th {_TH}>Its spend</th><th {_TH}>Gap</th><th {_TH}>{name} share of category spend</th></tr></thead>'
                 f'<tbody>{rows}</tbody></table></div>')
    return ('<div style="margin-bottom:6px;display:flex;align-items:center;gap:10px;">'
            '<span style="padding:2px 6px;font-size:9px;font-weight:700;letter-spacing:.06em;background:#1d4ed8;color:#ffffff;border-radius:3px;">NEW</span>'
            f'<div style="font-size:15px;font-weight:600;">Best of this month: {name} uploads this week</div></div>'
            '<div style="font-size:11px;color:#9a9a9a;margin:3px 0 14px;">Same week window as the rest of this report, so the numbers compare directly with the overall best performers</div>'
            + tiles + table)
