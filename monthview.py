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

from taxonomy import fmt_inr, ordered_categories, redact
from contentmap import content_html, CT_JS

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
    "tier_list_max": 400,           # ads listed per tier when you click a tier
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


def month_name_now():
    t = today_ist()
    return calendar.month_name[t.month]


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
      previous_only : the same last-month view, but counting only spend made inside last month.
    """
    until = today - timedelta(days=1)
    cur = (today.year, today.month)
    prev = prev_month(cur)
    cur_has_data = month_first(cur) <= until
    return {
        "current": {"ym": cur, "since": month_first(cur), "until": until if cur_has_data else None},
        "previous": {"ym": prev, "since": month_first(prev), "until": until},
        # last month on its own: only spend from last month itself, nothing from this month
        "previous_only": {"ym": prev, "since": month_first(prev), "until": month_first(cur) - timedelta(days=1)},
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


def floor_chip(ad):
    """Marks a card that is shown although it spends less than the card floor (a small launch)."""
    floor = ad.get("below_floor")
    if not floor:
        return ""
    return ('<div title="Spends less than the card floor. Shown because this category has few ads above it." '
            'style="position:absolute;top:28px;right:7px;z-index:10;padding:2px 6px;border-radius:3px;font-size:9px;'
            'font-weight:700;background:#3f2d0a;color:#fcd34d;border:1px solid #6b5a14;">'
            f'BELOW \u20b9{floor:,.0f}</div>')


def card_chips(ad, today=None):
    """Everything that goes in the top-right corner of an ad card."""
    return vintage_chip(ad.get("ad_name", ""), today) + floor_chip(ad)


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
    tier_rows = {k: [] for k in TIER_KEYS}      # every creative of each tier (the "click a tier" lists)
    mix = {}                                    # tier counts per category
    tier_of = {}                                # name -> (tier, category, spend, roas), used for movement
    for name, c in creatives.items():
        roas = c["cv"] / c["spend"] if c["spend"] else 0.0
        tier = classify(c["spend"], roas, c["purchases"], cfgm)
        tiers[tier]["count"] += 1
        tiers[tier]["spend"] += c["spend"]
        tier_of[name] = (tier, c["cat"], c["spend"], roas)
        tier_rows[tier].append({"n": name, "c": c["cat"], "s": int(round(c["spend"])), "r": round(roas, 2), "u": c["purchases"]})
        m = mix.setdefault(c["cat"], {"scaling": 0, "early": 0, "proving": 0, "notworking": 0, "spend": 0.0})
        m[tier] += 1
        m["spend"] += c["spend"]
        card = {"ad_id": c["top"].get("ad_id", ""), "ad_name": name, "adset_name": c["top"].get("adset_name", ""),
                "spend": round(c["spend"], 2), "roas": round(roas, 4), "conv_value": round(c["cv"], 2),
                "purchases": c["purchases"], "category": c["cat"], "tier": tier}
        if c["spend"] >= cfgm["card_min_spend"] and c["purchases"] >= cfgm["min_purchases"]:
            cards.append(card)
        if tier == "notworking":
            decisions.append(card)
    cards.sort(key=lambda x: -x["roas"])
    decisions.sort(key=lambda x: -x["spend"])
    for k in tier_rows:
        tier_rows[k].sort(key=lambda x: -x["s"])
        del tier_rows[k][cfgm["tier_list_max"]:]
    mix_rows = []
    for cat, m in mix.items():
        meta = cat_meta.get(cat, {})
        mix_rows.append({"category": "Unmapped" if cat == "Other" else cat, "group": meta.get("group", ""), "emoji": meta.get("emoji", ""),
                         "order": meta.get("order", 10 ** 6), "spend": round(m["spend"], 2),
                         "counts": {k: m[k] for k in TIER_KEYS}})

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
        "tier_ads": tier_rows,
        "tier_mix": mix_rows,
        "tier_of": tier_of,          # working data for movement; removed before the file is written
    }


def category_month_info(ads, ym, cfgm, today=None):
    """Per-category 'this month' numbers for the daily/weekly 7-day data (stored on each category)."""
    today = today or today_ist()
    cur = [a for a in ads if parse_vintage(a["ad_name"]) == ym]
    total = sum(a["spend"] for a in ads)
    spend = sum(a["spend"] for a in cur)
    q = [a for a in cur if a["spend"] >= cfgm["card_min_spend"] and a["purchases"] >= cfgm["min_purchases"]]
    best = max(q, key=lambda a: a["roas"]) if q else None
    if best:
        best = {k: v for k, v in best.items() if k != "below_floor"}     # a copy, without the floor flag
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


def load_month_status(base_dir):
    """data/month_status.json written by main_month.py ({"ok": bool, "error": str, "at": iso}), or None."""
    path = os.path.join(base_dir, "data", "month_status.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def month_error_html(status):
    """Shown in place of the month tab when the pull failed and there is no data yet, so the failure is
    visible on the dashboard itself instead of only in the Actions log."""
    return ('<div style="padding:26px 22px;border:1px solid #6b5a14;background:#1c1706;border-radius:10px;'
            'font-size:13px;color:#e8d9a0;line-height:1.7;">'
            '<b style="font-size:15px;color:#fcd34d;">The current-month data could not be pulled</b><br>'
            'The tab will fill in as soon as the "Pull current-month data" step of the Daily workflow succeeds.<br>'
            f'<span style="color:#b8a96a;">Last error ({escape(str(status.get("at", ""))[:16])} UTC): '
            f'{escape(redact(str(status.get("error", "unknown"))))}</span></div>')


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



_MOVES = [
    ("newly_scaling", "Newly scaling", "#22c55e", "\u2191"),
    ("newly_early", "New early winners", "#86efac", "\u2191"),
    ("recovered", "Recovered from Not working", "#94a3b8", "\u2191"),
    ("cooled", "Cooled from Scaling", "#fcd34d", "\u2193"),
    ("slipped", "Slipped to Not working", "#ef4444", "\u2193"),
    ("started", "Started spending", "#60a5fa", "+"),
]


def movement_html(v):
    """'Movement since yesterday': who changed tier since the previous daily snapshot."""
    m = v.get("movement")
    if m is None:
        return ""
    head = '<div style="font-size:15px;font-weight:600;">Movement since yesterday</div>'
    if not m.get("since"):
        return (head + '<div style="margin:8px 0 34px;padding:12px 16px;border:1px solid #252525;background:#121212;border-radius:8px;'
                'font-size:12px;color:#9a9a9a;line-height:1.6;">Movement appears after the next daily refresh. It compares each creative\'s tier '
                'with the snapshot saved the day before, and there is none yet.</div>')
    since = _fmt_day(date.fromisoformat(m["since"]))
    counts, lists = m.get("counts", {}), m.get("lists", {})
    tiles = '<div style="display:flex;gap:12px;flex-wrap:wrap;margin:10px 0 12px;">'
    for key, label, color, arrow in _MOVES:
        n = counts.get(key, 0)
        tiles += _tile(label, '<span style="color:' + (color if n else "#e8e8e8") + ';">' + arrow + " " + str(n) + "</span>", "since " + since)
    tiles += "</div>"
    det = ""
    for key, label, color, arrow in _MOVES:
        items = lists.get(key) or []
        if not items:
            continue
        rows, plist = "", ""
        for i in items[:15]:
            was = TIERS.get(i.get("from") or "", {}).get("name", "new")
            plist += ('<div style="padding:9px 14px;border-top:1px solid #1d1d1d;"><div style="color:#e0e0e0;font-size:12px;word-break:break-word;line-height:1.4;">' + escape(i["n"][:90]) + "</div>"
                      '<div style="display:flex;gap:4px 12px;flex-wrap:wrap;margin-top:4px;font-size:11.5px;color:#bbb;"><span>' + escape(i["c"]) + "</span><span>" + escape(was) + " \u2192 " + escape(TIERS[i["to"]]["name"])
                      + '</span><b style="color:#ddd;">' + fmt_inr(i["s"]) + '</b><b style="color:#ddd;">' + ("%.2fx" % i["r"]) + "</b></div></div>")
            rows += ('<tr style="border-top:1px solid #1d1d1d;"><td style="padding:7px 10px;color:#e0e0e0;word-break:break-word;">' + escape(i["n"][:90]) + "</td>"
                     '<td style="padding:7px 10px;color:#bbb;white-space:nowrap;">' + escape(i["c"]) + "</td>"
                     '<td style="padding:7px 10px;color:#9a9a9a;white-space:nowrap;">' + escape(was) + " \u2192 " + escape(TIERS[i["to"]]["name"]) + "</td>"
                     '<td style="padding:7px 10px;color:#ddd;white-space:nowrap;">' + fmt_inr(i["s"]) + "</td>"
                     '<td style="padding:7px 10px;font-weight:700;color:#ddd;white-space:nowrap;">' + ("%.2fx" % i["r"]) + "</td></tr>")
        more = counts.get(key, 0) - min(len(items), 15)
        det += ('<details style="margin:6px 0;"><summary style="cursor:pointer;font-size:12.5px;font-weight:600;padding:6px 0;color:' + color + ';">'
                + arrow + " " + label + " (" + str(counts.get(key, 0)) + ")</summary>"
                '<div class="uk-phone" style="background:#121212;border:1px solid #252525;border-radius:10px;overflow:hidden;">' + plist + "</div>"
                '<div class="uk-desk" style="background:#121212;border:1px solid #252525;border-radius:10px;overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:12px;min-width:560px;"><tbody>'
                + rows + "</tbody></table></div>"
                + ('<div style="font-size:11px;color:#777;margin:4px 0 0 2px;">+ ' + str(more) + " more</div>" if more > 0 else "") + "</details>")
    return (head + '<div style="font-size:11px;color:#9a9a9a;margin:3px 0 0;">Compared with the snapshot from ' + since
            + ". A creative moves when its tier changes as spend and purchases build up.</div>" + tiles + det + '<div style="height:34px;"></div>')


def mix_html(a):
    """'Tier mix by category': which categories are producing winners."""
    rows = a.get("tier_mix") or []
    if not rows:
        return ""
    ks = TIER_KEYS + ["nospend"]
    rows = sorted(rows, key=lambda r: (-(r["counts"]["scaling"] + r["counts"]["early"]), -r["spend"]))
    body, plist, shown = "", "", 0
    for r in rows:
        c = r["counts"]
        if not sum(c.get(k, 0) for k in ks):
            continue
        if shown >= 25:
            break
        shown += 1
        bar = "".join('<div style="flex:' + str(c.get(k, 0)) + ' 1 0;background:' + TIERS[k]["color"] + ';"></div>' for k in ks if c.get(k, 0))
        judged = sum(c.get(k, 0) for k in TIER_KEYS)
        hit = ("%.0f%%" % ((c["scaling"] + c["early"]) / judged * 100)) if judged else "\u2014"
        cells = "".join('<td style="padding:9px 8px;text-align:center;font-weight:700;color:' + (TIERS[k]["color"] if c.get(k, 0) else "#4a4a4a") + ';">' + str(c.get(k, 0)) + "</td>" for k in ks)
        parts = [TIERS[k]["name"] + " " + str(c[k]) for k in ks if c.get(k, 0)]
        plist += ('<div style="padding:10px 14px;border-top:1px solid #1d1d1d;"><div style="display:flex;justify-content:space-between;gap:10px;font-weight:600;font-size:13px;">'
                  '<span>' + r.get("emoji", "") + " " + escape(r["category"]) + '</span><span style="color:#ddd;white-space:nowrap;">' + hit + ' hit rate</span></div>'
                  '<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;gap:1px;margin:8px 0 6px;">' + bar + '</div>'
                  '<div style="font-size:11px;color:#9a9a9a;line-height:1.5;">' + " \u00b7 ".join(parts) + "</div></div>")
        body += ('<tr style="border-top:1px solid #1d1d1d;"><td style="padding:9px 12px;font-weight:600;white-space:nowrap;">' + r.get("emoji", "") + " " + escape(r["category"]) + "</td>"
                 '<td style="padding:9px 10px;min-width:140px;"><div style="display:flex;height:10px;border-radius:5px;overflow:hidden;gap:1px;">' + bar + "</div></td>"
                 + cells + '<td style="padding:9px 12px;text-align:right;font-weight:700;color:#ddd;">' + hit + "</td></tr>")
    if not body:
        return ""
    th = 'style="padding:9px 8px;font-weight:600;text-align:center;"'
    short = {"scaling": "Scaling", "early": "Early", "proving": "Proving", "notworking": "Not working", "nospend": "No spend"}
    heads = "".join("<th " + th + ' title="' + TIERS[k]["name"] + '"><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:' + TIERS[k]["color"] + ';margin-right:5px;"></span>' + short[k] + "</th>" for k in ks)
    return ('<div style="font-size:15px;font-weight:600;">Tier mix by category</div>'
            '<div style="font-size:11px;color:#9a9a9a;margin:3px 0 12px;">Which categories are producing winners: creatives per tier. Hit rate = (Scaling + Early winners) \u00f7 creatives that have spent.</div>'
            '<div class="uk-phone" style="background:#121212;border:1px solid #252525;border-radius:10px;overflow:hidden;margin-bottom:34px;">' + plist + "</div>"
            '<div class="uk-desk" style="background:#121212;border:1px solid #252525;border-radius:10px;overflow-x:auto;margin-bottom:34px;">'
            '<table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:760px;"><thead><tr style="color:#9a9a9a;font-size:10px;text-transform:uppercase;letter-spacing:.05em;">'
            '<th style="padding:9px 12px;text-align:left;font-weight:600;">Category</th><th style="padding:9px 10px;text-align:left;font-weight:600;">Mix</th>'
            + heads + '<th style="padding:9px 12px;text-align:right;font-weight:600;">Hit rate</th></tr></thead><tbody>' + body + "</tbody></table></div>")


TIER_JS = r"""
(function () {
  var COL = {scaling: "#22c55e", early: "#86efac", proving: "#94a3b8", notworking: "#ef4444", nospend: "#3f3f46"};
  var NAME = {scaling: "Scaling", early: "Early winner", proving: "Still proving", notworking: "Not working", nospend: "No spend yet"};
  var state = {};
  function inr(v) { return v >= 100000 ? "\u20b9" + (v / 100000).toFixed(1) + "L" : v >= 1000 ? "\u20b9" + Math.round(v / 1000) + "K" : "\u20b9" + Math.round(v); }
  function el(tag, css, text) { var e = document.createElement(tag); if (css) e.style.cssText = css; if (text != null) e.textContent = text; return e; }
  function mark(key, tier) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-tier-btn="' + key + '"]'), function (b) {
      var on = b.getAttribute("data-tier") === tier;
      b.style.borderColor = on ? COL[tier] : "#252525"; b.style.background = on ? "#161616" : "#121212";
    });
  }
  function roasColor(thr, r) { return r >= thr.good ? "#22c55e" : r >= thr.ok ? "#86efac" : r >= thr.warn ? "#f59e0b" : "#ef4444"; }
  function concept(t) {
    if (!t) return "\u2014";
    var p = [t[0], t[1], t[3]].filter(function (x) { return x && x !== "Unlabelled"; });
    return p.length ? p.join(" \u00b7 ") : "\u2014";
  }
  function fill(key) {
    var st = state[key], all = st.data.tiers[st.tier] || [];
    var rows = all.filter(function (r) { return !st.q || (r.n + " " + r.c).toLowerCase().indexOf(st.q) >= 0; });
    var k = st.sort, d = st.dir;
    rows.sort(function (a, b) {
      var x = a[k], y = b[k];
      if (typeof x === "string") return d * x.localeCompare(y);
      return d * ((x || 0) - (y || 0));
    });
    var tb = st.refs.tbody; tb.textContent = "";
    rows.slice(0, st.shown).forEach(function (r) {
      if (st.phone) {
        var row = el("div", "padding:10px 14px;border-top:1px solid #1d1d1d;"); row.setAttribute("data-row", "1");
        row.appendChild(el("div", "color:#e0e0e0;font-size:12px;word-break:break-word;line-height:1.4;", r.n));
        var meta = el("div", "display:flex;gap:6px 12px;flex-wrap:wrap;align-items:baseline;margin-top:5px;font-size:11.5px;");
        meta.appendChild(el("span", "color:#bbb;", r.c));
        if (st.tier !== "nospend") {
          meta.appendChild(el("b", "color:#ddd;", inr(r.s)));
          meta.appendChild(el("b", "color:" + roasColor(st.data.thr, r.r), r.r.toFixed(2) + "x"));
          meta.appendChild(el("span", "color:#9a9a9a;", r.u + " units"));
        }
        row.appendChild(meta);
        if (st.hasTags && st.tier !== "nospend" && concept(r.t) !== "\u2014") row.appendChild(el("div", "color:#8a8a8a;font-size:11px;margin-top:3px;", concept(r.t)));
        tb.appendChild(row);
        return;
      }
      var tr = el("tr", "border-top:1px solid #1d1d1d;"); tr.setAttribute("data-row", "1");
      tr.appendChild(el("td", "padding:8px 12px;color:#e0e0e0;max-width:420px;word-break:break-word;", r.n));
      tr.appendChild(el("td", "padding:8px 12px;color:#bbb;white-space:nowrap;", r.c));
      if (st.tier !== "nospend") {
        tr.appendChild(el("td", "padding:8px 12px;color:#ddd;white-space:nowrap;font-variant-numeric:tabular-nums;", inr(r.s)));
        tr.appendChild(el("td", "padding:8px 12px;font-weight:700;white-space:nowrap;color:" + roasColor(st.data.thr, r.r), r.r.toFixed(2) + "x"));
        tr.appendChild(el("td", "padding:8px 12px;color:#ddd;", String(r.u)));
        if (st.hasTags) tr.appendChild(el("td", "padding:8px 12px;color:#9a9a9a;", concept(r.t)));
      }
      tb.appendChild(tr);
    });
    var total = st.data.counts[st.tier] || all.length;
    st.refs.note.textContent = "Showing " + Math.min(st.shown, rows.length) + " of " + rows.length + (st.q ? " matching" : "") +
      (all.length < total && !st.q ? " (the largest " + all.length + " of " + total + " are listed)" : "");
    st.refs.more.textContent = "";
    if (rows.length > st.shown) {
      var b = el("button", "background:#1a1a1a;border:1px solid #2a2a2a;border-radius:6px;color:#ddd;padding:9px 14px;font-size:12px;cursor:pointer;min-height:36px;", "Show 50 more");
      b.type = "button"; b.addEventListener("click", function () { st.shown += 50; fill(key); });
      st.refs.more.appendChild(b);
    }
  }
  function build(key) {
    var st = state[key], panel = document.getElementById("tp-" + key); panel.textContent = "";
    var box = el("div", "background:#121212;border:1px solid " + COL[st.tier] + ";border-radius:10px;overflow:hidden;");
    var head = el("div", "display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 14px;border-bottom:1px solid #1d1d1d;");
    head.appendChild(el("span", "width:10px;height:10px;border-radius:2px;background:" + COL[st.tier] + ";display:inline-block;"));
    head.appendChild(el("div", "font-size:13px;font-weight:700;", NAME[st.tier] + " \u00b7 " + (st.data.counts[st.tier] || 0).toLocaleString("en-IN") + " ads"));
    var q = el("input", "flex:1 1 180px;min-width:140px;background:#0c0c0c;border:1px solid #2a2a2a;border-radius:6px;color:#e8e8e8;padding:9px 10px;font-size:12px;min-height:36px;");
    q.type = "search"; q.placeholder = "Search name or category"; q.setAttribute("aria-label", "Search ads in this tier");
    q.addEventListener("input", function () { st.q = q.value.toLowerCase(); st.shown = 50; fill(key); });
    var close = el("button", "background:#1a1a1a;border:1px solid #2a2a2a;border-radius:6px;color:#bbb;padding:9px 12px;font-size:12px;cursor:pointer;min-height:36px;", "Close");
    close.type = "button";
    close.addEventListener("click", function () { panel.style.display = "none"; st.tier = null; mark(key, null); });
    head.appendChild(q); head.appendChild(close); box.appendChild(head);
    st.phone = window.matchMedia("(max-width: 700px)").matches;
    if (st.phone) {
      if (st.tier !== "nospend") {
        var sel = el("select", "display:block;width:calc(100% - 28px);margin:10px 14px 4px;background:#0c0c0c;border:1px solid #2a2a2a;border-radius:6px;color:#e8e8e8;padding:9px 10px;font-size:12px;min-height:36px;");
        sel.setAttribute("aria-label", "Sort the list");
        [["s", -1, "Sort: biggest spend"], ["r", -1, "Sort: best ROAS"], ["u", -1, "Sort: most units"], ["n", 1, "Sort: name A-Z"], ["c", 1, "Sort: category A-Z"]].forEach(function (o) {
          var op = el("option", null, o[2]); op.value = o[0] + ":" + o[1]; if (st.sort === o[0] && st.dir === o[1]) op.selected = true; sel.appendChild(op);
        });
        sel.addEventListener("change", function () { var p = sel.value.split(":"); st.sort = p[0]; st.dir = +p[1]; fill(key); });
        box.appendChild(sel);
      }
      st.refs = {tbody: el("div"), more: el("div", "padding:6px 14px 12px;"), note: el("div", "padding:8px 14px;font-size:11px;color:#777;")};
      box.appendChild(st.refs.tbody); box.appendChild(st.refs.note); box.appendChild(st.refs.more);
      panel.appendChild(box);
    } else {
      var cols = st.tier === "nospend" ? [["Creative", "n"], ["Category", "c"]] : [["Creative", "n"], ["Category", "c"], ["Spend", "s"], ["ROAS", "r"], ["Units", "u"]];
      if (st.tier !== "nospend" && st.hasTags) cols.push(["Concept", "t"]);
      var wrap = el("div", "overflow-x:auto;"), tbl = el("table", "width:100%;border-collapse:collapse;font-size:12px;min-width:" + (st.tier === "nospend" ? 360 : 640) + "px;");
      var thead = el("thead"), tr = el("tr", "color:#9a9a9a;font-size:9.5px;text-transform:uppercase;letter-spacing:.06em;");
      cols.forEach(function (c) {
        var th = el("th", "padding:8px 12px;text-align:left;font-weight:600;" + (c[1] !== "t" ? "cursor:pointer;" : ""), c[0] + (st.sort === c[1] ? (st.dir > 0 ? " \u25b2" : " \u25bc") : ""));
        if (c[1] !== "t") th.addEventListener("click", function () {
          if (st.sort === c[1]) st.dir = -st.dir; else { st.sort = c[1]; st.dir = (c[1] === "n" || c[1] === "c") ? 1 : -1; }
          build(key); fill(key);
        });
        tr.appendChild(th);
      });
      thead.appendChild(tr); tbl.appendChild(thead);
      st.refs = {tbody: el("tbody"), more: el("div", "padding:6px 14px 12px;"), note: el("div", "padding:8px 14px;font-size:11px;color:#777;")};
      tbl.appendChild(st.refs.tbody); wrap.appendChild(tbl); box.appendChild(wrap); box.appendChild(st.refs.note); box.appendChild(st.refs.more);
      panel.appendChild(box);
    }
    q.value = st.q || "";
  }
  window.tierShow = function (key, tier) {
    var panel = document.getElementById("tp-" + key), src = document.getElementById("tj-" + key);
    if (!panel || !src) return;
    var st = state[key] = state[key] || {};
    if (st.tier === tier && panel.style.display === "block") { panel.style.display = "none"; st.tier = null; mark(key, null); return; }
    st.data = st.data || JSON.parse(src.textContent);
    st.hasTags = Object.keys(st.data.tiers).some(function (t) { return (st.data.tiers[t] || []).some(function (r) { return r.t; }); });
    st.tier = tier; st.shown = 50; st.q = ""; st.sort = "s"; st.dir = -1;
    if (tier === "nospend") { st.sort = "n"; st.dir = 1; }
    mark(key, tier); build(key); fill(key); panel.style.display = "block";
    if (panel.scrollIntoView) panel.scrollIntoView({behavior: "smooth", block: "nearest"});
  };
})();
"""


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
    elif v["key"] == "previous_only":
        scope = (f'<b style="color:#ddd;">{name} only:</b> spend counted from {_fmt_day(since)} to {_fmt_day(until)}, '
                 f'nothing from later months. ')
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
    clickable = bool(a.get("tier_ads"))
    legend = ""
    for k, n, sp in segs:
        inner = ('<div style="display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;"><span style="width:10px;height:10px;border-radius:2px;background:'
                 + TIERS[k]["color"] + ';display:inline-block;"></span>' + TIERS[k]["name"] + "</div>"
                 '<div style="font-size:20px;font-weight:700;margin-top:6px;">' + format(n, ",") + ' <span style="font-size:12px;font-weight:500;color:#9a9a9a;">\u00b7 ' + fmt_inr(sp) + "</span></div>"
                 '<div style="font-size:11px;color:#9a9a9a;margin-top:4px;line-height:1.5;">' + rules[k] + "</div>")
        if clickable and n > 0 and (k != "nospend" or a["tier_ads"].get("nospend")):
            legend += ('<button type="button" data-tier-btn="' + v["key"] + '" data-tier="' + k + '" onclick="tierShow(\'' + v["key"] + "','" + k + '\')" '
                       'style="flex:1 1 180px;text-align:left;font:inherit;color:inherit;cursor:pointer;background:#121212;border:1px solid #252525;border-radius:8px;padding:12px 14px;">'
                       + inner + '<div style="font-size:11px;color:#8ab4f8;margin-top:8px;">View all ' + format(n, ",") + " \u203a</div></button>")
        else:
            legend += '<div style="flex:1 1 180px;background:#121212;border:1px solid #252525;border-radius:8px;padding:12px 14px;">' + inner + "</div>"
    tier_json = ""
    if clickable:
        payload = {"tiers": a["tier_ads"], "counts": {k: n for k, n, _sp in segs}, "thr": (cfg or {}).get("roas_thresholds") or {"good": 2.5, "ok": 2.0, "warn": 1.5}}
        tier_json = ('<script type="application/json" id="tj-' + v["key"] + '">'
                     + json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c") + "</script>")
    funnel = ('<div style="font-size:15px;font-weight:600;">Are they working?</div>'
              '<div style="font-size:11px;color:#9a9a9a;margin:3px 0 12px;">Every ' + name + " upload, sorted by how it is doing so far"
              + ("; click a tier to see all of its ads" if clickable else "") + "</div>"
              '<div style="display:flex;height:30px;border-radius:6px;overflow:hidden;gap:2px;margin-bottom:14px;">' + bar + "</div>"
              '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px;">' + legend + "</div>"
              '<div id="tp-' + v["key"] + '" style="display:none;margin-bottom:6px;"></div>' + tier_json + '<div style="height:20px;"></div>')
    movement = movement_html(v)
    mix = mix_html(a)

    concepts = content_html(v.get("content"), v["key"], v["name"], lambda r: _roas_color(r, cfg), v.get("content_error"))

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
    return note + tiles + funnel + movement + mix + concepts + score + best + decide + warn


def month_tab_html(month_data, card_fn, cfg, status=None):
    """
    The whole tab. A three-way switch:
        <This month> (to date)  |  <Last month> (to date)  |  <Last month> (only)
    "to date" counts spend from the 1st of the month up to yesterday (last month's view runs from the 1st of
    last month, so it includes this month's spend). "only" counts spend made inside last month alone.
    Falls back to last month's view on the 1st of a month, when the new month has no data yet.
    """
    views = month_data["views"]
    cur, prev = views["current"], views["previous"]
    only = views.get("previous_only")             # absent in month files written before this view existed
    show = "cur" if cur.get("has_data") else "prev"
    order = [("cur", cur, "to date"), ("prev", prev, "to date")]
    if only:
        order.append(("only", only, "only"))
    btns = "".join(
        '<button class="filter-btn' + (" active" if show == w else "") + '" id="mvb-' + w + '" onclick="mvShow(\'' + w + '\')">'
        + escape(v["name"]) + " (" + tag + ")</button>" for w, v, tag in order)
    switch = '<div class="filter-bar" style="margin-bottom:18px;">' + btns + "</div>"
    stamp = ('<div style="font-size:11px;color:#9a9a9a;margin:-6px 0 16px;">Updated '
             + escape(str(month_data.get("generated_at", ""))[:10]) + "</div>")
    if status and not status.get("ok"):
        stamp = ('<div style="margin:0 0 14px;padding:9px 14px;border:1px solid #6b5a14;background:#1c1706;border-radius:8px;font-size:12px;color:#e8d9a0;">'
                 "The latest refresh failed, so this is data from " + escape(str(month_data.get("generated_at", ""))[:10]) + ". "
                 "Error: " + escape(redact(str(status.get("error", "unknown")))) + "</div>")
    ids = [w for w, _v, _t in order]
    script = ('<script>function mvShow(w){var ids=' + json.dumps(ids) + ';ids.forEach(function(i){'
              'document.getElementById("mv-"+i).style.display=i===w?"block":"none";'
              'document.getElementById("mvb-"+i).classList.toggle("active",i===w);});}' + CT_JS + TIER_JS + "</script>")
    panes = "".join('<div id="mv-' + w + '" style="display:' + ("block" if show == w else "none") + ';">'
                    + view_html(v, card_fn, cfg) + "</div>" for w, v, _t in order)
    return switch + stamp + panes + script


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
