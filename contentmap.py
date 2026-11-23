"""
contentmap.py: concept tags (Type, Theme, Messaging, Hook) from the team's Google Sheet, joined to Meta ads
by ad name, then summarised for the winners (ads with at least `winner_spend` in the window).

The sheet ("Videos - Ad copy", tab "2026") has one row per video:
    column D  the full ad name        e.g. November2026-...-Video-UGC-Dark-Post-Direct-06.11.2026
    column M  Type of Content         UGC / PLV / AI PLV / Lowfi ...
    column N  Theme   column O  Messaging   column P  Hook

Matching is on the ad name CUT AT THE DATE: everything after the DD.MM.YYYY date (".MP4", ".mov", " - Copy" ...)
is ignored, on both the sheet side and the Meta side.

Dependency-free apart from taxonomy.py (money formatting).
"""
import csv
import io
import json
import os
import re
from collections import Counter, defaultdict
from html import escape

from taxonomy import fmt_inr

FIELDS = [("type", "Type"), ("theme", "Theme"), ("messaging", "Messaging"), ("hook", "Hook")]
UNLABELLED = "Unlabelled"          # in the sheet, but the cell is blank / NA / "-"
NOT_IN_SHEET = "Not in sheet"      # the ad has no row in the sheet

# header names tried (lower-case) for each column; falls back to the column letters D, M, N, O, P
COLUMN_HEADERS = {
    "name": ["ad name", "ad nomenclature", "link", "name"],
    "type": ["type of content", "type", "video type"],
    "theme": ["theme"],
    "messaging": ["messaging", "message"],
    "hook": ["hook"],
}
COLUMN_LETTERS = {"name": 3, "type": 12, "theme": 13, "messaging": 14, "hook": 15}

BLANKS = {"", "na", "n/a", "n.a", "-", "--", "\u2014", "tbd", "none", "nil", "?"}
DEFAULT_ALIASES = {      # lower-case spelling in the sheet -> label shown on the dashboard
    "type": {"lowfi": "LoFi", "lo-fi": "LoFi", "lo fi": "LoFi", "low fi": "LoFi", "low-fi": "LoFi"},
    "messaging": {"problem & solutiom": "Problem & Solution", "problem and solution": "Problem & Solution",
                  "problem & solution": "Problem & Solution"},
    "theme": {},
    "hook": {},
}

_DATE_STRICT = re.compile(r"(?<!\d)\d{2}\.\d{2}\.\d{4}(?!\d)")
_DATE_LOOSE = re.compile(r"(?<!\d)\d{1,2}[.\-]\d{1,2}[.\-](?:\d{4}|\d{2})(?!\d)")
_EXT = re.compile(r"\.(mp4|mov|moov|mkv|avi|m4v|webm)$", re.I)
_AD_NAME = re.compile(r"^[A-Za-z]{3,9}\d{4}[-_]")        # "November2026-...", "Nov2026-..."


def ad_key(name):
    """Join key: the ad name cut right after its date, lower-cased, separators unified."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not n:
        return ""
    m = _DATE_STRICT.search(n) or _DATE_LOOSE.search(n)
    n = n[: m.end()] if m else _EXT.sub("", n)
    n = re.sub(r"[\s_]+", "-", n.lower())
    return re.sub(r"-{2,}", "-", n).strip("-")


def looks_like_ad_name(name):
    """Real ad names start with Month+Year. This skips Drive links, headings and the stray junk row."""
    return bool(_AD_NAME.match((name or "").strip()))


# ── reading the sheet ────────────────────────────────────────────────────────

def _resolve_columns(header, overrides=None):
    h = [c.strip().lower() for c in header]
    idx = {}
    for field, names in COLUMN_HEADERS.items():
        names = list((overrides or {}).get(field, [])) + names
        for n in names:
            n = str(n).strip().lower()
            if n in h:
                idx[field] = h.index(n)
                break
        else:
            if COLUMN_LETTERS[field] < len(h):
                idx[field] = COLUMN_LETTERS[field]
    missing = [f for f in COLUMN_HEADERS if f not in idx]
    if missing:
        raise RuntimeError("Could not find these columns in the sheet: " + ", ".join(missing) +
                           ". Header row was: " + " | ".join(c for c in header if c.strip())[:200])
    return idx


def parse_sheet_csv(text, columns=None, aliases=None, keep=None):
    """
    CSV text of the sheet tab -> ({key: [type, theme, messaging, hook]}, stats).
    keep(name) -> bool can drop old rows (the dashboard only needs the last few months).
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise RuntimeError("The sheet came back empty")
    idx = _resolve_columns(rows[0], columns)
    fields = [f for f, _ in FIELDS]
    width = max(idx.values()) + 1
    raw = {}
    seen = 0
    for r in rows[1:]:
        if len(r) < width:
            r = r + [""] * (width - len(r))
        name = r[idx["name"]].strip()
        if not looks_like_ad_name(name):
            continue
        seen += 1
        if keep is not None and not keep(name):
            continue
        key = ad_key(name)
        vals = [re.sub(r"\s+", " ", r[idx[f]]).strip() for f in fields]
        filled = sum(1 for v in vals if v.lower() not in BLANKS)
        prev = raw.get(key)
        if prev is None or filled >= prev[0]:                 # duplicate rows: keep the better-filled one
            raw[key] = (filled, vals)

    al = {f: dict(DEFAULT_ALIASES.get(f, {})) for f in fields}
    for f, m in (aliases or {}).items():
        if f in al:
            al[f].update({str(k).strip().lower(): v for k, v in (m or {}).items()})
    casing = {f: defaultdict(Counter) for f in fields}        # lower-case -> Counter of spellings (most common wins)
    for _filled, vals in raw.values():
        for f, v in zip(fields, vals):
            casing[f][v.lower()][v] += 1

    def canon(f, v):
        low = v.lower()
        if low in BLANKS:
            return UNLABELLED
        if low in al[f]:
            return al[f][low]
        return casing[f][low].most_common(1)[0][0]

    tags, vocab = {}, {f: Counter() for f in fields}
    for key, (_filled, vals) in raw.items():
        t = [canon(f, v) for f, v in zip(fields, vals)]
        tags[key] = t
        for f, label in zip(fields, t):
            vocab[f][label] += 1
    stats = {"rows_seen": len(rows) - 1, "ad_rows": seen, "kept": len(tags),
             "tagged": sum(1 for t in tags.values() if any(x != UNLABELLED for x in t)),
             "vocab": {f: dict(vocab[f].most_common(40)) for f in fields}}
    return tags, stats


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ── the analysis ─────────────────────────────────────────────────────────────

def aggregate_creatives(by_cat, only_vintage=None, vintage_fn=None):
    """{category: [ads]} -> one entry per creative name (spend/value summed over categories)."""
    out = {}
    for ads in by_cat.values():
        for a in ads:
            if only_vintage is not None and vintage_fn(a["ad_name"]) != only_vintage:
                continue
            c = out.setdefault(a["ad_name"], {"ad_name": a["ad_name"], "spend": 0.0, "cv": 0.0, "purchases": 0})
            c["spend"] += a["spend"]
            c["cv"] += a["conv_value"]
            c["purchases"] += a["purchases"]
    return list(out.values())


def breakdown(creatives, tags, winner_spend, top_unmatched=8):
    """Winners (spend >= winner_spend) by Type / Theme / Messaging / Hook, plus coverage of the sheet match."""
    dims = {f: defaultdict(lambda: {"ads": 0, "winners": 0, "spend": 0.0, "cv": 0.0, "wspend": 0.0}) for f, _ in FIELDS}
    tot = {"ads": 0, "winners": 0, "spend": 0.0, "cv": 0.0, "wspend": 0.0, "m_ads": 0, "m_winners": 0, "m_wspend": 0.0}
    unmatched = []
    for c in creatives:
        win = c["spend"] >= winner_spend
        t = tags.get(ad_key(c["ad_name"]))
        tot["ads"] += 1
        tot["spend"] += c["spend"]
        tot["cv"] += c["cv"]
        if win:
            tot["winners"] += 1
            tot["wspend"] += c["spend"]
        if t is None:
            labels = [NOT_IN_SHEET] * len(FIELDS)
            if win:
                unmatched.append({"ad_name": c["ad_name"], "spend": round(c["spend"], 2),
                                  "roas": round(c["cv"] / c["spend"], 2) if c["spend"] else 0})
        else:
            labels = t
            tot["m_ads"] += 1
            if win:
                tot["m_winners"] += 1
                tot["m_wspend"] += c["spend"]
        for (f, _n), label in zip(FIELDS, labels):
            g = dims[f][label]
            g["ads"] += 1
            g["spend"] += c["spend"]
            g["cv"] += c["cv"]
            if win:
                g["winners"] += 1
                g["wspend"] += c["spend"]

    def rows_for(f):
        out = []
        for label, g in dims[f].items():
            out.append({"label": label, "ads": g["ads"], "winners": g["winners"],
                        "win_rate": round(g["winners"] / g["ads"], 4) if g["ads"] else 0,
                        "spend": round(g["spend"], 2), "roas": round(g["cv"] / g["spend"], 4) if g["spend"] else 0,
                        "share_winners": round(g["winners"] / tot["winners"], 4) if tot["winners"] else 0})
        out.sort(key=lambda r: (r["label"] == NOT_IN_SHEET, r["label"] == UNLABELLED, -r["winners"], -r["spend"]))
        return out

    unmatched.sort(key=lambda u: -u["spend"])
    return {
        "winner_spend": winner_spend,
        "totals": {"ads": tot["ads"], "winners": tot["winners"], "spend": round(tot["spend"], 2),
                   "roas": round(tot["cv"] / tot["spend"], 4) if tot["spend"] else 0,
                   "win_rate": round(tot["winners"] / tot["ads"], 4) if tot["ads"] else 0,
                   "matched_ads": tot["m_ads"], "matched_winners": tot["m_winners"],
                   "winner_match_pct": round(tot["m_winners"] / tot["winners"] * 100, 1) if tot["winners"] else None,
                   "winner_spend_match_pct": round(tot["m_wspend"] / tot["wspend"] * 100, 1) if tot["wspend"] else None},
        "dims": {f: rows_for(f) for f, _n in FIELDS},
        "unmatched": unmatched[:top_unmatched], "unmatched_total": len(unmatched),
    }


# ── rendering ────────────────────────────────────────────────────────────────

_TH = 'style="padding:8px 10px;font-weight:600;text-align:left;"'


def _table(title, rows, overall_rate, roas_color, limit=10):
    body = ""
    for r in rows[:limit]:
        small = r["ads"] < 3                                    # too few ads to read anything into
        rate_color = "#9a9a9a"
        if not small and r["label"] not in (NOT_IN_SHEET, UNLABELLED) and overall_rate:
            if r["win_rate"] >= overall_rate * 1.25:
                rate_color = "#86efac"
            elif r["win_rate"] <= overall_rate * 0.75:
                rate_color = "#fca5a5"
        muted = r["label"] in (NOT_IN_SHEET, UNLABELLED) or small
        name_color = "#8a8a8a" if muted else "#e8e8e8"
        body += ('<tr style="border-top:1px solid #1d1d1d;">'
                 '<td style="padding:8px 10px;color:' + name_color + ';font-weight:600;">' + escape(r["label"]) + '</td>'
                 '<td style="padding:8px 10px;color:#ccc;">' + str(r["ads"]) + '</td>'
                 '<td style="padding:8px 10px;color:#ddd;font-weight:700;">' + str(r["winners"]) + '</td>'
                 '<td style="padding:8px 10px;font-weight:700;color:' + rate_color + ';">' + ("%.0f%%" % (r["win_rate"] * 100)) + '</td>'
                 '<td style="padding:8px 10px;color:#ddd;">' + fmt_inr(r["spend"]) + '</td>'
                 '<td style="padding:8px 10px;font-weight:700;color:' + roas_color(r["roas"]) + ';">' + ("%.2fx" % r["roas"]) + '</td></tr>')
    return ('<div style="background:#121212;border:1px solid #252525;border-radius:10px;overflow:hidden;">'
            '<div style="padding:10px 12px;font-size:12px;font-weight:700;color:#e8e8e8;border-bottom:1px solid #1d1d1d;">' + escape(title) + '</div>'
            '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:12px;min-width:330px;">'
            '<thead><tr style="color:#9a9a9a;font-size:9.5px;text-transform:uppercase;letter-spacing:.06em;">'
            '<th ' + _TH + '>Concept</th><th ' + _TH + '>Ads</th><th ' + _TH + '>Winners</th><th ' + _TH + '>Win rate</th>'
            '<th ' + _TH + '>Spend</th><th ' + _TH + '>ROAS</th></tr></thead><tbody>' + body + '</tbody></table></div></div>')


def _scope_html(b, roas_color, label):
    t = b["totals"]
    if not t["ads"]:
        return '<div style="padding:22px;color:#9a9a9a;font-size:12px;">No ads in this scope yet.</div>'
    wm = t["winner_match_pct"]
    if t["winners"]:
        cov_ok = wm is not None and wm >= 80
        cov = ('<b style="color:#ddd;">' + str(t["matched_winners"]) + ' of ' + str(t["winners"]) + ' winners</b> are in the sheet ('
               + ("%.0f%%" % t["winner_spend_match_pct"]) + ' of winner spend).')
    else:
        cov_ok = True
        cov = 'No ad has reached ' + fmt_inr(b["winner_spend"]) + ' yet in this scope.'
    banner = ('<div style="margin:0 0 12px;padding:9px 14px;border:1px solid ' + ('#252525' if cov_ok else '#6b5a14') + ';background:'
              + ('#121212' if cov_ok else '#1c1706') + ';border-radius:8px;font-size:12px;color:' + ('#9a9a9a' if cov_ok else '#e8d9a0')
              + ';line-height:1.6;">' + escape(label) + ': <b style="color:#ddd;">' + str(t["ads"]) + ' ads</b>, '
              '<b style="color:#ddd;">' + str(t["winners"]) + ' winners</b> (' + fmt_inr(b["winner_spend"]) + '+ spend), overall win rate '
              + ("%.0f%%" % (t["win_rate"] * 100)) + '. ' + cov + '</div>')
    grid = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:14px;">'
    for f, title in FIELDS:
        grid += _table(title, b["dims"][f], t["win_rate"], roas_color)
    grid += '</div>'
    miss = ""
    if b["unmatched"]:
        items = "".join('<li style="margin:3px 0;">' + escape(u["ad_name"][:96]) + ' <span style="color:#777;">\u00b7 ' + fmt_inr(u["spend"])
                        + ' \u00b7 ' + ("%.2fx" % u["roas"]) + '</span></li>' for u in b["unmatched"])
        more = b["unmatched_total"] - len(b["unmatched"])
        miss = ('<details style="margin-top:12px;"><summary style="cursor:pointer;font-size:12px;color:#e8d9a0;padding:6px 0;">'
                + str(b["unmatched_total"]) + ' winners are not in the sheet yet (add them to get their concepts counted)</summary>'
                '<ul style="margin:6px 0 0;padding-left:18px;font-size:11.5px;color:#cfcfcf;">' + items + '</ul>'
                + ('<div style="font-size:11px;color:#777;margin-top:4px;">+ ' + str(more) + ' more</div>' if more > 0 else '') + '</details>')
    return banner + grid + miss


def content_html(content, key, month_name, roas_color, error=None):
    """The 'What concepts are working' block of one month view. content = {"all": breakdown, "new": breakdown}."""
    if not content:
        if error:
            return ('<div style="margin:0 0 28px;padding:12px 16px;border:1px solid #6b5a14;background:#1c1706;border-radius:8px;'
                    'font-size:12px;color:#e8d9a0;line-height:1.6;"><b>Concept tags are unavailable.</b> The Google Sheet could not be read: '
                    + escape(str(error)) + '</div>')
        return ""
    allb, newb = content["all"], content["new"]
    on = ' active'
    btn = ('<div class="filter-bar" style="margin:6px 0 14px;">'
           '<button class="filter-btn' + on + '" id="ctb-' + key + '-all" onclick="ctShow(\'' + key + '\',\'all\')">All ads with spend (' + str(allb["totals"]["ads"]) + ')</button>'
           '<button class="filter-btn" id="ctb-' + key + '-new" onclick="ctShow(\'' + key + '\',\'new\')">Uploaded in ' + escape(month_name) + ' (' + str(newb["totals"]["ads"]) + ')</button></div>')
    return ('<div style="font-size:15px;font-weight:600;">What concepts are working?</div>'
            '<div style="font-size:11px;color:#9a9a9a;margin:3px 0 4px;">Type, Theme, Messaging and Hook come from the team\'s Google Sheet, matched to Meta by ad name. '
            'A winner is an ad with ' + fmt_inr(allb["winner_spend"]) + '+ spend in this window. Win rate = winners \u00f7 ads with that concept; green / red = clearly above / below average (3+ ads only).</div>'
            + btn
            + '<div id="ct-' + key + '-all">' + _scope_html(allb, roas_color, "All ads with spend") + '</div>'
            + '<div id="ct-' + key + '-new" style="display:none;">' + _scope_html(newb, roas_color, "Uploaded in " + month_name) + '</div>'
            + '<div style="height:34px;"></div>')


CT_JS = ('function ctShow(k,s){var o=s==="all"?"new":"all";document.getElementById("ct-"+k+"-"+s).style.display="block";'
         'document.getElementById("ct-"+k+"-"+o).style.display="none";document.getElementById("ctb-"+k+"-"+s).classList.add("active");'
         'document.getElementById("ctb-"+k+"-"+o).classList.remove("active");}')
