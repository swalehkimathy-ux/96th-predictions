#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_sources.py — v3
Inakuja:
  A) WinComparator: kwa kila mechi — best odds (1X2, O/U 1.5/2.5/3.5, BTTS) + WC model predictions
  B) FootballPredictions: correct-score prediction kwa kila mechi (kutoka league pages)
Output: data/wc.json + data/fp.json
"""
import json, os, re, urllib.request, concurrent.futures
from html.parser import HTMLParser

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BASE = os.path.dirname(os.path.abspath(__file__))

WC_SLUG = {
    "bham-brentford": "birmingham-brentford-8731361",
    "lask-celtic": "lask-linz-celtic-8703930",
    "forest-leeds": "nottingham-forest-leeds-8731382",
    "aek-levski": "aek-athens-levski-sofia-8703928",
    "bradford-burnley": "bradford-city-burnley-8731372",
    "celje-slovan": "celje-slovan-bratislava-8703934",
    "lyon-fenerbahce": "lyon-fenerbahce-8703924",
    "newcastle-westbrom": "newcastle-west-brom-8731365",
    "preston-everton": "preston-north-end-everton-8731383",
    "rapid-hearts": "rapid-wien-heart-of-midlothian-8703991",
    "realmadrid-sociedad": "real-madrid-real-sociedad-8586420",
    "tottenham-charlton": "tottenham-charlton-athletic-8731362",
    "viking-dinamo": "viking-fk-gnk-dinamo-zagreb-8703932",
}

def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")

# ---------------- WinComparator ----------------
class TransParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self._stack = []
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "data-trans" in d:
            self._stack.append((d["data-trans"], []))
    def handle_data(self, data):
        if self._stack:
            self._stack[-1][1].append(data)
    def handle_endtag(self, tag):
        if self._stack:
            trans, texts = self._stack[-1]
            self.items.append((trans, re.sub(r"\s+", " ", "".join(texts)).strip()))
            self._stack.pop()

def is_num(s):
    try:
        v = float(s)
        return 1.01 <= v <= 1000
    except Exception:
        return False

def parse_wc(html):
    d = {"odds": {}, "preds": {}}
    raw = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    toks = [x.strip() for x in re.sub(r"<[^>]+>", "|", raw).split("|")]
    toks = [x for x in toks if x]
    def odds_after(label, window=25):
        for i, x in enumerate(toks):
            if x == label:
                for j in range(i + 1, min(i + 1 + window, len(toks))):
                    if is_num(toks[j]):
                        return float(toks[j])
        return None
    for line in ("1.5", "2.5", "3.5"):
        u, o = odds_after(f"Under {line} goals"), odds_after(f"Over {line} goals")
        if u: d["odds"]["U" + line] = u
        if o: d["odds"]["O" + line] = o
    # BTTS: token lenye "BTTS Odds" (label kamiliki)
    i = next((k for k, x in enumerate(toks) if x.endswith("BTTS Odds")), None)
    if i:
        seq = toks[i + 1:i + 9]
        try:
            yi = seq.index("Yes"); ni = seq.index("No")
            d["odds"]["BTTS_Y"] = float(seq[yi + 1]); d["odds"]["BTTS_N"] = float(seq[ni + 1])
        except Exception:
            pass
    # 1X2: 'Draw' iliyoundwa na odds
    for i, x in enumerate(toks[:500]):
        if x == "Draw":
            pre = [t for t in toks[max(0, i - 6):i] if is_num(t)]
            post = [t for t in toks[i + 1:i + 8] if is_num(t)]
            if pre and len(post) >= 2:
                d["odds"]["1"] = float(pre[-1]); d["odds"]["X"] = float(post[0]); d["odds"]["2"] = float(post[1])
                break
    # WC model probabilities
    p = TransParser()
    try:
        p.feed(html)
    except Exception:
        pass
    for trans, text in p.items:
        text = text or ""
        m = re.search(r"(\d{1,2}\.?\d*)%", text)
        prob = float(m.group(1)) if m else None
        if trans.startswith("match.probability.1x2") and prob is not None:
            d["preds"]["p1x2"] = prob
        elif "under.over.probability" in trans and prob is not None:
            d["preds"]["pou"] = prob
        elif "btts.percent" in trans and prob is not None:
            d["preds"]["pbtts"] = prob
        # direction ya pick (maneno mafupi tu — title ndefu yaondolewe)
        if trans.startswith("match.probability.1x2") and len(text) <= 25 and not text.endswith("prediction") and "%" not in text and "robability" not in text:
            d["preds"]["w1x2"] = text
        elif trans.startswith("match.probability.under.over") and len(text) <= 30 and re.search(r"[OU]nder?", text):
            d["preds"]["wou"] = text
        elif trans.startswith("match.probability.btts") and text in ("Yes", "No"):
            d["preds"]["wbtts"] = text
    return d

# ---------------- FootballPredictions ----------------
FP_LEAGUES = {
    "efl": "https://footballpredictions.com/footballpredictions/eflcuppredictions/",
    "ucl": "https://footballpredictions.com/footballpredictions/championsleaguepredictions/",
    "uel": "https://footballpredictions.com/footballpredictions/europaleaguepredictions/",
    "laliga": "https://footballpredictions.com/footballpredictions/primeradivisionpredictions/",
}
FP_KEYS = {
    "bham-brentford": ("Birmingham", "Brentford"),
    "lask-celtic": ("LASK", "Celtic"),
    "forest-leeds": ("Forest", "Leeds"),
    "aek-levski": ("AEK", "Levski"),
    "bradford-burnley": ("Bradford", "Burnley"),
    "celje-slovan": ("Celje", "Slovan"),
    "lyon-fenerbahce": ("Lyon", "Fenerbahce"),
    "newcastle-westbrom": ("Newcastle", "Brom"),
    "preston-everton": ("Preston", "Everton"),
    "rapid-hearts": ("Rapid", "Midlothian"),
    "realmadrid-sociedad": ("Real Madrid", "Sociedad"),
    "tottenham-charlton": ("Tottenham", "Charlton"),
    "viking-dinamo": ("Viking", "Dinamo"),
}

def parse_fp(html):
    """Return dict: (home, away) -> (gh, ga) kwa kila match yenyé 'Prediction: a-b' kabla ya title."""
    t = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"\|+", "|", t)
    out = {}
    for tm in re.finditer(r"([A-Z][A-Za-z0-9 .&\u2019-]{2,40}?)\s+vs\s+([A-Z][A-Za-z0-9 .&\u2019-]{2,40}?)\s+Prediction\|", t):
        home, away = tm.group(1).strip(), tm.group(2).strip()
        back = t[max(0, tm.start() - 2000):tm.start()]
        pm = list(re.finditer(r"Prediction:", back))
        if not pm:
            continue
        after = back[pm[-1].end():]
        sm = re.search(r"\|(\d)\s*-\s*(\d)\|", after)
        if sm:
            out[(home, away)] = (int(sm.group(1)), int(sm.group(2)))
    return out

def main():
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    out = {}
    def wc_fetch(mid):
        try:
            return mid, parse_wc(get(f"https://www.wincomparator.com/predictions/{WC_SLUG[mid]}/"))
        except Exception as e:
            return mid, {"error": str(e)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for mid, res in ex.map(wc_fetch, WC_SLUG):
            out[mid] = {"wc": res}
            o = res.get("odds", {})
            pr = res.get("preds", {})
            print(f"WC {mid:22s} 1X2={o.get('1')}/{o.get('X')}/{o.get('2')} "
                  f"O25={o.get('O2.5')} U25={o.get('U2.5')} BTTSy={o.get('BTTS_Y')} | {pr}")
    # FP
    fp = {}
    for name, url in FP_LEAGUES.items():
        try:
            pairs = parse_fp(get(url))
            print(f"FP {name}: {len(pairs)} predictions")
            for (home, away), (gh, ga) in pairs.items():
                for mid, (kh, ka) in FP_KEYS.items():
                    if kh.lower() in home.lower() and ka.lower() in away.lower():
                        fp[mid] = {"home": home, "away": away, "cs": [gh, ga], "league": name}
        except Exception as e:
            print(f"FP {name} error: {e}")
    for mid, d in sorted(fp.items()):
        print(f"FP {mid:22s} -> {d['cs'][0]}-{d['cs'][1]} ({d['home']} vs {d['away']})")
    json.dump(out, open(os.path.join(BASE, "data", "wc.json"), "w"), indent=1)
    json.dump(fp, open(os.path.join(BASE, "data", "fp.json"), "w"), indent=1)
    print("\n💾 data/wc.json + data/fp.json")

if __name__ == "__main__":
    main()
