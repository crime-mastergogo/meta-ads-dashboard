"""
Shared helpers for the grouped category taxonomy (config.yaml -> `group:` on each category).

Dependency-free so every generator and main script can import it.

Backward compatible by design: data written before groups existed has no "group"/"order"
keys, and every helper here then behaves like the old flat layout (insertion order,
no group headers, per-category Slack lines).
"""
from collections import defaultdict

LEGACY_ORDER = 10 ** 6


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
    """{group: {spend, ads, roas, cats, emoji}} in display order. Ungrouped categories are skipped."""
    out = {}
    for _cat, c in ordered_categories(categories).items():
        g = c.get("group")
        if not g:
            continue
        r = out.setdefault(g, {"spend": 0.0, "cv": 0.0, "ads": 0, "cats": 0, "emoji": c.get("emoji", "")})
        if c.get("group_emoji"):
            r["emoji"] = c["group_emoji"]
        spend = c.get("total_spend", 0) or 0
        r["spend"] += spend
        r["cv"] += spend * (c.get("blended_roas", 0) or 0)
        r["ads"] += c.get("total_ads", 0) or 0
        r["cats"] += 1
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
        return (
            '<div class="sec grp-hdr" data-cat="__group__" '
            'style="margin:34px 0 16px;padding:12px 16px;border-left:3px solid #3b82f6;'
            'background:#121212;border-radius:0 8px 8px 0;">'
            f'<div style="font-size:15px;font-weight:700;color:#e8e8e8;letter-spacing:.2px;">{r["emoji"]} {g}</div>'
            '<div style="font-size:11px;color:#777;margin-top:4px;font-variant-numeric:tabular-nums;">'
            f'{n} categor{"y" if n == 1 else "ies"} · {r["ads"]} ads · {fmt_inr(r["spend"])} · {r["roas"]:.2f}x blended ROAS'
            '</div></div>'
        )


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
