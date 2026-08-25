#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_wc_be.py — Inakuja odds aggregated kutoka WinComparator na BetExplorer kwa mechi 13.
Output: data/wc_be.json  + summary kwenye terminal.
"""
import json, os, re, sys, urllib.request, concurrent.futures

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BASE = os.path.dirname(os.path.abspath(__file__))

M = {
    "bham-brentford":   ("birmingham-brentford-8731361", "nottingham-forest-leeds-8731382"),
    "lask-celtic":      ("lask-linz-celtic-8703930", None),
    "forest-leeds":     ("nottingham-forest-leeds-8731382", None),
    "aek-levski":       ("aek-athens-levski-sofia-8703928", None),
    "bradford-burnley": ("bradford-city-burnley-8731372", None),
    "celje-slovan":     ("celje-slovan-bratislava-8703934", None),
    "lyon-fenerbahce":  ("lyon-fenerbahce-8703924", None),
    "newcastle-westbrom": ("newcastle-west-brom-8731365", None),
    "preston-everton":  ("preston-north-end-everton-8731383", None),
    "rapid-hearts":     ("rapid-wien-heart-of-midlothian-8703991", None),
    "realmadrid-sociedad": ("real-madrid-real-sociedad-8586420", None),
    "tottenham-charlton": ("tottenham-charlton-athletic-8731362", None),
    "viking-dinamo":    ("viking-fk-gnk-dinamo-zagreb-8703932", None),
}
WC_SLUG = {k: v[0] for k, v in M.items()}

# BetExplorer slugs (kuchunguzwa kutoka be.html / league pages)
BE_SLUG = {
    "lask-celtic": "/football/europe/champions-league/lask-linz-celtic/OOklm0j3/",
}

def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")

def strip_tags(h):
    h = re.sub(r"<script.*?</script>", "", h, flags=re.S)
    h = re.sub(r"<style.*?</style>", "", h, flags=re.S)
    h = re.sub(r"<[^>]+>", "|", h)
    h = re.sub(r"\|+", "|", h)
    h = h.replace("&nbsp;", " ")
    return h

def parse_wc(html):
    """WinComparator match page -> best odds kwa markets makuu."""
    d = {"odds": {}, "wc_prediction": None}
    t = strip_tags(html)
    # 1X2: "Home | 4.2 | Draw | 3.6 | Away | 1.79" — sehemu ya kwanza baada ya "BET NOW!"
    m = re.search(r"BET NOW!\|([^|]{2,40})\|([0-9.]+)\|Draw\|([0-9.]+)\|([^|]{2,40})\|([0-9.]+)", t)
    if m:
        d["odds"]["1"] = float(m.group(2)); d["odds"]["X"] = float(m.group(4)); d["odds"]["2"] = float(m.group(6))
    # O/U lines: "Under 2.5 goals | ... | 1.88 | Over 2.5 goals | ... | 1.98"
    for line in ("1.5", "2.5", "3.5"):
        m = re.search(r"Under " + line + r" goals\|[^|]*\|([0-9.]+)\|Over " + line + r" goals\|[^|]*\|([0-9.]+)", t)
        if m:
            d["odds"]["U" + line] = float(m.group(1)); d["odds"]["O" + line] = float(m.group(2))
    # BTTS
    m = re.search(r"BTTS Odds\|Yes\|[^|]*\|([0-9.]+)\|No\|[^|]*\|([0-9.]+)", t)
    if m:
        d["odds"]["BTTS_Y"] = float(m.group(1)); d["odds"]["BTTS_N"] = float(m.group(2))
    # Double chance sections (ikipo)
    m = re.search(r"1X[^|]{0,20}\|([0-9.]+)\|X2[^|]{0,20}\|([0-9.]+)", t)
    # WC own prediction + probability (listing-page style)
    m = re.search(r"Prediction:\|?\s*\|?([^|\n]{1,30})\|[^|\n]*Probability:\s*([0-9]+)%", t)
    if m:
        d["wc_prediction"] = {"pick": m.group(1).strip(), "prob": int(m.group(2))}
    return d

def parse_be(html):
    """BetExplorer match odds page -> average odds + idadi ya bookmakers."""
    d = {"avg_odds": {}, "n_bookmakers": None}
    t = strip_tags(html)
    m = re.search(r"(\d+) bookmakers", t)
    if m:
        d["n_bookmakers"] = int(m.group(1))
    # "Average" odds table: 1 | X | 2 rows
    for key, pat in [("1X2_1", r"Average\|([0-9.]+)\|([0-9.]+)\|([0-9.]+)"),
                     ("OU", r"2\.5\s*Under\|([0-9.]+)\|2\.5\s*Over\|([0-9.]+)")]:
        m = re.search(pat, t)
        if m:
            if key == "1X2_1":
                d["avg_odds"]["1"] = float(m.group(1)); d["avg_odds"]["X"] = float(m.group(2)); d["avg_odds"]["2"] = float(m.group(3))
            else:
                d["avg_odds"]["U2.5"] = float(m.group(1)); d["avg_odds"]["O2.5"] = float(m.group(2))
    return d

def main():
    out = {}
    # WinComparator pages (parallel)
    def wc_fetch(mid):
        url = f"https://www.wincomparator.com/predictions/{WC_SLUG[mid]}/"
        try:
            return mid, parse_wc(get(url))
        except Exception as e:
            return mid, {"error": str(e)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for mid, res in ex.map(wc_fetch, WC_SLUG):
            out[mid] = {"wc": res}
            print(f"WC {mid}: {res if 'error' in res else 'ok ' + str(res.get('odds'))}")
    # BetExplorer: kwanza league pages kupata slugs
    be_leagues = {
        "efl": "https://www.betexplorer.com/football/england/efl-cup/",
        "laliga": "https://www.betexplorer.com/football/spain/laliga/",
        "uecl": "https://www.betexplorer.com/football/europe/uefa-europa-conference-league/",
    }
    be_links = {}
    for name, url in be_leagues.items():
        try:
            h = get(url)
            for l in re.findall(r'href="(/football/[^"]+)"', h):
                if re.search(r"/[a-z0-9-]+/[A-Za-z0-9_-]{8}/$", l):
                    be_links[l] = True
            print(f"BE {name}: {len(re.findall(chr(34)+'/football/', h))} links")
        except Exception as e:
            print(f"BE {name} error: {e}")
    be_links = list(be_links)
    print("BE match links found:", len(be_links))
    for mid in out:
        slug = BE_SLUG.get(mid)
        if not slug:
            # fungua kutoka slugs
            norm = WC_SLUG[mid].rsplit("-", 1)[0]
            for l in be_links:
                base = l.split("/")[-2]
                if base.replace("-", " ") == norm.replace("-", " "):
                    slug = l
                    break
        if slug:
            try:
                out[mid]["be"] = parse_be(get("https://www.betexplorer.com" + slug))
                out[mid]["be_url"] = slug
                print(f"BE {mid}: ok n={out[mid]['be'].get('n_bookmakers')} avg={out[mid]['be'].get('avg_odds')}")
            except Exception as e:
                out[mid]["be"] = {"error": str(e)}
                print(f"BE {mid}: error {e}")
        else:
            out[mid]["be"] = {"not_found": True}
            print(f"BE {mid}: slug si ipatikana")
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    json.dump(out, open(os.path.join(BASE, "data", "wc_be.json"), "w"), indent=1)
    print(f"\n💾 data/wc_be.json imekwisha")

if __name__ == "__main__":
    main()
