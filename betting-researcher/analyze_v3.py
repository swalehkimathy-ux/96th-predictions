#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_v3.py — v2 + HISTORIA ENGINE
  * Vyanzo 5 (Soko la Bookmakers, WC Model, Forebet, Whispers, FootballPredictions)
  * + Form ya sasa, Head-to-Head, ukubwa/quality ya timu, leg 1 (kwa mechi za two-leg)
  * Uhakika wa mwisho = 0.65 * (vyanzo) + 0.35 * (historia/form/H2H/quality)
  * Flag: ✅ historia inathibitisha | ≈ sawa | ⚠️ historia inaondokea na sources
"""
import json, math, os, html as H
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
TZ_OFFSET_H = 3

def load():
    raw = json.load(open(os.path.join(BASE, "data", "raw.json"), encoding="utf-8"))
    wc = json.load(open(os.path.join(BASE, "data", "wc.json"), encoding="utf-8"))
    fp = json.load(open(os.path.join(BASE, "data", "fp.json"), encoding="utf-8"))
    try:
        hist = json.load(open(os.path.join(BASE, "data", "history.json"), encoding="utf-8"))
    except Exception:
        hist = {}
    return raw, wc, fp, hist

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
    k = math.floor(line)
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

def clamp(p, lo=0.30, hi=0.96):
    return min(hi, max(lo, p))

SRC_NAMES = {
    "MARKET": "Soko (Bookmakers 30+)",
    "WC": "WinComparator",
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

# ---------------- HISTORIA ENGINE ----------------
def form_pts(s):
    if not s:
        return None
    s = s[-5:]
    return sum(3 if c == "W" else 1 if c == "D" else 0 for c in s)

def history_dc(side, ctx):
    opp = "away" if side == "home" else "home"
    fp = form_pts(ctx["form"][side])
    fprob = 0.42 + 0.53 * (fp / 15) if fp is not None else 0.60
    h2h = ctx["h2h_adv"]
    if h2h["side"] == side:
        hprob = {1: 0.62, 2: 0.70, 3: 0.78}.get(h2h["strength"], 0.55)
    elif h2h["side"] == "even":
        hprob = 0.55  # draws nyingi -> DC inasaidiwa
    elif h2h["side"] == "none":
        hprob = 0.50
    else:
        hprob = {1: 0.38, 2: 0.32, 3: 0.28}.get(h2h["strength"], 0.45)
    gap = ctx["quality"][opp] - ctx["quality"][side]
    qprob = min(0.95, max(0.35, 0.52 + 0.20 * gap))
    if abs(gap) >= 2:
        w = (0.35, 0.25, 0.40)   # gap kubwa -> quality ndio nguvu zaidi (cup)
    else:
        w = (0.45, 0.30, 0.25)
    p = w[0] * fprob + w[1] * hprob + w[2] * qprob
    leg = ctx.get("leg1") or ""
    if leg.startswith("home win"):
        p += 0.05 if side == "home" else -0.05
    elif leg.startswith("away win"):
        p += 0.05 if side == "away" else -0.05
    elif leg.startswith("draw"):
        p += 0.02
    return clamp(p)

def history_goals(ctx):
    hf, af = ctx["gf"]["home"], ctx["gf"]["away"]
    lam = (hf + af) * 0.9
    return clamp(over_prob(lam, 2)), clamp(pois_cdf(3, lam))

def history_btts(ctx):
    hf, af = ctx["gf"]["home"], ctx["gf"]["away"]
    p = (1 - math.exp(-hf * 0.85)) * (1 - math.exp(-af * 0.85))
    return clamp(p)

# ---------------- VOTING (kama v2) ----------------
def votes_for(mid, m, wc, fp, hist):
    fb, fw = m.get("forebet"), m.get("fw")
    wcd = wc.get(mid, {}).get("wc", {})
    odds, preds = wcd.get("odds", {}), wcd.get("preds", {})
    fpd = fp.get(mid)
    ctx = hist.get(mid)
    home, away = m["home"], m["away"]

    votes = {k: {} for k in MARKET_INFO}
    stance = {k: {} for k in MARKET_INFO}

    def add(mk, src, prob, direction=None):
        if prob is not None and 0.05 < prob < 0.995:
            votes[mk][src] = min(0.99, prob)
            if direction:
                stance[mk][src] = direction

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

    w1 = preds.get("w1x2")
    if w1:
        p = (preds.get("p1x2") or 0) / 100
        # Model inajenga outcome X -> probability ya DC ya upande huo = p + sehemu ya baadhi
        # (draw inapatikana kwa ajili ya DC; baadhi = (1-p) imetenganwa kati ya draw na upande mwingine)
        pv_side = p + 0.55 * (1 - p)
        pv_draw = p + 0.45 * (1 - p)
        if w1.lower() == "draw":
            add("DC1X", "WC", pv_draw, "X"); add("DCX2", "WC", pv_draw, "X")
        elif home.lower() in w1.lower() or w1.lower() in home.lower() or "home" in w1.lower():
            add("DC1X", "WC", pv_side, "1")
        elif away.lower() in w1.lower() or w1.lower() in away.lower() or "away" in w1.lower():
            add("DCX2", "WC", pv_side, "2")
    wou = (preds.get("wou") or "")
    p = preds.get("pou")
    if wou and p:
        if wou.lower().startswith("over"):
            add("O15", "WC", p/100)
        elif wou.lower().startswith("under"):
            add("U35", "WC", p/100)
    if preds.get("wbtts") == "Yes" and preds.get("pbtts"):
        add("BTTS", "WC", preds["pbtts"]/100)

    if fb:
        dc = fb.get("dc")
        if dc:
            add("DC1X" if dc["market"] == "1X" else "DCX2", "FB", dc["prob"]/100)
        elif fb.get("p1") is not None:
            tot = fb.get("p1", 0) + fb.get("px", 0) + fb.get("p2", 0)
            if tot:
                add("DC1X", "FB", (fb["p1"] + fb["px"])/tot)
                add("DCX2", "FB", (fb["px"] + fb["p2"])/tot)
        if fb.get("p2_win") and not dc:
            add("DCX2", "FB", fb["p2_win"]/100 + 0.20)
        if fb.get("cs"):
            h, a = fb["cs"].split("-")
            g = int(h) + int(a)
            if g >= 2: add("O15", "FB", over_prob(max(g, 1), 2))
            if g <= 3: add("U35", "FB", pois_cdf(3, max(g, 1)))
        btts = fb.get("btts_yes")
        if btts is None and fb.get("cs"):
            h, a = fb["cs"].split("-")
            btts = 70 if int(h) > 0 and int(a) > 0 else 25
        if btts and btts >= 55:
            add("BTTS", "FB", btts/100)

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

    if fpd:
        gh, ga = fpd["cs"]
        g = gh + ga
        if gh > ga: add("DC1X", "FP", 0.80, "1")
        elif ga > gh: add("DCX2", "FP", 0.80, "2")
        else:
            add("DC1X", "FP", 0.75, "X"); add("DCX2", "FP", 0.75, "X")
        if g >= 2: add("O15", "FP", over_prob(max(g, 1), 2))
        if g <= 3: add("U35", "FP", pois_cdf(3, max(g, 1)))
        if gh > 0 and ga > 0: add("BTTS", "FP", 0.65)

    return votes, stance, ctx

# ---------------- JUMLA + DASHBOARD ----------------
def esc(x):
    return H.escape(str(x)) if x is not None else ""

def src_badges(rec):
    return " ".join(f'<span class="sb">{esc(SRC_NAMES[s])}</span>' for s in rec["src"])

def bar(p, w=110):
    pct = int(round(p*100))
    color = "#22c55e" if pct >= 80 else ("#eab308" if pct >= 70 else "#f97316")
    return f'<div class="bar" style="width:{w}px"><div class="fill" style="width:{pct}%;background:{color}"></div></div><b>{pct}%</b>'

def form_text(ctx):
    if not ctx:
        return "<span class='sub'>data ya historia: hakuna</span>"
    def pts(side):
        fp = form_pts(ctx["form"][side])
        return f"{fp}/15" if fp is not None else "—"
    h2h = ctx.get("h2h", "—")
    if len(h2h) > 60:
        h2h = h2h[:57] + "…"
    return (f'<div class="sub">Form H {pts("home")} · A {pts("away")}</div>'
            f'<div class="sub">H2H: {esc(h2h)}</div>')

def analyze(raw, wc, fp, hist):
    picks, near, failed = [], [], []
    for m in raw["matches"]:
        mid = m["id"]
        votes, stance, ctx = votes_for(mid, m, wc, fp, hist)
        tm = to_tz(m["date_gmt"], m["time_gmt"])
        label = f"{m['home']} – {m['away']}"
        for mk, info in MARKET_INFO.items():
            vs = votes[mk]
            if not vs:
                continue
            probs = list(vs.values())
            n = len(probs)
            avg = sum(probs)/n
            # historia
            if ctx:
                if mk == "DC1X": hp = history_dc("home", ctx)
                elif mk == "DCX2": hp = history_dc("away", ctx)
                elif mk == "O15": hp = history_goals(ctx)[0]
                elif mk == "U35": hp = history_goals(ctx)[1]
                else: hp = history_btts(ctx)
            else:
                hp = None
            final = (0.65*avg + 0.35*hp) if hp is not None else avg
            if hp is None:
                flag, flag_cls = "–", "flag-n"
            elif hp >= avg - 0.05:
                flag, flag_cls = "✅ inathibitisha", "flag-ok"
            elif hp >= avg - 0.15:
                flag, flag_cls = "≈ sawa", "flag-mid"
            else:
                flag, flag_cls = f"⚠️ inapingana ({int(hp*100)}%)", "flag-warn"
            dis = [SRC_NAMES[s] for s, d in stance[mk].items()
                   if mk in ("DC1X", "DCX2") and d == ("2" if mk == "DC1X" else "1")]
            sel = {
                "DC1X": f"{m['home']} au draw",
                "DCX2": f"{m['away']} au draw",
                "O15": "Magoli 2+ kwenye mechi",
                "U35": "Magoli 3- pekee",
                "BTTS": "Timu zote mbili zitaungua",
            }[mk]
            rec = {
                "mid": mid, "match": label, "comp": m["comp"], "time": tm,
                "market": info, "selection": sel, "type": mk, "n": n,
                "avg": avg, "hist": hp, "final": final,
                "odds": round(1/final, 2),
                "src": [s for s in SRC_ORDER if s in vs],
                "dis": dis, "ctx": ctx, "flag": flag, "flag_cls": flag_cls,
                "lo": min(probs), "hi": max(probs),
            }
            if n >= 4 and final >= 0.70:
                picks.append(rec)
            elif n >= 3 and final >= 0.72:
                near.append(rec)
            elif n >= 4 and final < 0.70:
                failed.append(rec)
    picks.sort(key=lambda x: -x["final"])
    near.sort(key=lambda x: -x["final"])
    return picks, near, failed

def render(raw, picks, near, failed):
    n_matches = len(raw["matches"])
    rows = []
    for i, p in enumerate(picks, 1):
        dis = f'<span class="dis">⚠ {esc(", ".join(p["dis"]))}</span>' if p["dis"] else '<span class="okc">✓ hakuna</span>'
        rows.append(f"""<tr>
          <td class="rk">{i}</td>
          <td>{esc(p['match'])}<div class="sub">{esc(p['comp'])} · {esc(p['time'])} TZ</div></td>
          <td class="mkt">{esc(p['market'])}<div class="sub">{esc(p['selection'])}</div></td>
          <td class="odds">{p['odds']:.2f}<div class="sub">makadirio</div></td>
          <td>{bar(p['final'])}<div class="sub">sources {int(p['avg']*100)}%{(' · historia ' + str(int(p['hist']*100)) + '%') if p['hist'] else ''}</div></td>
          <td>{src_badges(p)}<div class="sub">{p['n']} vyanzo</div></td>
          <td>{form_text(p['ctx'])}<div class="sub">{esc((p['ctx'] or {}).get('context', '')[:110]) if p['ctx'] else ''}</div></td>
          <td><span class="{p['flag_cls']}">{p['flag']}</span></td>
        </tr>""")
    near_txt = " · ".join(
        f"{p['match']}: {p['market']} ({p['n']} vyanzo, {int(p['final']*100)}%)" for p in near) or "—"
    failed_txt = " · ".join(
        f"{p['match']}: {p['market']} ({int(p['final']*100)}% — historia {int(p['hist']*100) if p['hist'] is not None else '–'}%)"
        for p in failed) or ""
    prod, legs, used = 1.0, 0, set()
    for p in picks:
        if p["mid"] in used:
            continue
        if prod * p["odds"] <= 3.0:
            prod *= p["odds"]; legs += 1; used.add(p["mid"])
    stack = f"Ikipakia picks {legs} kuu (leg 1 kwa mechi): total odds ≈ <b>{prod:.2f}</b>" if legs else ""

    return f"""<!DOCTYPE html>
<html lang="sw"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PICK RADAR v3 — Best Picks (70%+) + Historia</title>
<style>
:root {{ --bg:#0b1020; --card:#131a2e; --line:#232c47; --tx:#e8ecf6; --sub:#8b95b2; --acc:#4f8cff; --grn:#22c55e; --yel:#eab308; --org:#f97316; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--tx); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif; padding:24px 14px 60px; }}
.wrap {{ max-width:1180px; margin:0 auto; }}
header {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:12px; }}
h1 {{ font-size:26px; letter-spacing:.5px; }}
h1 .v3 {{ color:var(--acc); }}
.dateline {{ color:var(--sub); font-size:13px; }}
.badges {{ margin:10px 0 18px; display:flex; gap:8px; flex-wrap:wrap; }}
.badge {{ background:var(--card); border:1px solid var(--line); border-radius:999px; padding:5px 12px; font-size:12px; color:var(--sub); }}
.badge b {{ color:var(--tx); }}
.rule {{ background:#101b33; border:1px solid #27406e; border-left:4px solid var(--acc); border-radius:10px; padding:12px 14px; font-size:13px; color:#c9d6f2; margin-bottom:18px; line-height:1.6; }}
.rule b {{ color:#fff; }}
h2 {{ font-size:17px; margin:8px 0 10px; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; font-size:13px; }}
th {{ text-align:left; color:var(--sub); font-size:10.5px; text-transform:uppercase; letter-spacing:.6px; padding:10px; border-bottom:1px solid var(--line); background:#0f1526; }}
td {{ padding:12px 10px; border-bottom:1px solid #1b2440; vertical-align:top; }}
tr:last-child td {{ border-bottom:none; }}
.rk {{ font-size:18px; font-weight:800; color:var(--acc); }}
.sub {{ color:var(--sub); font-size:11px; margin-top:3px; line-height:1.5; }}
.mkt {{ font-weight:600; }}
.odds {{ font-weight:800; font-size:17px; color:var(--acc); white-space:nowrap; }}
.bar {{ height:8px; background:#1c2542; border-radius:5px; overflow:hidden; display:inline-block; vertical-align:middle; }}
.fill {{ height:100%; border-radius:5px; }}
td b {{ font-size:14px; }}
.sb {{ display:inline-block; background:#1a2540; border:1px solid #2c3a63; color:#c9d6f2; border-radius:7px; padding:2px 7px; font-size:10.5px; margin:0 3px 4px 0; }}
.dis {{ color:var(--org); font-size:11.5px; }}
.okc {{ color:var(--grn); font-size:11.5px; }}
.flag-ok {{ color:var(--grn); font-size:11.5px; white-space:nowrap; }}
.flag-mid {{ color:var(--yel); font-size:11.5px; white-space:nowrap; }}
.flag-warn {{ color:var(--org); font-size:11.5px; }}
.flag-n {{ color:var(--sub); font-size:11.5px; }}
.stack {{ margin-top:14px; background:linear-gradient(180deg,#14203c,#101729); border:1px dashed #2c3a63; border-radius:12px; padding:12px 16px; font-size:13.5px; color:#c9d6f2; }}
.near {{ margin-top:18px; color:var(--sub); font-size:12.5px; line-height:1.9; }}
.near b {{ color:var(--tx); }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin-top:20px; }}
summary {{ cursor:pointer; font-weight:600; font-size:14px; }}
details ol {{ margin:10px 0 4px 20px; color:var(--sub); font-size:13px; line-height:1.75; }}
.warn {{ background:#2a1d12; border:1px solid #6b4a1f; border-radius:12px; padding:14px 16px; font-size:13px; color:#f5d9a8; line-height:1.7; margin-top:24px; }}
.warn b {{ color:#ffd98a; }}
footer {{ margin-top:28px; color:#59647f; font-size:11.5px; line-height:1.7; }}
</style></head><body><div class="wrap">

<header>
  <h1>⚽ PICK <span class="v3">RADAR v3</span></h1>
  <div class="dateline">Best Picks (70%+) · Form + H2H + Quality · Mechi za 25–26 Ag 2026 · Saa za Tanzania (GMT+3)</div>
</header>

<div class="badges">
  <span class="badge">📡 Vyanzo: <b>Soko la Bookmakers (30+) · WinComparator · Forebet · Whispers · FootballPredictions</b></span>
  <span class="badge">🧠 + Historia: <b>Form ya sasa · H2H · Quality/ukubwa wa timu · Leg 1</b></span>
  <span class="badge">🔍 Mechi: <b>{n_matches}</b> · 🏆 Picks: <b>{len(picks)}</b></span>
</div>

<div class="rule">📋 <b>Kiwango:</b> pick inaingia ikiwa <b>vyanzo 4+</b> vimekubaliana NA <b>uhakika wa mwisho ≥ 70%</b>. Uhakika wa mwisho = <b>65% vyanzo + 35% historia</b> (form, head-to-head, ukubwa wa timu, matokeo ya leg 1). <b>⚠️ flag</b> = historia inapingana na sources kwa >15% — hii inaweza kumvuta pick chini ya kiwango.</div>

<h2>🏆 BEST PICKS — UHAKIKA WA MWISHO 70%+</h2>
<table>
  <tr><th>#</th><th>Mechi</th><th>Pick</th><th>Odds</th><th>Uhakika (mwisho)</th><th>Vyanzo</th><th>Form · H2H · Context</th><th>Historia</th></tr>
  {''.join(rows)}
</table>

<div class="stack">🧮 {stack} <span class="sub">(info tu)</span></div>

<div class="near"><b>Karibu kuingia (3 vyanzo, sio 4+):</b> {near_txt}<br>
<b>⛔ Zilizokataliwa na historia (vyanzo 4+ lakini &lt;70% baada ya history blend):</b> {failed_txt if failed_txt else "—"}</div>

<details open><summary>🧠 Jinsi Historia Inavyohesabiwa (v3)</summary>
<ol>
  <li><b>Form ya sasa:</b> mechi 5 za mwisho za kila timu (W=3, D=1, L=0) → 0–15 points → probability ya 42%–95%.</li>
  <li><b>Head-to-Head:</b> rekodi ya ushindani wa timu hizi mbili (mifano: Real Madrid imeshinda H2H 7/8 za mwisho → +nguvu; Tottenham-Charlton H2H ni ya 2011 → si thamani).</li>
  <li><b>Ukubwa/quality ya timu:</b> tiera (PL/La Liga = 1, ligu kuu za Ulaya = 2, Championship = 3...). Katika cup ties, quality gap inapewa uzito mkubwa (40%).</li>
  <li><b>Goals engine:</b> kwa Over/Under — expected goals kutoka kwa averaging ya goals za timu hizo za karibini (m.f. Brentford 4.7 goals/mochezo wa karibini → Over 1.5 ina nguvu kubwa).</li>
  <li><b>Leg 1 (mechi za two-leg):</b> matokeo ya mechi ya kwanza + context (m.f. Fener inapaswa kushinda → itaangamia, itaachia nafasi).</li>
  <li><b>Jumla:</b> historia = weighted blend ya haya (35% ya uhakika wa mwisho). Ikiwa historia <i>inapingana</i> na sources kwa >15%, flag ⚠️ inawekwa.</li>
</ol></details>

<div class="warn">⚠️ <b>Kumbuka:</b> "70%+" inamaanisha <b>sauti ya utafiti</b> (vyanzo 5 + historia) — si kauli yako yako ya 100%. Hata pick ya 85% inaweza kushindwa; ndiyo maana ya ⚠️ flags. <b>Ucheze kwa uhakika.</b></div>

<footer>
  Data: forebet.com · footballwhispers.com · wincomparator.com · footballpredictions.com · bookmakers 30+ (aggregated) · context: Whispers key stats, Forebet match pages, tips.gg/totalfootballanalysis (1st legs). Imekusanywa 25 Ag 2026. Odds ni makadirio (1/uhakika wa mwisho).<br>
  PICK RADAR v3 · utafiti binafsi · si ushauri wa kifedha.
</footer>

</div></body></html>"""

def main():
    raw, wc, fp, hist = load()
    picks, near, failed = analyze(raw, wc, fp, hist)
    html = render(raw, picks, near, failed)
    open(os.path.join(BASE, "dashboard.html"), "w", encoding="utf-8").write(html)
    print(f"✅ dashboard.html (v3)")
    print(f"\n🏆 PICKS ({len(picks)}):")
    for p in picks:
        hs = f"{int(p['hist']*100)}%" if p['hist'] is not None else "–"
        print(f"  [{int(p['final']*100):>3}%] {p['match']:38s} {p['market']:30s} "
              f"@{p['odds']:<5} sources={int(p['avg']*100)}% hist={hs} n={p['n']} {p['flag']}")
    print(f"\nKaribu ({len(near)}):")
    for p in near:
        hs = f"{int(p['hist']*100)}%" if p['hist'] is not None else "–"
        print(f"  [{int(p['final']*100):>3}%] {p['match']:38s} {p['market']:30s} n={p['n']} hist={hs} {p['flag']}")

if __name__ == "__main__":
    main()
