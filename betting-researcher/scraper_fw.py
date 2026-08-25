#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_fw.py — Kupokea tips za Football Whispers kwa siku (auto, bila login).

Njia: homepage -> links za blog posts za leo/kesho -> kila post ina "Whispers' Tips"
(market + odds + likelihood), Hot tip, BTTS, na Correct score.

Utumiaji:
    python3 scraper_fw.py            # inakuja data/fw_latest.json
Data hii baadaye hujumlishwa na data ya Forebet katika data/raw.json (manual kwa sasa;
Forebet inazuia curl moja kwa moja — inahitaji fetch kama ya browser au API headless).
"""
import json, os, re, html as H, sys, urllib.request, datetime

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BASE = os.path.dirname(os.path.abspath(__file__))


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


def to_text(h):
    h = re.sub(r"<script.*?</script>", "", h, flags=re.S)
    h = re.sub(r"<style.*?</style>", "", h, flags=re.S)
    h = re.sub(r"<[^>]+>", "\n", h)
    h = H.unescape(h)
    h = re.sub(r"[ \t]+", " ", h)
    return re.sub(r"\n\s*\n+", "\n", h)


def frac_to_dec(s):
    """'3/4' -> 1.75, '1.75' -> 1.75"""
    if "/" in s:
        a, b = s.split("/")
        return round(1 + float(a) / float(b), 2)
    return float(s)


def next_nonempty(lines, i):
    j = i + 1
    while j < len(lines):
        if lines[j].strip():
            return lines[j].strip()
        j += 1
    return None


def classify(market, home, away):
    m = market.lower()
    if "to win" in m or m.startswith("match result"):
        team = market
        for t in (home, away):
            if t.lower() in m or m in t.lower():
                return {"market": "result", "selection": "1" if t == home else "2"}
        # "Match result: X" — cheza team ya mwisho kabla ya kama
        if "match result" in m:
            t = market.split(":", 1)[1].strip()
            return {"market": "result", "selection": "1" if t == home else "2"}
        return None
    if "to nil" in m:  # "Everton win to nil" -> away win
        for t in (home, away):
            if t and t.lower() in m:
                return {"market": "result", "selection": "1" if t == home else "2"}
        return None
    mo = re.search(r"over\s+([\d.]+)\s+total goals", m)
    mu = re.search(r"under\s+([\d.]+)\s+(?:total\s+)?goals", m)
    if mo:
        return {"market": "over", "line": float(mo.group(1))}
    if mu:
        return {"market": "under", "line": float(mu.group(1))}
    if "btts" in m or "both teams to score" in m:
        sel = "yes" if "yes" in m else ("no" if "no" in m else "yes")
        return {"market": "btts", "selection": sel}
    if re.match(r"^(over|under)\s+[\d.]+\s+cor", m):
        return None  # corners — hatupendae MVP hii
    return None


def parse_post(url, home, away):
    t = to_text(get(url))
    lines = t.split("\n")
    d = {"url": url, "tips": [], "hot_tip": None, "btts": None, "cs": None}
    # fixture/time
    m = re.search(r"(\d{2}/\d{2}/\d{2}) - (\d{2}:\d{2})\n([^\n]+?) - ([^\n]+)\n", t)
    if m:
        d["date_gmt"], d["time_gmt"] = m.group(1), m.group(2)
        home, away = m.group(3).strip(), m.group(4).strip()
    # Our predictions block
    for i, ln in enumerate(lines):
        if "Hot tip" in ln and d["hot_tip"] is None:
            d["hot_tip"] = next_nonempty(lines, i)
        if "Both Teams To Score" in ln and d["btts"] is None:
            d["btts"] = next_nonempty(lines, i)
        if "Correct score" in ln and d["cs"] is None:
            d["cs"] = next_nonempty(lines, i)
    # Whispers' Tips: format A "> X at 3/4 (1.75) | Likelihood: Y"  B "> X at 4/11 | Likelihood: Y"
    for m in re.finditer(r">\s*([^|\n]+?)\s+at\s+([\d/\.]+)(?:\s*\(([\d\.]+)\))?\s*\|?\s*\n?\s*Likelihood:\s*([^\n]+)", t):
        market, fr, dec, lik = m.group(1).strip(), m.group(2), m.group(3), m.group(4).strip()
        c = classify(market, home, away)
        if not c:
            continue
        c["odds"] = float(dec) if dec else frac_to_dec(fr)
        c["lik"] = lik
        d["tips"].append(c)
    return d


def main():
    home_html = get("https://footballwhispers.com/")
    t = to_text(home_html)
    today = (datetime.datetime.now() + datetime.timedelta(hours=3)).strftime("%d-%m-%Y")  # TZ
    tomorrow = (datetime.datetime.now() + datetime.timedelta(hours=3) + datetime.timedelta(days=1)).strftime("%d-%m-%Y")
    links = sorted(set(re.findall(r'href="(https://footballwhispers\.com/blog/[^"]+)"', home_html)))
    out = []
    for url in links:
        if not any(d in url for d in (today, tomorrow)):
            continue
        m = re.match(r".*/blog/(.+?)-(\d{2}-\d{2}-\d{4})/?$", url)
        if not m:
            continue
        slug, date = m.group(1), m.group(2)
        home, away = slug.split("-vs-") if "-vs-" in slug else (slug, None)
        home = re.sub(r"-(?=[a-z])", " ", home).title()
        away = re.sub(r"-(?=[a-z])", " ", away).title() if away else ""
        try:
            d = parse_post(url, home, away)
            d["date_gmt"] = date[:2] + "/" + date[3:5] + "/" + date[-2:]  # slug ndio halali
            d["home_guess"], d["away_guess"] = home, away
            out.append(d)
            print(f"✅ {d.get('date_gmt','?')} {home} vs {away}: {len(d['tips'])} tips")
        except Exception as e:
            print(f"❌ {url}: {e}", file=sys.stderr)
    out_path = os.path.join(BASE, "data", "fw_latest.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(out, open(out_path, "w"), indent=1)
    print(f"\n💾 {out_path} — posts {len(out)}")


if __name__ == "__main__":
    main()
