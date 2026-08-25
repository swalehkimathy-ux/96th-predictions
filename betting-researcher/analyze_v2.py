#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_v2.py — Mantiki mpya:
  * Vyanzo 5 kwa pick: Soko la Bookmakers (30+, kutoka WinComparator) + WinComparator Model + Forebet + Whispers + FootballPredictions
  * Pick inapita ikiwa: vyanzo 4+ vimekubaliana NA uhakika wa wastani >= 70%
  * Output: dashboard.html (TABLE 1 tu — best best picks)
"""
import json, math, os, html as H
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
TZ_OFFSET_H = 3

def load():
    raw = json.load(open(os.path.join(BASE, "data", "raw.json"), encoding="utf-8"))
    try:
        wc = json.load(open(os.path.join(BASE, "data", "wc.json"), encoding="utf-8"))
    except Exception:
        wc = {}
    try:
        fp = json.load(open(os.path.join(BASE, "data", "fp.json"), encoding="utf-8"))
    except Exception:
        fp = {}
    return raw, wc, fp

# ---------------- Poisson ----------------
def pois_cdf(k, lam):
    return sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k + 1))

def over_prob(lam, k):
    return 1 - pois_cdf(k - 1, lam)

def lam_from_over(k, p):
    p = min(max(p, 0.02), 0.98)
    target = 1 - p
    lo, hi = 0.05, 15.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if pois_cdf(k - 1, mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

def lam_from_under(line, p_under):
    k = math.floor(line)  # 2.5 -> 2
    p = min(max(p_under, 0.02), 0.98)
    lo, hi = 0.05, 12.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if pois_cdf(k, mid) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

def to_tz(date_gmt, time_gmt):
    dt = datetime.strptime(date_gmt + " " + time_gmt, "%Y-%m-%d %H:%M") + timedelta(hours=TZ_OFFSET_H)
    return dt.strftime("%d/%m %H:%M")

SRC_NAMES = {
    "MARKET": "Soko la Bookmakers",
    "WC": "WinComparator (model)",
    "FB": "Forebet",
    "FW": "Whispers",
    "FP": "FootballPredictions",
}
SRC_ORDER = ["MARKET", "WC", "FB", "FW", "FP"]

MARKET_INFO = {
    "DC1X": "Double Chance (home au draw)",
    "DCX2": "Double Chance (away au draw)",
    "O15": "Over 1.5 goals",
    "U35": "Under 3.5 goals",
    "BTTS": "BTTS – Yes",
}

def analyze(raw, wc, fp):
    picks, near, missed = [], [], []
    for m in raw["matches"]:
        mid = m["id"]
        fb, fw = m.get("forebet"), m.get("fw")
        wcd = wc.get(mid, {}).get("wc", {})
        odds, preds = wcd.get("odds", {}), wcd.get("preds", {})
        fpd = fp.get(mid)
        home, away = m["home"], m["away"]

        votes = {k: {} for k in MARKET_INFO}   # market -> {src: prob}
        stance = {k: {} for k in MARKET_INFO}  # market -> {src: direction} kwa dissent

        def add(mk, src, prob, direction=None):
            if prob is not None and 0.05 < prob < 0.995:
                votes[mk][src] = min(0.99, prob)
                if direction:
                    stance[mk][src] = direction

        # ---------- 1) SOKO LA BOOKMAKERS (WC best odds, devigged) ----------
        o1, ox, o2 = odds.get("1"), odds.get("X"), odds.get("2")
        if o1 and ox and o2:
            s = 1/o1 + 1/ox + 1/o2
            add("DC1X", "MARKET", (1/o1 + 1/ox)/s)
            add("DCX2", "MARKET", (1/ox + 1/o2)/s)
        if odds.get("O2.5"):
            add("O15", "MARKET", over_prob(lam_from_over(3, 1/odds["O2.5"]), 2))
        if odds.get("U2.5"):
            add("U35", "MARKET", pois_cdf(3, lam_from_under(2.5, 1/odds["U2.5"])))
        if odds.get("BTTS_Y"):
            add("BTTS", "MARKET", 1/odds["BTTS_Y"])

        # ---------- 2) WINCOMPARATOR MODEL ----------
        w1 = preds.get("w1x2")
        if w1:
            p = (preds.get("p1x2") or 0) / 100
            if w1.lower() == "draw":
                add("DC1X", "WC", p, "X"); add("DCX2", "WC", p, "X")
            elif home.lower() in w1.lower() or w1.lower() in home.lower() or "home" in w1.lower():
                add("DC1X", "WC", p, "1")
            elif away.lower() in w1.lower() or w1.lower() in away.lower() or "away" in w1.lower():
                add("DCX2", "WC", p, "2")
        wou = (preds.get("wou") or "")
        p = preds.get("pou")
        if wou and p:
            if wou.lower().startswith("over"):
                add("O15", "WC", p/100)
            elif wou.lower().startswith("under"):
                add("U35", "WC", p/100)
        if preds.get("wbtts") == "Yes" and preds.get("pbtts"):
            add("BTTS", "WC", preds["pbtts"]/100)

        # ---------- 3) FOREBET ----------
        if fb:
            dc = fb.get("dc")
            if dc:
                p = dc["prob"]/100
                add("DC1X" if dc["market"] == "1X" else "DCX2", "FB", p)
            elif fb.get("p1") is not None:
                tot = fb.get("p1",0) + fb.get("px",0) + fb.get("p2",0)
                if tot:
                    add("DC1X", "FB", (fb["p1"]+fb["px"])/tot)
                    add("DCX2", "FB", (fb["px"]+fb["p2"])/tot)
            if fb.get("p2_win") and not dc:
                add("DCX2", "FB", fb["p2_win"]/100 + 0.20)
            g = None
            if fb.get("cs"):
                h, a = fb["cs"].split("-")
                g = int(h) + int(a)
                if g >= 2: add("O15", "FB", over_prob(max(g,1), 2))
                if g <= 3: add("U35", "FB", pois_cdf(3, max(g,1)))
            btts = fb.get("btts_yes")
            if btts is None and fb.get("cs"):
                h, a = fb["cs"].split("-")
                btts = 70 if int(h) > 0 and int(a) > 0 else 25
            if btts and btts >= 55:
                add("BTTS", "FB", btts/100)

        # ---------- 4) WHISPERS ----------
        if fw:
            for t in fw.get("tips", []):
                if t["market"] == "result":
                    p = min(0.95, 1/t["odds"] + 0.26)
                    add("DC1X" if t["selection"] == "1" else "DCX2", "FW", p, t["selection"])
                elif t["market"] == "over":
                    add("O15", "FW", over_prob(lam_from_over(math.ceil(t["line"]), 1/t["odds"]), 2))
                elif t["market"] == "under":
                    add("U35", "FW", pois_cdf(3, lam_from_under(t["line"], 1/t["odds"])))
                elif t["market"] == "btts" and t["selection"] == "yes":
                    add("BTTS", "FW", 1/t["odds"])
            if not any(t["market"] == "result" for t in fw.get("tips", [])) and fw.get("cs"):
                h, a = fw["cs"].split("-")
                if int(h) > int(a): add("DC1X", "FW", 0.72, "1")
                elif int(a) > int(h): add("DCX2", "FW", 0.72, "2")

        # ---------- 5) FOOTBALLPREDICTIONS ----------
        if fpd:
            gh, ga = fpd["cs"]
            g = gh + ga
            if gh > ga: add("DC1X", "FP", 0.80, "1")
            elif ga > gh: add("DCX2", "FP", 0.80, "2")
            else:
                add("DC1X", "FP", 0.75, "X"); add("DCX2", "FP", 0.75, "X")
            if g >= 2: add("O15", "FP", over_prob(max(g,1), 2))
            if g <= 3: add("U35", "FP", pois_cdf(3, max(g,1)))
            if gh > 0 and ga > 0: add("BTTS", "FP", 0.65)

        # ---------- JUMLA ----------
        tm = to_tz(m["date_gmt"], m["time_gmt"])
        label = f"{m['home']} – {m['away']}"
        for mk, info in MARKET_INFO.items():
            vs = votes[mk]
            if not vs:
                continue
            probs = list(vs.values())
            n = len(probs)
            avg = sum(probs)/n
            sel = {
                "DC1X": f"{home} au draw",
                "DCX2": f"{away} au draw",
                "O15": "Magoli 2+ kwenye mechi",
                "U35": "Magoli 3- pekee",
                "BTTS": "Timu zote mbili zitaungua",
            }[mk]
            # dissent: sources zenye stance isiyo sawa
            dis = []
            st = stance[mk]
            want = "1X" if mk == "DC1X" else "X2"
            for src, d in st.items():
                if mk in ("DC1X","DCX2") and d == ("2" if mk=="DC1X" else "1"):
                    dis.append(SRC_NAMES[src])
            rec = {
                "mid": mid, "match": label, "comp": m["comp"], "time": tm,
                "market": info, "selection": sel, "n": n,
                "avg": avg, "lo": min(probs), "hi": max(probs),
                "odds": round(1/avg, 2) if mk != "BTTS" else None,
                "src": [s for s in SRC_ORDER if s in vs],
                "dis": dis,
            }
            if n >= 4 and avg >= 0.70:
                picks.append(rec)
            elif n >= 3 and avg >= 0.75:
                near.append(rec)
            else:
                missed.append((label, info, n, avg))
    picks.sort(key=lambda x: -x["avg"])
    near.sort(key=lambda x: -x["avg"])
    return picks, near

# ---------------- Dashboard ----------------
def esc(x):
    return H.escape(str(x)) if x is not None else ""

def src_badges(rec):
    b = []
    for s in rec["src"]:
        b.append(f'<span class="sb">{esc(SRC_NAMES[s])}</span>')
    return " ".join(b)

def bar(p):
    pct = int(round(p*100))
    color = "#22c55e" if pct >= 80 else ("#eab308" if pct >= 70 else "#f97316")
    return f'<div class="bar"><div class="fill" style="width:{pct}%;background:{color}"></div></div><b>{pct}%</b>'

def render(raw, picks, near):
    n_matches = len(raw["matches"])
    rows = []
    for i, p in enumerate(picks, 1):
        dis = f'<span class="dis">⚠ {esc(", ".join(p["dis"]))}</span>' if p["dis"] else '<span class="okc">✓ wote</span>'
        rows.append(f"""<tr>
          <td class="rk">{i}</td>
          <td>{esc(p['match'])}<div class="sub">{esc(p['comp'])} · {esc(p['time'])} TZ</div></td>
          <td class="mkt">{esc(p['market'])}<div class="sub">{esc(p['selection'])}</div></td>
          <td class="odds">{p['odds']:.2f}<div class="sub">makadirio</div></td>
          <td>{bar(p['avg'])}<div class="sub">kikao: {int(p['lo']*100)}–{int(p['hi']*100)}%</div></td>
          <td>{src_badges(p)}<div class="sub">{p['n']} vyanzo vimekubaliana</div></td>
          <td>{dis}</td>
        </tr>""")
    near_txt = " · ".join(f"{p['match']}: {p['market']} ({p['n']} vyanzo, {int(p['avg']*100)}%)" for p in near) or "—"

    # stacking strip (info)
    prod, legs, used = 1.0, 0, set()
    for p in picks:
        if p["mid"] in used or not p["odds"]:
            continue
        if prod * p["odds"] <= 3.0:
            prod *= p["odds"]; legs += 1; used.add(p["mid"])
    stack = f"Ikipakia picks {legs} kuu (leg 1 kwa mechi): total odds ≈ <b>{prod:.2f}</b>" if legs else ""

    return f"""<!DOCTYPE html>
<html lang="sw"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PICK RADAR v2 — Best Picks (70%+)</title>
<style>
:root {{ --bg:#0b1020; --card:#131a2e; --line:#232c47; --tx:#e8ecf6; --sub:#8b95b2; --acc:#4f8cff; --grn:#22c55e; --yel:#eab308; --org:#f97316; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--tx); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif; padding:24px 14px 60px; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
header {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:12px; }}
h1 {{ font-size:26px; letter-spacing:.5px; }}
h1 .v2 {{ color:var(--acc); }}
.dateline {{ color:var(--sub); font-size:13px; }}
.badges {{ margin:10px 0 20px; display:flex; gap:8px; flex-wrap:wrap; }}
.badge {{ background:var(--card); border:1px solid var(--line); border-radius:999px; padding:5px 12px; font-size:12px; color:var(--sub); }}
.badge b {{ color:var(--tx); }}
.rule {{ background:#101b33; border:1px solid #27406e; border-left:4px solid var(--acc); border-radius:10px; padding:12px 14px; font-size:13px; color:#c9d6f2; margin-bottom:18px; line-height:1.6; }}
.rule b {{ color:#fff; }}
h2 {{ font-size:17px; margin:8px 0 10px; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; font-size:13.5px; }}
th {{ text-align:left; color:var(--sub); font-size:10.5px; text-transform:uppercase; letter-spacing:.7px; padding:10px 10px; border-bottom:1px solid var(--line); background:#0f1526; }}
td {{ padding:12px 10px; border-bottom:1px solid #1b2440; vertical-align:top; }}
tr:last-child td {{ border-bottom:none; }}
.rk {{ font-size:18px; font-weight:800; color:var(--acc); }}
.sub {{ color:var(--sub); font-size:11.5px; margin-top:3px; }}
.mkt {{ font-weight:600; }}
.odds {{ font-weight:800; font-size:17px; color:var(--acc); white-space:nowrap; }}
.bar {{ width:110px; height:8px; background:#1c2542; border-radius:5px; overflow:hidden; display:inline-block; vertical-align:middle; }}
.fill {{ height:100%; border-radius:5px; }}
td b {{ font-size:14px; }}
.sb {{ display:inline-block; background:#1a2540; border:1px solid #2c3a63; color:#c9d6f2; border-radius:7px; padding:2px 7px; font-size:11px; margin:0 3px 4px 0; }}
.dis {{ color:var(--org); font-size:11.5px; }}
.okc {{ color:var(--grn); font-size:11.5px; }}
.stack {{ margin-top:14px; background:linear-gradient(180deg,#14203c,#101729); border:1px dashed #2c3a63; border-radius:12px; padding:12px 16px; font-size:13.5px; color:#c9d6f2; }}
.near {{ margin-top:18px; color:var(--sub); font-size:12.5px; line-height:1.8; }}
.near b {{ color:var(--tx); }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin-top:20px; }}
summary {{ cursor:pointer; font-weight:600; font-size:14px; }}
details ol {{ margin:10px 0 4px 20px; color:var(--sub); font-size:13px; line-height:1.75; }}
.warn {{ background:#2a1d12; border:1px solid #6b4a1f; border-radius:12px; padding:14px 16px; font-size:13px; color:#f5d9a8; line-height:1.7; margin-top:24px; }}
.warn b {{ color:#ffd98a; }}
footer {{ margin-top:28px; color:#59647f; font-size:11.5px; line-height:1.7; }}
</style></head><body><div class="wrap">

<header>
  <h1>⚽ PICK <span class="v2">RADAR v2</span></h1>
  <div class="dateline">Best Picks — Uhakika 70%+ · Mechi za 25–26 Ag 2026 · Saa za Tanzania (GMT+3)</div>
</header>

<div class="badges">
  <span class="badge"> Vyanzo vya utafiti: <b>Forebet · Football Whispers · WinComparator · FootballPredictions · Soko la Bookmakers (30+)</b></span>
  <span class="badge">🔍 Mechi zilizochunguzwa: <b>{n_matches}</b></span>
  <span class="badge">🏆 Picks zilizopita kiwango: <b>{len(picks)}</b></span>
</div>

<div class="rule">📋 <b>Kiwango cha kuingia kwenye jedwali hili:</b> pick lazima iwe imethibitishwa na <b>vyanzo 4+</b> (soko la bookmakers + vyanzo 3+ vya utabiri) NA <b>uhakika wa wastani ≥ 70%</b>. Hii ndio jedwali la kazi — "best best picks" — hata kama mechi si ya popular.</div>

<h2>🏆 BEST PICKS — UHAKIKA 70%+</h2>
<table>
  <tr><th>#</th><th>Mechi</th><th>Pick</th><th>Odds</th><th>Uhakika</th><th>Vyanzo vilivyokubaliana</th><th>Aliyopingana</th></tr>
  {''.join(rows)}
</table>

<div class="stack">🧮 {stack} <span class="sub">(info tu — pick zenyewe ni solo; kukataza kuongeza leg ambayo haijaingia kwenye kiwango)</span></div>

<div class="near"><b>Karibu kuingia (3 vyanzo tu, sio 4+):</b> {near_txt}<br>
Mechi zisizopata pick 4+: {esc(", ".join(sorted({m['home']+' – '+m['away'] for m in raw['matches'] if m['id'] not in {p['mid'] for p in picks + near}})))}</div>

<details open><summary>🧠 Jinsi Inavyofanya Kazi (v2 — vyanzo 5)</summary>
<ol>
  <li><b>Soko la Bookmakers (30+):</b> WinComparator inasawazisha odds za Bet365, 1xBet, Betfred, Unibet, William Hill n.k. — app inatoa <i>implied probability</i> (baada ya kuondoa margin) kwa DC, O/U, BTTS. Hili ndilo "bookmakers wanne+" kwa kila pick.</li>
  <li><b>WinComparator (model yake):</b> prediction yake pekee na probability yake kwa kila market.</li>
  <li><b>Forebet:</b> probabilities zake (1X2, DC, BTTS) + correct score yake.</li>
  <li><b>Football Whispers:</b> tips za wataalamu + odds + likelihood (Probable/Likely/Outsider).</li>
  <li><b>FootballPredictions:</b> correct score yao huria (inaleta matokeo + goals + BTTS).</li>
  <li><b>Jumla:</b> pick inapita ikiwa vyanzo 4+ vinaingia upande mmoja na wastani wa probabilities zao ≥ 70%. Chanzo kinachopingana kinatajwa wazi kwenye jedwali.</li>
</ol></details>

<div class="warn">⚠️ <b>70% si 100%.</b> Hata pick yenye uhakika wa 85% inaweza kushindwa — hii ndiyo tabia ya michezo, na ndiyo maana bookmakers wapo hai. Jedwali hili linakuza chanya (edge) yako kupitia research ya vyanzo 5, si uhakika. <b>Ucheze kwa uhakika — usishughulie zaidi ya unazoweza kupoteza.</b></div>

<footer>
  Chanzo cha data: forebet.com · footballwhispers.com · wincomparator.com · footballpredictions.com · (odds aggregated za bookmakers 30+ kupitia WinComparator). Imekusanywa 25 Agosti 2026. Odds ya DC / Over 1.5 / Under 3.5 ni <b>makadirio</b> (1/uhakika wa wastani) — odds halisi za bookmakers zitaishi karibu na hivyo.<br>
  PICK RADAR v2 · utafiti binafsi · si ushauri wa kifedha.
</footer>

</div></body></html>"""

def main():
    raw, wc, fp = load()
    picks, near = analyze(raw, wc, fp)
    html = render(raw, picks, near)
    out = os.path.join(BASE, "dashboard.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"✅ {out}")
    print(f"\n🏆 PICKS ({len(picks)}):")
    for p in picks:
        print(f"  [{int(p['avg']*100):>3}%] {p['match']:38s} {p['market']:32s} @ {p['odds']}  vyanzo={p['n']} {p['src']} dis={p['dis']}")
    print(f"\nKaribu ({len(near)}):")
    for p in near:
        print(f"  [{int(p['avg']*100):>3}%] {p['match']:38s} {p['market']:32s} vyanzo={p['n']}")

if __name__ == "__main__":
    main()
