#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_app.py — Inajenga 96th Predictions app (index.html)
Kutoka pipeline ya betting-researcher: analyze_v3 (vyanzo 5 + historia engine).
"""
import json, os, sys, calendar, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/user/betting-researcher")
import analyze_v3 as A

SHORT = {"MARKET": "Soko (30+ BM)", "WC": "WinComparator", "FB": "Forebet",
         "FW": "Whispers", "FP": "FootballPredictions"}

def form_short(ctx):
    if not ctx:
        return None
    def pts(s):
        f = A.form_pts(ctx["form"][s])
        return (str(f) + "/15") if f is not None else "\u2014"
    return "H " + pts("home") + " \u00b7 A " + pts("away")

def slim(p):
    return {
        "market": p["market"], "selection": p["selection"], "type": p.get("type"),
        "odds": p["odds"],
        "final": round(p["final"], 3), "avg": round(p["avg"], 3),
        "hist": round(p["hist"], 3) if p["hist"] is not None else None,
        "n": p["n"],
        "src": [SHORT[s] for s in p["src"]],
        "flag": "ok" if "inathibitisha" in p["flag"] else ("warn" if "inapingana" in p["flag"] else "mid"),
        "form": form_short(p["ctx"]),
        "h2h": (p["ctx"] or {}).get("h2h", "\u2014"),
    }

def kickoff_ms(m):
    dt = datetime.datetime.strptime(m["date_gmt"] + " " + m["time_gmt"], "%Y-%m-%d %H:%M")
    return calendar.timegm(dt.utctimetuple()) * 1000

def main():
    raw, wc, fp, hist = A.load()
    picks, near, failed = A.analyze(raw, wc, fp, hist)

    bp, bn = {}, {}
    for p in picks:
        bp.setdefault(p["mid"], []).append(slim(p))
    for p in near:
        bn.setdefault(p["mid"], []).append(slim(p))

    now = datetime.datetime.utcnow()
    matches = []
    for m in raw["matches"]:
        mid = m["id"]
        matches.append({
            "id": mid, "home": m["home"], "away": m["away"], "comp": m["comp"],
            "kickoff": kickoff_ms(m),
            "picks": bp.get(mid, []),
            "near": bn.get(mid, []),
        })
    matches.sort(key=lambda x: x["kickoff"])

    data = {
        "generated_utc": now.strftime("%d/%m/%Y %H:%M"),
        "generated_ms": calendar.timegm(now.utctimetuple()) * 1000,
        "sources": ["Soko la Bookmakers (30+)", "WinComparator", "Forebet",
                    "Whispers", "FootballPredictions"],
        "matches": matches,
    }

    tpl = open(os.path.join(BASE, "template.html"), encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out = tpl.replace("__APP_DATA__", payload)
    path = os.path.join(BASE, "index.html")
    open(path, "w", encoding="utf-8").write(out)
    print(f"✅ {path}  ({len(out)//1024} KB)")
    print(f"   Mechi: {len(matches)} · Picks: {len(picks)} · Near: {len(near)} · Rejected: {len(failed)}")
    print(f"   Data generated (UTC): {data['generated_utc']}")

if __name__ == "__main__":
    main()
