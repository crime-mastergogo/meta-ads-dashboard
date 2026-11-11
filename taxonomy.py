"""
Shared helpers for the grouped category taxonomy (config.yaml -> `group:` on each category).

Dependency-free so every generator and main script can import it.

Backward compatible by design: data written before groups existed has no "group"/"order"
keys, and every helper here then behaves like the old flat layout (insertion order,
no group headers, per-category Slack lines).
"""
from collections import defaultdict
from html import escape

LEGACY_ORDER = 10 ** 6


def category_orders(categories_cfg, group_order=None):
    """
    {category: display order}. Groups follow `group_order` from config (groups not listed go
    last, in config order); inside a group, categories keep their config order. This lets
    matching priority (config order) differ from display order (e.g. Winter is matched first
    but shown after Innerwear).
    """
    rank = {g: i for i, g in enumerate(group_order or [])}
    for c in categories_cfg.values():
        g = c.get("group")
        if g and g not in rank:
            rank[g] = len(rank)
    return {name: rank.get(c.get("group"), len(rank)) * 1000 + i
            for i, (name, c) in enumerate(categories_cfg.items())}


def drop_ignored(by_cat, categories_cfg):
    """Remove categories marked `ignore: true`. Returns (by_cat_without_ignored, ignored_ads)."""
    ignored, kept = [], {}
    for cat, ads in by_cat.items():
        if categories_cfg.get(cat, {}).get("ignore"):
            ignored.extend(ads)
        else:
            kept[cat] = ads
    return kept, ignored


def fmt_inr(v):
    if v >= 100000:
        return f"₹{v/100000:.1f}L"
    if v >= 1000:
        return f"₹{v/1000:.0f}K"
    return f"₹{int(v)}"


# ── ordering / grouping ──────────────────────────────────────────────────────

def ordered_categories(categories):
    """Categories in config order (data["order"]). Stable, so legacy data keeps its order."""
    return dict(sorted(categories.items(), key=lambda kv: kv[1].get("order", LEGACY_ORDER)))


def group_rollup(categories):
    """
    {group: {spend, ads, roas, units, cats, emoji, summary, children}} in display order.
    Ungrouped categories are skipped.

    Spend is counted ONCE: every ad lives in exactly one category, and a group is only the sum
    of its categories (it never holds ads of its own), so parent and children never double-count.
    """
    out = {}
    for cat, c in ordered_categories(categories).items():
        g = c.get("group")
        if not g:
            continue
        r = out.setdefault(g, {"spend": 0.0, "cv": 0.0, "ads": 0, "units": 0, "has_units": False,
                               "cats": 0, "emoji": c.get("emoji", ""), "summary": False, "children": []})
        if c.get("group_emoji"):
            r["emoji"] = c["group_emoji"]
        if c.get("group_summary"):
            r["summary"] = True
        spend = c.get("total_spend", 0) or 0
        roas = c.get("blended_roas", 0) or 0
        units = c.get("total_purchases")          # absent in data written before units were saved
        r["spend"] += spend
        r["cv"] += spend * roas
        r["ads"] += c.get("total_ads", 0) or 0
        r["cats"] += 1
        if units is not None:
            r["units"] += units
            r["has_units"] = True
        r["children"].append({"name": cat, "spend": spend, "roas": roas, "units": units})
    for r in out.values():
        r["roas"] = r["cv"] / r["spend"] if r["spend"] else 0
    return out


class GroupHeaders:
    """
    Emits one header block before the first category of each group.

        gh = GroupHeaders(categories)
        ...inside the loop, after the "no ads -> continue" check:
        html += gh.before(cat_data)

    The header carries class="sec" + data-cat so the existing category filter buttons
    hide it whenever a single category is selected (it only shows under "All").
    Deliberately block-level (no flex): the filter JS toggles display to "block".
    """

    def __init__(self, categories):
        self._roll = group_rollup(categories)
        self._seen = set()

    def before(self, cat_data):
        g = cat_data.get("group")
        if not g or g in self._seen or g not in self._roll:
            return ""
        self._seen.add(g)
        r = self._roll[g]
        n = r["cats"]
        units = f' · {r["units"]:,} units sold' if r["has_units"] else ""
        summary = ""
        if r["summary"]:      # overview groups (e.g. Winter): one line listing every child, biggest first
            parts = " &nbsp;&middot;&nbsp; ".join(
                f'{escape(ch["name"])} <b style="color:#bbb;">{fmt_inr(ch["spend"])}</b> '
                f'<span style="color:#666;">({ch["roas"]:.1f}x'
                + (f' · {ch["units"]:,} units' if ch["units"] is not None else "") + ')</span>'
                for ch in sorted(r["children"], key=lambda x: -x["spend"])
            )
            summary = f'<div style="font-size:11px;color:#8a8a8a;margin-top:8px;line-height:1.9;">By category &middot; {parts}</div>'
        return (
            '<div class="sec grp-hdr" data-cat="__group__" '
            'style="margin:34px 0 16px;padding:12px 16px;border-left:3px solid #3b82f6;'
            'background:#121212;border-radius:0 8px 8px 0;">'
            f'<div style="font-size:15px;font-weight:700;color:#e8e8e8;letter-spacing:.2px;">{r["emoji"]} {g}</div>'
            '<div style="font-size:11px;color:#777;margin-top:4px;font-variant-numeric:tabular-nums;">'
            f'{n} categor{"y" if n == 1 else "ies"} · {r["ads"]} ads · {fmt_inr(r["spend"])} · {r["roas"]:.2f}x blended ROAS{units}'
            f'</div>{summary}</div>'
        )


# ── static vs video ──────────────────────────────────────────────────────────

VIDEO_NAME_TOKENS = ("-video-", "_video_", "ugc-pa", "ugc-diy", "ugc-dark")
STATIC_NAME_TOKENS = ("dpa", "carousel", "static", "image", "catalog", "collection")


def ad_format(ad):
    """
    "video" or "static". EVERY ad gets exactly one.

    The old pair of tests (is_static / is_video) could reject the same ad from BOTH dashboards:
    a static / DPA / carousel creative sitting in an ad set whose name contains "Video" failed
    is_static (video ad set) and failed is_video (static-type name), so it simply vanished.

      1. name carries a static-type marker (static, image, carousel, dpa, catalog, collection) -> static
      2. ad set name says "video", or the name carries a video marker                          -> video
      3. everything else                                                                        -> static
    """
    adset = (ad.get("adset_name") or "").lower()
    name = (ad.get("ad_name") or "").lower()
    if any(t in name for t in STATIC_NAME_TOKENS):
        return "static"
    if "video" in adset or any(t in name for t in VIDEO_NAME_TOKENS):
        return "video"
    return "static"


# ── spend-floor note ─────────────────────────────────────────────────────────

def floor_note(data):
    """Says why the page shows fewer ads than the totals count, so a filtered page is never
    mistaken for missing data. Empty when nothing is hidden."""
    floor = data.get("card_min_spend")
    hidden = data.get("hidden_by_card_floor") or {}
    if not floor or not hidden.get("ads"):
        return ""
    return (
        '<div style="margin:0 0 18px;padding:10px 14px;border:1px solid #252525;background:#121212;'
        'border-radius:8px;font-size:12px;color:#9a9a9a;line-height:1.6;">'
        f'Showing ads with <b style="color:#ddd;">\u20b9{floor:,.0f}+ spend</b>. '
        f'{hidden["ads"]} smaller ads ({fmt_inr(hidden["spend"])} combined) are hidden here but are still '
        'counted in every category total, group rollup and Slack line.</div>'
    )


# ── overview tab (e.g. Winter Overview) ──────────────────────────────────────

def overview_groups(categories):
    """Groups flagged `group_summary` in config (Winter Overview). Each gets its own dashboard tab."""
    return [g for g, r in group_rollup(categories).items() if r["summary"]]


def _roas_color(roas, cfg):
    t = (cfg or {}).get("roas_thresholds") or {"good": 2.5, "ok": 2.0, "warn": 1.5}
    if roas >= t["good"]:
        return "#22c55e"
    if roas >= t["ok"]:
        return "#86efac"
    if roas >= t["warn"]:
        return "#f59e0b"
    return "#ef4444"


def overview_tab_html(categories, group, card_fn, min_purchases, cfg, card_floor=None):
    """
    Body of the "<group>" tab: the launch at a glance, then the best creative of each category.

      1. tiles       - spend, share of all spend, blended ROAS, units sold, ads, categories
      2. scoreboard  - EVERY category of the group (even ones with no ad above the card floor)
      3. top 10      - best creatives across the whole group, by ROAS
      4. per category- best-ROAS creative + top spender, as the usual ad cards

    card_fn(ad, kind) returns one ad card; kind is "best" or "top".
    "Best" = highest ROAS among ads with at least `min_purchases` purchases.
    """
    roll = group_rollup(categories).get(group)
    if not roll:
        return ""
    cats = [(n, c) for n, c in ordered_categories(categories).items() if c.get("group") == group]
    by_spend = sorted(cats, key=lambda x: -(x[1].get("total_spend") or 0))
    grand = sum((c.get("total_spend") or 0) for c in categories.values()) or 1

    def tile(label, value):
        return (f'<div style="flex:1;min-width:120px;"><div style="font-size:10px;color:#777;text-transform:uppercase;'
                f'letter-spacing:.6px;">{label}</div><div style="font-size:21px;font-weight:700;color:#e8e8e8;'
                f'margin-top:3px;font-variant-numeric:tabular-nums;">{value}</div></div>')

    tiles = (
        '<div style="display:flex;gap:18px;flex-wrap:wrap;background:#121212;border:1px solid #252525;'
        'border-radius:10px;padding:16px 18px;margin-bottom:22px;">'
        + tile("Spend", fmt_inr(roll["spend"]))
        + tile("Share of all spend", f'{roll["spend"] / grand * 100:.0f}%')
        + tile("Blended ROAS", f'<span style="color:{_roas_color(roll["roas"], cfg)};">{roll["roas"]:.2f}x</span>')
        + tile("Units sold", f'{roll["units"]:,}' if roll["has_units"] else "\u2014")
        + tile("Ads counted", roll["ads"])
        + tile("Categories", roll["cats"])
        + "</div>"
    )

    # scoreboard
    th = 'style="text-align:left;font-weight:600;color:#777;padding:8px 12px;font-size:10px;text-transform:uppercase;letter-spacing:.5px;"'
    rows = ""
    for n, c in by_spend:
        spend = c.get("total_spend") or 0
        pct = spend / roll["spend"] * 100 if roll["spend"] else 0
        shown = len(c.get("top_ads", []))
        units = c.get("total_purchases")
        shown_txt = f"{shown}" if shown else '<span style="color:#666;">none above floor</span>'
        rows += (
            '<tr style="border-top:1px solid #1d1d1d;">'
            f'<td style="padding:9px 12px;color:#e8e8e8;font-weight:600;">{c.get("emoji", "")} {escape(n)}</td>'
            f'<td style="padding:9px 12px;color:#ddd;font-variant-numeric:tabular-nums;">{fmt_inr(spend)}</td>'
            '<td style="padding:9px 12px;min-width:130px;"><div style="display:flex;align-items:center;gap:8px;">'
            '<div style="flex:1;background:#1f1f1f;border-radius:4px;height:7px;">'
            f'<div style="background:#3b82f6;height:7px;border-radius:4px;width:{pct:.0f}%;"></div></div>'
            f'<span style="color:#888;font-size:11px;width:30px;text-align:right;">{pct:.0f}%</span></div></td>'
            f'<td style="padding:9px 12px;font-weight:700;color:{_roas_color(c.get("blended_roas", 0), cfg)};">{c.get("blended_roas", 0):.2f}x</td>'
            f'<td style="padding:9px 12px;color:#ddd;font-variant-numeric:tabular-nums;">{f"{units:,}" if units is not None else chr(8212)}</td>'
            f'<td style="padding:9px 12px;color:#aaa;">{c.get("total_ads", 0)}</td>'
            f'<td style="padding:9px 12px;color:#aaa;">{shown_txt}</td></tr>'
        )
    scoreboard = (
        '<div style="background:#121212;border:1px solid #252525;border-radius:10px;margin-bottom:26px;overflow-x:auto;">'
        '<table style="width:100%;border-collapse:collapse;font-size:12.5px;"><thead><tr>'
        f'<th {th}>Category</th><th {th}>Spend</th><th {th}>Share of {escape(group)}</th><th {th}>Blended ROAS</th>'
        f'<th {th}>Units sold</th><th {th}>Ads counted</th><th {th}>Cards shown</th></tr></thead><tbody>{rows}</tbody></table></div>'
    )

    # best creatives across the whole group
    pool = [(n, a) for n, c in cats for a in c.get("top_ads", []) if a.get("purchases", 0) >= min_purchases]
    pool.sort(key=lambda x: -x[1].get("roas", 0))
    seen, top_rows = set(), ""
    for n, a in pool:
        if a["ad_name"] in seen:
            continue
        seen.add(a["ad_name"])
        nm = a["ad_name"] if len(a["ad_name"]) <= 78 else a["ad_name"][:77] + "\u2026"
        top_rows += (
            '<tr style="border-top:1px solid #1d1d1d;">'
            f'<td style="padding:8px 12px;color:#777;">{len(seen)}</td>'
            f'<td style="padding:8px 12px;color:#ddd;white-space:nowrap;">{escape(n)}</td>'
            f'<td style="padding:8px 12px;color:#ccc;">{escape(nm)}</td>'
            f'<td style="padding:8px 12px;color:#ddd;font-variant-numeric:tabular-nums;">{fmt_inr(a["spend"])}</td>'
            f'<td style="padding:8px 12px;font-weight:700;color:{_roas_color(a["roas"], cfg)};">{a["roas"]:.2f}x</td>'
            f'<td style="padding:8px 12px;color:#ddd;">{a["purchases"]:,}</td></tr>'
        )
        if len(seen) >= 10:
            break
    top10 = ""
    if top_rows:
        top10 = (
            f'<div style="font-size:13px;font-weight:700;color:#e8e8e8;margin:0 0 4px;">Best of {escape(group)} \u2014 top creatives by ROAS</div>'
            f'<div style="font-size:11px;color:#777;margin-bottom:10px;">Across all categories \u00b7 at least {min_purchases} purchases'
            + (f' \u00b7 ads with \u20b9{card_floor:,.0f}+ spend' if card_floor else "") + '</div>'
            '<div style="background:#121212;border:1px solid #252525;border-radius:10px;margin-bottom:28px;overflow-x:auto;">'
            '<table style="width:100%;border-collapse:collapse;font-size:12.5px;"><thead><tr>'
            f'<th {th}>#</th><th {th}>Category</th><th {th}>Creative</th><th {th}>Spend</th><th {th}>ROAS</th><th {th}>Units</th>'
            f'</tr></thead><tbody>{top_rows}</tbody></table></div>'
        )

    # best of each category, as cards
    blocks = ""
    for n, c in by_spend:
        top = c.get("top_ads", [])
        if not top:
            continue
        qual = [a for a in top if a.get("purchases", 0) >= min_purchases]
        best = max(qual, key=lambda a: a["roas"]) if qual else None
        picks = []
        if best:
            picks.append((best, "best"))
        if not best or top[0]["ad_name"] != best["ad_name"]:
            picks.append((top[0], "top"))
        units = c.get("total_purchases")
        note = "" if best else f' \u00b7 <span style="color:#777;">no ad has {min_purchases}+ purchases yet \u2014 showing top spender</span>'
        blocks += (
            '<div class="sec"><div class="sec-hdr"><div>'
            f'<div class="sec-t">{c.get("emoji", "")} {escape(n)}</div>'
            f'<div class="sec-s">{c.get("total_ads", 0)} ads \u00b7 {fmt_inr(c.get("total_spend", 0))} \u00b7 '
            f'{c.get("blended_roas", 0):.2f}x blended ROAS'
            + (f' \u00b7 {units:,} units sold' if units is not None else "") + f'{note}</div></div></div>'
            f'<div class="cgrid">{"".join(card_fn(a, k) for a, k in picks)}</div></div>'
        )
    if blocks:
        blocks = ('<div style="font-size:13px;font-weight:700;color:#e8e8e8;margin:4px 0 4px;">Best creative per category</div>'
                  '<div style="font-size:11px;color:#777;margin-bottom:12px;">\u2605 Best ROAS first, then the top spender</div>') + blocks

    return tiles + scoreboard + top10 + blocks


# ── Slack ────────────────────────────────────────────────────────────────────

def slack_group_lines(categories, limit=5):
    """
    Top-N group lines for Slack from a data["categories"] dict, by spend.
    Returns None when the data has no groups (caller falls back to per-category lines).
    """
    roll = group_rollup(categories)
    if not roll:
        return None
    rows = sorted(roll.items(), key=lambda kv: kv[1]["spend"], reverse=True)[:limit]
    return [f'{r["emoji"]} {g[:22]} · {fmt_inr(r["spend"])} · {r["roas"]:.2f}x' for g, r in rows]


def slack_group_lines_from_ads(by_cat, categories_cfg, limit=5):
    """Same, but from raw {category: [ads]} (the daily Slack section works on ad rows, not JSON)."""
    agg = defaultdict(lambda: {"spend": 0.0, "cv": 0.0, "emoji": ""})
    group_emoji = {c["group"]: c["group_emoji"] for c in categories_cfg.values()
                   if c.get("group") and c.get("group_emoji")}
    for cat, ads in by_cat.items():
        if cat == "Other":
            continue
        cfg = categories_cfg.get(cat, {})
        if cfg.get("ignore"):
            continue
        g = cfg.get("group")
        if not g:
            return None
        a = agg[g]
        a["emoji"] = group_emoji.get(g) or a["emoji"] or cfg.get("emoji", "")
        a["spend"] += sum(x.get("spend", 0) for x in ads)
        a["cv"] += sum(x.get("conv_value", 0) for x in ads)
    if not agg:
        return None
    rows = sorted(agg.items(), key=lambda kv: kv[1]["spend"], reverse=True)[:limit]
    return [
        f'{a["emoji"]} {g[:22]} · {fmt_inr(a["spend"])} · {(a["cv"] / a["spend"] if a["spend"] else 0):.2f}x'
        for g, a in rows
    ]


# ── unmapped ad sets (so nothing is dropped silently) ────────────────────────

def summarise_unmapped(ads, top=12):
    """Summary of ads that matched no category: {total_spend, total_ads, ad_sets:[{name, spend, ads}]}."""
    if not ads:
        return {"total_spend": 0, "total_ads": 0, "ad_sets": []}
    sets = defaultdict(lambda: {"spend": 0.0, "ads": 0})
    for a in ads:
        s = sets[a.get("adset_name", "(unknown)")]
        s["spend"] += a.get("spend", 0)
        s["ads"] += 1
    ranked = sorted(sets.items(), key=lambda kv: kv[1]["spend"], reverse=True)
    return {
        "total_spend": sum(a.get("spend", 0) for a in ads),
        "total_ads": len(ads),
        "ad_sets": [{"name": n, "spend": round(v["spend"], 2), "ads": v["ads"]} for n, v in ranked[:top]],
    }


def unmapped_banner(unmapped):
    """Warning strip for the top of the Overview tab. Empty string when everything is mapped."""
    if not unmapped or not unmapped.get("total_spend"):
        return ""
    items = "".join(
        f'<li style="margin:2px 0;">{s["name"]} <span style="color:#777;">· {fmt_inr(s["spend"])} · {s["ads"]} ads</span></li>'
        for s in unmapped.get("ad_sets", [])
    )
    return (
        '<div style="margin:0 0 22px;padding:12px 16px;border:1px solid rgba(234,179,8,0.4);'
        'background:#1a1608;border-radius:8px;font-size:12px;color:#e8d9a0;">'
        f'<div style="font-weight:600;margin-bottom:6px;">⚠ {fmt_inr(unmapped["total_spend"])} across '
        f'{unmapped["total_ads"]} ads matched no category and is NOT shown below</div>'
        '<div style="color:#b8a96a;margin-bottom:4px;">Add a keyword for these ad sets in config.yaml:</div>'
        f'<ul style="margin:0;padding-left:18px;">{items}</ul></div>'
    )
