"""
main_tags.py: pull the ad concept tags (Type, Theme, Messaging, Hook) from the team's Google Sheet.

The sheet must be shared as "anyone with the link can view" (no login or key is used). The "2026" tab is
downloaded as CSV, reduced to {ad name cut at its date -> [Type, Theme, Messaging, Hook]} for the last few
months, and written to data/ad_tags.json. Only those four labels are stored: nothing else from the sheet
(creator names, ad codes, copy, links) is copied into the repo.

main_month.py joins these tags to the Meta ads. If this pull fails the reason is written to data/tags_status.json
and shown on the November tab; the previous ad_tags.json keeps being used.
"""
import json
import os
import sys
import traceback
from datetime import datetime

import requests
import yaml

import contentmap as cm
import monthview as mv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
OUTPUT_DIR = os.path.join(BASE_DIR, "data")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_csv(sheet_id, gid):
    """CSV text of one tab of a link-shared Google Sheet."""
    r = requests.get("https://docs.google.com/spreadsheets/d/" + str(sheet_id) + "/export",
                     params={"format": "csv", "gid": str(gid)}, timeout=120,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; xyxx-dashboard)"})
    if r.status_code in (401, 403) or "accounts.google.com" in r.url:
        raise RuntimeError("Google asked for a login: set the sheet to 'anyone with the link can view'")
    if r.status_code != 200:
        raise RuntimeError("Google returned HTTP " + str(r.status_code) + " for the sheet")
    head = r.content[:300].lstrip().lower()
    if "text/html" in (r.headers.get("content-type") or "").lower() or head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise RuntimeError("Google returned a web page instead of CSV: the sheet is probably not shared with 'anyone with the link'")
    return r.content.decode("utf-8-sig", errors="replace")


def write_json(name, obj):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def run():
    cfg = load_config()
    ct = cfg.get("content_tags") or {}
    if not ct.get("sheet_id"):
        raise RuntimeError("content_tags.sheet_id is missing from config.yaml")
    print("[Tags] Downloading tab gid=" + str(ct.get("gid", 0)) + " of the Google Sheet...")
    text = fetch_csv(ct["sheet_id"], ct.get("gid", 0))
    today = mv.today_ist()
    cutoff = (today.year, today.month)
    for _ in range(max(int(ct.get("keep_months", 4)), 1) - 1):
        cutoff = mv.prev_month(cutoff)

    def keep(name):                       # only the last few months matter; older rows would just bloat the repo
        return (mv.parse_vintage(name) or (0, 0)) >= cutoff

    tags, stats = cm.parse_sheet_csv(text, ct.get("columns"), ct.get("aliases"), keep)
    if not stats["ad_rows"]:
        raise RuntimeError("No ad rows found in the sheet (looking for names that start like 'November2026-')")
    if not tags:
        raise RuntimeError("The sheet has ad rows, but none from the last " + str(ct.get("keep_months", 4)) + " months")
    write_json("ad_tags.json", {"generated_at": datetime.utcnow().isoformat(), "rows_seen": stats["rows_seen"],
                                "ad_rows": stats["ad_rows"], "kept": stats["kept"], "tagged": stats["tagged"],
                                "vocab": stats["vocab"], "tags": tags})
    write_json("tags_status.json", {"ok": True, "error": None, "at": datetime.utcnow().isoformat()})
    print("[Tags] " + str(stats["ad_rows"]) + " ad rows in the sheet | kept " + str(stats["kept"]) + " (last "
          + str(ct.get("keep_months", 4)) + " months) | with at least one concept: " + str(stats["tagged"]))
    for f, _n in cm.FIELDS:
        print("[Tags]   " + f + ": " + ", ".join(k + " " + str(v) for k, v in list(stats["vocab"][f].items())[:12]))


def main():
    try:
        run()
        return 0
    except Exception as e:                                   # noqa: BLE001 - record every failure where the dashboard can show it
        traceback.print_exc()
        write_json("tags_status.json", {"ok": False, "error": (type(e).__name__ + ": " + str(e))[:600], "at": datetime.utcnow().isoformat()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
