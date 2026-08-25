#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PICK RADAR — Utafiti & Uchambuzi wa Sports Betting
Pipeline: raw.json (data kutoka sources) -> consensus -> market rahisi -> confidence -> accumulator -> dashboard.html

Mantiki:
  1. CONSENSUS: mechi ambapo vyanzo 2+ vinakubaliana kwa market moja.
  2. CONVERSION (market rahisi zaidi):
     - win consensus      -> Double Chance (1X / X2)
     - over 2.5/3.5       -> Over 1.5   (odds kutoka Poisson)
     - under 2.5          -> Under 3.5  (odds kutoka Poisson)
     - BTTS consensus     -> BTTS (ina safety yake, tunatumia odds halisi)
  3. CONFIDENCE SCORE (0-100): idadi ya vyanzo + uwezekano wa makadirio.
  4. ACCUMULATOR: legs za consensus zimechaguliwa kwa greedy kwamba TOTAL ODDS iwe 1.5-3.0.
"""
import json, math, os, html as H
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
TZ_OFFSET_H = 3  # GMT+3 Tanzania

# ---------------- Poisson helpers ----------------
def pois_cdf(k, lam):
    return sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k + 1))

def over_prob(lam, k):
    return 1 - pois_cdf(k - 1, lam)

def lam_from_over(k, p):
    """λ ambayo P(X>=k)=p"""
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

def est_odds(prob, margin=0.0):
    prob = min(max(prob, 0.05), 0.97)
    return round(1.0 / (prob * (1 + margin)), 2)

def draw_prob_default():
    return 0.26  # wastani wa mechi za mchezo

# ---------------- TZ time ----------------
def to_tz(date_gmt, time_gmt):
    dt = datetime.strptime(date_gmt + " " + time_gmt, "%Y-%m-%d %H:%M") + timedelta(hours=TZ_OFFSET_H)
    return dt.strftime("%d/%m") + " " + dt.strftime("%H:%M")

# ---------------- Consensus + conversion ----------------
def analyze(matches):
    picks = []   # consensus picks
    singles = [] # picks za chanzo kimoja (maelezo)

    for m in matches:
        fw, fb = m.get("fw"), m.get("forebet")
        if not fw and not fb:
            continue
        label = f"{m['home']} – {m['away']}"
        n_src = (1 if fw else 0) + (1 if fb else 0)

        # --- 1X2 result signals ---
        res_fb = fb.get("pred") if fb else None
        res_fw = None
        if fw:
            for t in fw.get("tips", []):
                if t["market"] == "result":
                    res_fw = t["selection"]
            if res_fw is None and fw.get("cs"):
                h, a = fw["cs"].split("-")
                res_fw = "1" if int(h) > int(a) else ("2" if int(a) > int(h) else "X")
        cs_goals_fb = None
        if fb and fb.get("cs"):
            cs_goals_fb = sum(int(x) for x in fb["cs"].split("-"))
        cs_goals_fw = None
        if fw and fw.get("cs"):
            cs_goals_fw = sum(int(x) for x in fw["cs"].split("-"))

        # --- WIN consensus -> Double Chance ---
        if res_fb in ("1", "2") and res_fw == res_fb:
            win_side = res_fb
            dc = fb.get("dc") if fb else None
            if dc and dc["market"] in ("1X", "X2") and (("1" in dc["market"]) == (win_side == "1")):
                prob = dc["prob"] / 100.0
                src_note = f"Forebet DC {dc['prob']}%" + (" (makadirio)" if dc.get("est") else "")
            elif fb and fb.get("p1") is not None:
                prob = (fb.get("p1", 0) + fb.get("px", 0)) / 100.0 if win_side == "1" else (fb.get("px", 0) + fb.get("p2", 0)) / 100.0
                src_note = "Forebet prob 1X2 + Whispers tip"
            else:
                prob = (fb.get("p2_win", 50) if win_side == "2" else 45) / 100.0 + draw_prob_default() * 0.5
                src_note = "Whispers tip + Forebet"
            mkt = "1X" if win_side == "1" else "X2"
            winner = m["home"] if win_side == "1" else m["away"]
            picks.append({
                "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                "market": f"Double Chance {mkt}", "selection": f"{winner} au draw",
                "odds": est_odds(prob), "prob": round(prob, 3), "n": 2,
                "src": src_note, "real_odds": False, "id": m["id"],
                "why": f"Vyanzo vyote 2 vinachagua {winner} — DC inaongeza draw kama msalaba."
            })
        elif res_fw in ("1", "2") and res_fw != res_fb and n_src == 1:
            t = [t for t in fw["tips"] if t["market"] == "result"][0]
            winner = m["home"] if res_fw == "1" else m["away"]
            singles.append({
                "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                "market": "Matokeo (Whispers tu)", "selection": winner,
                "odds": t["odds"], "prob": round(1 / t["odds"], 3), "n": 1,
                "src": f"Whispers ({t['lik']})", "real_odds": True, "id": m["id"] + "-res"
            })

        # --- O/U consensus ---
        over_fb = cs_goals_fb is not None and cs_goals_fb >= 3   # CS ya FB ina goals 3+ = over 2.5
        under_fb = cs_goals_fb is not None and cs_goals_fb <= 1  # CS ya FB ina goals 1- = under 2.5
        fw_tips = fw.get("tips", []) if fw else []
        fw_over = [t for t in fw_tips if t["market"] == "over"]
        fw_under = [t for t in fw_tips if t["market"] == "under"]
        fw_over_cs = cs_goals_fw is not None and cs_goals_fw >= 3

        if (fw_over or fw_over_cs) and over_fb:
            best = min([t["odds"] for t in fw_over] or [2.5])
            k = min([t["line"] for t in fw_over] or [2.5])
            lam = lam_from_over(math.ceil(k), 1 / best)
            p15 = over_prob(lam, 2)
            picks.append({
                "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                "market": "Over 1.5 goals (kutoka Over 2.5/3.5)", "selection": "Magoli 2+ kwenye mechi",
                "odds": est_odds(p15), "prob": round(p15, 3), "n": 2,
                "src": f"Whispers O{k} @{best} + Forebet CS {fb['cs']}", "real_odds": False, "id": m["id"],
                "why": "Vyanzo vyote 2 vinaangalia goals nyingi — Over 1.5 ndio toleo la kasi (sahihi zaidi)."
            })
        elif (fw_under or (cs_goals_fw is not None and cs_goals_fw <= 1)) and (under_fb or (res_fb == "X" and fb.get("cs"))):
            if fw_under:
                best = min(t["odds"] for t in fw_under)
                line = min(t["line"] for t in fw_under)
                lam = lam_from_over(math.ceil(line) + 1, 1 / best)  # P(U line)=p -> P(X <= line-... ) use cdf
                # P(X <= line-1 ... ) — kwa U2.5: P(X<=2)=p -> λ; then P(U3.5)=P(X<=3)
                lam = _lam_from_under(line, 1 / best)
                p35 = pois_cdf(3, lam)
                picks.append({
                    "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                    "market": "Under 3.5 goals (kutoka Under 2.5)", "selection": "Magoli 3- pekee",
                    "odds": est_odds(p35), "prob": round(p35, 3), "n": 2,
                    "src": f"Whispers U{line} @{best} + Forebet (CS {fb.get('cs','—')} / pred X)", "real_odds": False, "id": m["id"],
                    "why": "Vyanzo vyote 2 vinaangalia mechi ya goals chache — Under 3.5 ni msalaba salama."
                })

        # --- BTTS consensus ---
        btts_fb = fb.get("btts_yes") if fb else None
        if fb and btts_fb is None and fb.get("cs"):
            h, a = fb["cs"].split("-")
            btts_fb = 70 if (int(h) > 0 and int(a) > 0) else 25
        fw_btts = [t for t in fw_tips if t["market"] == "btts" and t["selection"] == "yes"]
        if fw_btts and (btts_fb or 0) >= 50:
            t = fw_btts[0]
            picks.append({
                "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                "market": "BTTS – Yes", "selection": "Timu zote mbili zitaungua",
                "odds": t["odds"], "prob": round(1 / t["odds"], 3), "n": 2,
                "src": f"Whispers @{t['odds']} ({t['lik']}) + Forebet (BTTS {btts_fb}% / CS {fb.get('cs','—')})",
                "real_odds": True, "id": m["id"],
                "why": "Vyanzo vyote 2 vinaangalia timu zote zitaungua — BTTS na uwezekano wa juu."
            })

        # --- DC kutoka FB DC + FW win ( Rapid case: FB X + FW win => DC 1X ) ---
        if not any(p["id"] == m["id"] and p["market"].startswith("Double") for p in picks):
            dc = fb.get("dc") if fb else None
            if dc and dc["prob"] >= 70:
                side_win = "1" if "1" in dc["market"] else "2"
                if res_fw == side_win:
                    prob = dc["prob"] / 100.0
                    winner = m["home"] if side_win == "1" else m["away"]
                    picks.append({
                        "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                        "market": f"Double Chance {dc['market']}", "selection": f"{winner} au draw",
                        "odds": est_odds(prob), "prob": round(prob, 3), "n": 2,
                        "src": f"Forebet DC {dc['prob']}% + Whispers tip @{[t for t in fw['tips'] if t['market']=='result'][0]['odds']}",
                        "real_odds": False, "id": m["id"],
                        "why": "Forebet inaangalia " + ("draw au" if fb.get("pred") == "X" else "") + f" {winner} kuwa na nguvu — Whispers inakubali. DC inafunga msalaba."
                    })

        # --- Singles (chanzo kimoja) ---
        if fw:
            for t in fw.get("tips", []):
                if t["market"] == "under" and not any(p["id"] == m["id"] and "Under" in p["market"] for p in picks):
                    lam = _lam_from_under(t["line"], 1 / t["odds"])
                    p35 = pois_cdf(3, lam)
                    singles.append({
                        "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                        "market": f"Under 3.5 (kutoka U{t['line']})", "selection": "Magoli 3- pekee",
                        "odds": est_odds(p35), "prob": round(p35, 3), "n": 1,
                        "src": f"Whispers tu (U{t['line']} @{t['odds']}, {t['lik']})", "real_odds": False,
                        "id": m["id"] + "-u35"
                    })
            if fw.get("cs") and n_src == 1:
                h, a = fw["cs"].split("-")
                singles.append({
                    "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                    "market": "Correct score (maelezo)", "selection": fw["cs"],
                    "odds": None, "prob": 0.1, "n": 1,
                    "src": "Whispers tu", "real_odds": False, "id": m["id"] + "-cs"
                })
        if fb and not fw:
            if fb.get("cs"):
                h, a = fb["cs"].split("-")
                goals = int(h) + int(a)
                singles.append({
                    "match": label, "comp": m["comp"], "time": to_tz(m["date_gmt"], m["time_gmt"]),
                    "market": f"Under 3.5 (Forebet CS {fb['cs']})", "selection": "Magoli 3- pekee",
                    "odds": 1.30, "prob": 0.76, "n": 1,
                    "src": "Forebet tu (pred X, CS %s)" % fb["cs"], "real_odds": False, "id": m["id"] + "-u35"
                })

    # --- Confidence scores ---
    for p in picks:
        p["score"] = min(96, round((p["n"] / 3) * 38 + p["prob"] * 52 + (6 if p["real_odds"] else 0)))
    for s in singles:
        s["score"] = min(60, round((s["n"] / 3) * 38 + s["prob"] * 40))
    picks.sort(key=lambda x: -x["score"])
    singles.sort(key=lambda x: -x["score"])
    return picks, singles

def _lam_from_under(line, p_under):
    """λ ambayo P(X <= floor(line-1)) = p, m.f. U2.5 -> P(X<=2)=p"""
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

# ---------------- Accumulator ----------------
def build_combos(picks):
    legs = [p for p in picks if p["odds"]]
    legs.sort(key=lambda x: -x["score"])

    # Combo A: greedy kwa confidence, total 1.5-3.0, leg 1 pekee kwa mechi
    combo_a, prod, used = [], 1.0, set()
    for leg in legs:
        if leg["id"] in used:
            continue
        if prod * leg["odds"] <= 3.0 and len(combo_a) < 8:
            combo_a.append(leg)
            prod *= leg["odds"]
            used.add(leg["id"])
    # hakikisha >= 1.5 (ikihitajika, ongeza leg inayobaki ndani ya 3.0)
    for leg in legs:
        if leg["id"] in used:
            continue
        if prod < 1.5 and prod * leg["odds"] <= 3.0:
            combo_a.append(leg)
            prod *= leg["odds"]
            used.add(leg["id"])
    combo_a_odds = round(prod, 2)
    combo_a_hit = round(math.prod(l["prob"] for l in combo_a) * 100, 1)

    # Combo B: thamani — legs zenye odds halisi >= 1.5, mpaka 2, total <= 3.0
    val = [p for p in picks if p["real_odds"] and p["odds"] >= 1.5]
    val.sort(key=lambda x: -x["score"])
    combo_b, prod, used_b = [], 1.0, set()
    for leg in val:
        if leg["id"] in used_b or len(combo_b) >= 2:
            continue
        if prod * leg["odds"] <= 3.0:
            combo_b.append(leg)
            prod *= leg["odds"]
            used_b.add(leg["id"])
    combo_b_odds = round(prod, 2) if combo_b else None
    combo_b_hit = round(math.prod(l["prob"] for l in combo_b) * 100, 1) if combo_b else None

    return combo_a, combo_a_odds, combo_a_hit, combo_b, combo_b_odds, combo_b_hit

# ---------------- Dashboard ----------------
def esc(x):
    return H.escape(str(x)) if x is not None else ""

def score_bar(score, cls=None):
    color = "#22c55e" if score >= 70 else ("#eab308" if score >= 55 else "#f97316")
    return (f'<div class="bar"><div class="barfill" style="width:{score}%;background:{color}"></div></div>'
            f'<span class="score">{score}</span>')

def render_dashboard(data, picks, singles, combo_a, ca_odds, ca_hit, combo_b, cb_odds, cb_hit):
    meta = data["meta"]
    n_matches = len(data["matches"])
    sources = " • ".join(meta["sources"])

    def leg_rows(combo):
        out = []
        for l in combo:
            out.append(f"""<tr>
              <td>{esc(l['match'])}<div class="sub">{esc(l['comp'])} · {esc(l['time'])} TZ</div></td>
              <td class="mkt">{esc(l['market'])}<div class="sub">{esc(l['selection'])}</div></td>
              <td class="odds">{l['odds']:.2f}</td>
              <td>{int(round(l['prob']*100))}%</td>
              <td>{score_bar(l['score'])}</td>
            </tr>""")
        return "\n".join(out)

    def pick_rows(items):
        out = []
        for p in items:
            odds = f"{p['odds']:.2f}" if p["odds"] else "—"
            out.append(f"""<tr>
              <td>{esc(p['match'])}<div class="sub">{esc(p['comp'])} · {esc(p['time'])} TZ</div></td>
              <td class="mkt">{esc(p['market'])}<div class="sub">{esc(p['selection'])}</div></td>
              <td class="odds">{odds}</td>
              <td>{int(round(p['prob']*100))}%</td>
              <td><span class="src">{p['n']}/2 vyanzo</span></td>
              <td>{score_bar(p['score'])}</td>
            </tr>""")
        return "\n".join(out)

    combo_b_html = ""
    if combo_b:
        combo_b_html = f"""
    <div class="combo">
      <div class="combohead"><span class="tag tag2">COMBO B · THAMANI</span><span class="csub">2 legs, odds za juu zaidi (BTTS)</span></div>
      <table class="tbl">{leg_rows(combo_b)}</table>
      <div class="totalrow"><div><span class="tlabel">TOTAL ODDS</span><span class="todds">{cb_odds:.2f}</span></div>
      <div><span class="tlabel">UWEZEKANO WA KUKITA (makadirio)</span><span class="thit">{cb_hit}%</span></div>
      <div><span class="tlabel">STAKE 10,000 TZS</span><span class="tpay">≈ {int(10000*cb_odds):,} TZS</span></div></div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="sw"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PICK RADAR — 25–26 Agosti 2026</title>
<style>
:root {{ --bg:#0b1020; --card:#131a2e; --card2:#0f1526; --line:#232c47; --tx:#e8ecf6; --sub:#8b95b2; --acc:#4f8cff; --grn:#22c55e; --yel:#eab308; --org:#f97316; --red:#ef4444; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--tx); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif; padding:24px 16px 60px; }}
.wrap {{ max-width:1000px; margin:0 auto; }}
header {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:12px; margin-bottom:6px; }}
h1 {{ font-size:26px; letter-spacing:.5px; }}
h1 .radar {{ color:var(--acc); }}
.dateline {{ color:var(--sub); font-size:13px; }}
.badges {{ margin:10px 0 22px; display:flex; gap:8px; flex-wrap:wrap; }}
.badge {{ background:var(--card); border:1px solid var(--line); border-radius:999px; padding:5px 12px; font-size:12px; color:var(--sub); }}
.badge b {{ color:var(--tx); }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:22px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; }}
.kpi .v {{ font-size:22px; font-weight:700; }}
.kpi .l {{ font-size:11px; color:var(--sub); text-transform:uppercase; letter-spacing:.8px; margin-top:4px; }}
.combo {{ background:linear-gradient(180deg,#141d36,#101729); border:1px solid #2c3a63; border-radius:16px; padding:16px; margin-bottom:16px; }}
.combohead {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap:wrap; }}
.tag {{ background:var(--acc); color:#fff; font-size:12px; font-weight:700; padding:4px 10px; border-radius:8px; }}
.tag2 {{ background:var(--org); }}
.csub {{ color:var(--sub); font-size:12px; }}
table.tbl {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
table.tbl th {{ text-align:left; color:var(--sub); font-size:11px; text-transform:uppercase; letter-spacing:.7px; padding:8px 8px; border-bottom:1px solid var(--line); }}
table.tbl td {{ padding:10px 8px; border-bottom:1px solid #1b2440; vertical-align:top; }}
tr:last-child td {{ border-bottom:none; }}
.sub {{ color:var(--sub); font-size:11.5px; margin-top:3px; }}
.mkt {{ font-weight:600; }}
.odds {{ font-weight:700; color:var(--acc); font-size:15px; white-space:nowrap; }}
.totalrow {{ display:flex; gap:26px; flex-wrap:wrap; margin-top:12px; padding-top:12px; border-top:1px dashed #2c3a63; }}
.totalrow .tlabel {{ display:block; font-size:10.5px; color:var(--sub); text-transform:uppercase; letter-spacing:.8px; }}
.todds {{ font-size:24px; font-weight:800; color:var(--grn); }}
.thit {{ font-size:20px; font-weight:700; color:var(--yel); }}
.tpay {{ font-size:16px; font-weight:700; color:var(--tx); }}
h2 {{ font-size:17px; margin:26px 0 10px; letter-spacing:.3px; }}
h2 .pill {{ font-size:11px; background:var(--card); border:1px solid var(--line); color:var(--sub); border-radius:999px; padding:3px 10px; margin-left:8px; font-weight:400; }}
.bar {{ width:90px; height:7px; background:#1c2542; border-radius:5px; overflow:hidden; display:inline-block; vertical-align:middle; }}
.barfill {{ height:100%; border-radius:5px; }}
.score {{ font-size:12px; font-weight:700; margin-left:8px; }}
.src {{ font-size:12px; color:var(--sub); white-space:nowrap; }}
.why {{ color:var(--sub); font-size:12px; }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin:10px 0; }}
summary {{ cursor:pointer; font-weight:600; font-size:14px; }}
details ol {{ margin:10px 0 4px 20px; color:var(--sub); font-size:13px; line-height:1.75; }}
.warn {{ background:#2a1d12; border:1px solid #6b4a1f; border-radius:12px; padding:14px 16px; font-size:13px; color:#f5d9a8; line-height:1.7; margin-top:26px; }}
.warn b {{ color:#ffd98a; }}
footer {{ margin-top:30px; color:#59647f; font-size:11.5px; line-height:1.7; }}
.dim td {{ opacity:.72; }}
</style></head><body><div class="wrap">

<header>
  <h1>⚽ PICK <span class="radar">RADAR</span></h1>
  <div class="dateline">Utafiti &amp; Uchambuzi wa Mchezo · Mechi za 25–26 Ag 2026 · Saa za Tanzania (GMT+3)</div>
</header>

<div class="badges">
  <span class="badge">📡 Vyanzo: <b>{sources}</b> (2/3 — BetMine haikufanya kazi leo)</span>
  <span class="badge">🔍 Mechi zilizochunguzwa: <b>{n_matches}</b></span>
  <span class="badge">✅ Picks za consensus: <b>{len(picks)}</b></span>
  <span class="badge">🎯 Total odds combo ya imara: <b>{ca_odds:.2f}</b></span>
</div>

<div class="kpis">
  <div class="kpi"><div class="v">{n_matches}</div><div class="l">Mechi zilizochunguzwa</div></div>
  <div class="kpi"><div class="v">{len(picks)}</div><div class="l">Picks zimekubaliana (2+ vyanzo)</div></div>
  <div class="kpi"><div class="v">{len(combo_a)}</div><div class="l">Legs kwenye combo ya imara</div></div>
  <div class="kpi"><div class="v">{ca_odds:.2f}</div><div class="l">Total odds (mpaka 3.0)</div></div>
  <div class="kpi"><div class="v">{ca_hit}%</div><div class="l">Uwezekano wa kukita (makadirio)</div></div>
</div>

<div class="combo">
  <div class="combohead"><span class="tag">COMBO A · IMARA ZIDIA</span><span class="csub">Legs zimechaguliwa kwa confidence score — total odds imehifadhiwa ndani ya 1.5–3.0</span></div>
  <table class="tbl">
    <tr><th>Mechi</th><th>Market (rahisi zaidi)</th><th>Odds</th><th>Uwezekano</th><th>Imani</th></tr>
    {leg_rows(combo_a)}
  </table>
  <div class="totalrow">
    <div><span class="tlabel">TOTAL ODDS</span><span class="todds">{ca_odds:.2f}</span></div>
    <div><span class="tlabel">UWEZEKANO WA KUKITA (makadirio)</span><span class="thit">{ca_hit}%</span></div>
    <div><span class="tlabel">STAKE 10,000 TZS</span><span class="tpay">≈ {int(10000*ca_odds):,} TZS</span></div>
  </div>
</div>
{combo_b_html}

<h2>Picks Zote za Consensus <span class="pill">vyanzo 2+ zimekubaliana</span></h2>
<table class="tbl">
  <tr><th>Mechi</th><th>Market (rahisi zaidi)</th><th>Odds</th><th>Uwezekano</th><th>Vyanzo</th><th>Imani</th></tr>
  {pick_rows(picks)}
</table>

<h2>Picks za Chanzo Kimoja <span class="pill">maelezo tu — si za kwenye combo</span></h2>
<table class="tbl">
  <tr><th>Mechi</th><th>Market</th><th>Odds</th><th>Uwezekano</th><th>Chanzo</th><th>Imani</th></tr>
  {pick_rows(singles)}
</table>

<details open><summary>🧠 Jinsi App Inavyofikiri (Mantiki ya Uchambuzi)</summary>
<ol>
  <li><b>Utafiti:</b> App inapokea predictions za siku kutoka kwa Forebet (probabilities + picks + correct score) na Football Whispers (tips + odds + likelihood: Probable / Likely / Outsider).</li>
  <li><b>Consensus:</b> Mechi inapokea alama ikiwa vyanzo 2+ vinaangalia kitu kimoja (win, over/under, BTTS). Ikiwa source moja inapinga nyingine, market hiyo yafuliwa.</li>
  <li><b>Market rahisi zaidi (conversion):</b> Win → <b>Double Chance</b> (win + draw); Over 2.5/3.5 → <b>Over 1.5</b>; Under 2.5 → <b>Under 3.5</b>. Hii ndiyo inayoongeza uwezekano bila kupoteza mwelekeo wa consensus.</li>
  <li><b>Confidence score (0–100):</b> = (idiani ya vyanzo) + (uwezekano wa makadirio) + bonus ikiwa odds ni halisi kutoka kwenye bookmaker. Score ≥ 70 = imara.</li>
  <li><b>Accumulator:</b> Legs zimepangwa kwa score, zinaongezwa moja kwa moja mpaka total odds ipeleke juu ya <b>3.0</b> (kwa kufuata utaratibu wako: min 1.5, max 3.0).</li>
</ol></details>

<div class="warn">
  ⚠️ <b>Kweli ulichoomba ufahamu:</b> Hakuna app ya dunia inayoweza kugharamia 90% ya matokeo. Total odds {ca_odds:.2f} inamaanisha soko linaona uwezekano wa kukita ≈ <b>{round(100/ca_odds)}%</b> (makadirio yangu: {ca_hit}%). Hata combo iliyo "imara zaidi" inaweza kushindwa — ndani yake kila leg ina risk yake. Hii ni <b>research</b> sahihi zaidi kuliko kushughulikia bila taarifa, si uhakika. <b>Ucheze kwa uhakika — usishughulie zaidi ya unazoweza kupoteza.</b>
</div>

<footer>
  Chanzo cha data: forebet.com · footballwhispers.com (imekusanywa {meta['generated']}). Odds za Double Chance / Over 1.5 / Under 3.5 ni <b>makadirio</b> (model ya Poisson + probabilities za Forebet) — odds halisi za bookmaker zitaishi kidogo chini. App hii haitumii login ya Google yoyote: data zote ni za uhakika.<br>
  PICK RADAR MVP · imeundwa kwa ajili ya utafiti binafsi · si ushauri wa kifedha.
</footer>

</div></body></html>"""

def main():
    raw = json.load(open(os.path.join(BASE, "data", "raw.json"), encoding="utf-8"))
    picks, singles = analyze(raw["matches"])
    ca, cao, cah, cb, cbo, cbh = build_combos(picks)
    html = render_dashboard(raw, picks, singles, ca, cao, cah, cb, cbo, cbh)
    out = os.path.join(BASE, "dashboard.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"✅ dashboard.html imeundwa: {out}")
    print(f"   Consensus picks: {len(picks)} | Singles: {len(singles)}")
    print(f"   Combo A: {len(ca)} legs @ {cao} (hit ≈{cah}%)")
    if cb:
        print(f"   Combo B: {len(cb)} legs @ {cbo} (hit ≈{cbh}%)")
    for p in picks:
        print(f"   [{p['score']:>3}] {p['match']}: {p['market']} @ {p['odds']:.2f} (p={int(p['prob']*100)}%)")

if __name__ == "__main__":
    main()
