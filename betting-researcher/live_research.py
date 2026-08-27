#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_research.py — v6: DAILY research (siku kamili) + multi-source + auto scores.

Mechi zinajua kwa LIVE (si hardcoded) kutoka WinComparator league pages:
ZOTE za siku husika (GMT+3) ambazo bado hazianza, kisha kila mechi
inachunguzwa na vyanzo 5:

  1. Soko (Bookmakers 30+)  — aggregated market odds (1X2, O/U, BTTS)
  2. WinComparator (Model)  — model yake ya maendeleo (p1x2 / pou / pbtts)
  3. FootballPredictions    — correct score predictions (live)
  4. Football Whispers      — betting tips (live + local cache)
  5. Forebet                — local cache (siku ile ile)

Kanuni ya pick (kwa user): pick inafanya kazi IPELE
  - imevotiwa na vyanzo 4+ (n >= 4)
  - na consensus confidence >= 70% (conf = avg + 0.03*(n-1), cap 0.97)

ACCUMULATOR (bet ya siku):
  - legs makuu: picks zenye conf >= 80%
  - ikiwa total < 1.6: jumlisha picks 70–80% hadi >= 1.6
  - total odds lazima iwe 1.6 – 3.0, hadi legs 10
  - inakiuka 1.6 → flag min_met=false (UI itaonyesha onyo)

AUTO SCORES: wc_result(slug) — final score kutoka page ya mechi ya WC
(hakuna API key — data halisi; hupatikana tu baada ya "End").
"""
import json, os, re, time, datetime, urllib.request, concurrent.futures, html as H
from analyze_v3 import over_prob, lam_from_over, lam_from_under, pois_cdf

BASE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
WC_BASE = "https://www.wincomparator.com/predictions/football"
WC_MATCH_BASE = "https://www.wincomparator.com/predictions"

WC_LEAGUES = [
    ("efl-cup-391", "England EFL Cup"),
    ("europe/champions-league-8", "UEFA Champions League"),
    ("europe/europa-league-481", "UEFA Europa League"),
    ("europe/europa-conference-league-139206", "UEFA Europa Conference League"),
    ("england/premier-league-49", "England Premier League"),
    ("england/championship-50", "England Championship"),
    ("spain/laliga-108", "Spain LaLiga"),
    ("germany/bundesliga-65", "Germany Bundesliga"),
    ("italy/serie-a-79", "Italy Serie A"),
    ("france/ligue-1-123", "France Ligue 1"),
]

MON = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6, "July": 7,
       "August": 8, "September": 9, "October": 10, "November": 11, "December": 12}
MON_ABBR = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

try:
    from zoneinfo import ZoneInfo
    _WC_TZ = ZoneInfo("America/Los_Angeles")  # WC inaonyesha siku za US Pacific
except Exception:
    _WC_TZ = None  # fallback: heuristics ya DST hapa chini


def _wc_month(s):
    s = (s or "").strip()
    if s in MON:
        return MON[s]
    return MON_ABBR.get(s[:3].title())


def _us_dst_active(year, mo, day):
    """US DST: Jumapili ya 2 ya Machi → Jumapili ya 1 ya Novemba."""
    def nth_sunday(y, m, n):
        d = datetime.date(y, m, 1)
        offset = (6 - d.weekday()) % 7
        return d + datetime.timedelta(days=offset + 7 * (n - 1))
    return nth_sunday(year, 3, 2) <= datetime.date(year, mo, day) < nth_sunday(year, 11, 1)

CACHE_DISC = 3600    # mechi list: 1h
CACHE_ODDS = 900     # odds kwa match: 15min
CACHE_FP = 3600      # FP: 1h
CACHE_FW = 900       # FW live: 15min
_CACHE = {"disc": (0, []), "odds": {}, "fp": (0, {}), "fw": (0, []), "fwf": (0, []), "fb": (0, [])}

SOKO = "Soko (Bookmakers 30+)"
WCM = "WinComparator (Model)"
FPS = "FootballPredictions"
FWS = "Football Whispers"
FBS = "Forebet"
ALL_SOURCES = [SOKO, WCM, FPS, FWS, FBS]

N_MIN = 4        # vyanzo vichache vya kupitisha pick
CONF_MIN = 0.70  # confidence vichache (70%)

# ---------------- DAILY MODE (siku kamili, GMT+3 Tanzania) ----------------
DAY_TZ = datetime.timezone(datetime.timedelta(hours=3))
ACC_CONF = 0.80          # legs makuu za accumulator: conf >= 80%
ACC_FALLBACK_CONF = 0.70 # zidisha hadi 1.6: picks 70–80%
ACC_MIN_TOTAL = 1.6      # total odds ya accumulator: chini ya hapa = haina maana
ACC_MAX_TOTAL = 3.0      # juu ya hapa = hatari
ACC_MAX_LEGS = 10

def day_ms_range(now=None):
    """(start_ms, end_ms, 'YYYY-MM-DD') ya siku sasa GMT+3."""
    now = now or datetime.datetime.now(DAY_TZ)
    start = datetime.datetime(now.year, now.month, now.day, tzinfo=DAY_TZ)
    return (int(start.timestamp() * 1000),
            int((start + datetime.timedelta(days=1)).timestamp() * 1000),
            now.strftime("%Y-%m-%d"))


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


# ---------------- team-name matching ----------------
STOP = {"fc", "cf", "afc", "sc", "fk", "bk", "sk", "ks", "kks", "jk", "aif", "if",
        "club", "de", "the", "team", "pfc", "hnk", "nk", "sv", "ac", "ss", "us", "as"}


def _norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    toks = [t for t in s.split() if t not in STOP]
    return " ".join(toks)


def _toks(s):
    return set(t for t in _norm(s).split() if len(t) > 2)


def _teams_match(home, away, post_home, post_away):
    """True ikiwa team names zinalingana (exact/substring/token)."""
    nh, na = _norm(home), _norm(away)
    ph, pa = _norm(post_home), _norm(post_away)
    if not nh or not na:
        return False
    if (nh == ph and na == pa) or (ph in nh and pa in na) or (nh in ph and na in pa):
        return True
    th, ta = _toks(nh), _toks(na)
    phh, pha = _toks(ph), _toks(pa)
    return bool(th & phh) and bool(ta & pha)


# ---------------- WC discovery ----------------
def _parse_wc_league_jsonld(html):
    """schema.org SportsEvent (JSON-LD): slug canonical + startDate UTC + names rasmi.
    Hii ndiyo source ya kweli — display time ya page inaweza kuwa kwa TZ ya viewer."""
    out = []
    seen = set()
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        events = data if isinstance(data, list) else [data]
        for ev in events:
            if not isinstance(ev, dict) or ev.get("@type") != "SportsEvent":
                continue
            um = re.search(r"/predictions/([a-z0-9-]+-\d+)/?$", ev.get("url", ""))
            if not um:
                continue
            slug = um.group(1)
            if slug in seen:
                continue
            try:
                t = datetime.datetime.fromisoformat(ev["startDate"])
                k = int(t.timestamp() * 1000)
            except Exception:
                continue
            home = ((ev.get("homeTeam") or {}).get("name") or "").strip()
            away = ((ev.get("awayTeam") or {}).get("name") or "").strip()
            if not home or not away:
                nm = ev.get("name", "")
                if " vs " in nm:
                    a, b = nm.split(" vs ", 1)
                    home, away = a.strip(), b.strip()
            if not home or not away:
                continue
            seen.add(slug)
            out.append({"month": "Jan", "date": 1, "time": "0:00",
                        "home": home, "away": away, "slug": slug,
                        "kickoff_utc_ms": k})
    return out

def _parse_wc_league_display(html):
    out = []
    idxs = [m.start() for m in re.finditer(r'data-navigation-url-value="(/predictions/[a-z0-9-]+-\d+/)"', html)]
    for i, ix in enumerate(idxs):
        end = idxs[i + 1] if i + 1 < len(idxs) else ix + 4000
        block = html[ix:end]
        sm = re.search(r'(/predictions/([a-z0-9-]+)-(\d+)/)', block)
        if not sm:
            continue
        slug = sm.group(1).strip("/").replace("predictions/", "", 1)
        dm = re.search(r'(\d{1,2}) (\w{3,9}) - (\d{1,2}):(\d{2})', block)
        names = re.findall(r'<span[^>]*>\s*([A-Z][A-Za-z0-9 .\u2019&-]{2,30}?)\s*</span>', block)
        names = [n for n in names if not re.match(r'^\d', n)]
        if dm and len(names) >= 2:
            out.append({"month": dm.group(2), "date": int(dm.group(1)),
                        "time": dm.group(3) + ":" + dm.group(4),
                        "home": names[0].strip(), "away": names[1].strip(), "slug": slug})
    return out

def _parse_wc_league(html):
    out = _parse_wc_league_jsonld(html)
    if out:
        return out
    return _parse_wc_league_display(html)


def _wc_to_utc_ms(m):
    """Saa ya WC = US Pacific local (PDT/PST) → UTC. Inashughulikia DST na mwaka ukizunguka (Dec→Jan)."""
    mo = _wc_month(m["month"])
    if not mo:
        return None
    try:
        hh, mm = map(int, m["time"].split(":"))
    except Exception:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    for year in (now.year, now.year + 1):
        try:
            dt = datetime.datetime(year, mo, m["date"], hh, mm)
        except ValueError:
            continue
        if _WC_TZ is not None:
            utc = dt.replace(tzinfo=_WC_TZ).astimezone(datetime.timezone.utc)
        else:
            off = 7 if _us_dst_active(year, mo, m["date"]) else 8
            utc = (dt + datetime.timedelta(hours=off)).replace(tzinfo=datetime.timezone.utc)
        ms = int(utc.timestamp() * 1000)
        # kupokea: mechi iwe ya siku zijao (au iko ndani ya 2 siku zilizopita)
        if ms > (now - datetime.timedelta(days=2)).timestamp() * 1000:
            return ms
    return None


def discover_matches(day_end_ms=None):
    """Mechi ZOTE za siku (GMT+3) ambazo bado hazianza.
    day_end_ms = mpaka wa siku (ikiwa iko wazi, inatumika badala ya end_of_day ya sasa)."""
    now = time.time()
    if now - _CACHE["disc"][0] < CACHE_DISC and _CACHE["disc"][1]:
        ms = _CACHE["disc"][1]
    else:
        ms = []

        def fetch_league(item):
            slug, name = item
            for attempt in range(2):  # retry 1: cold-start/timeout hazopotezi league nzima
                try:
                    html = get(f"{WC_BASE}/{slug}/", timeout=20)
                    r = _parse_wc_league(html)
                    for m in r:
                        m["comp"] = name
                    return r
                except Exception:
                    if attempt == 1:
                        return []
                    time.sleep(1.5)
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            for res in ex.map(fetch_league, WC_LEAGUES):
                ms += res
        seen = {}
        for m in ms:
            if not m.get("kickoff_utc_ms"):  # JSON-LD tayari ilitoa UTC — usiibadilishe
                m["kickoff_utc_ms"] = _wc_to_utc_ms(m)
            if m["kickoff_utc_ms"] and m["slug"] not in seen:
                seen[m["slug"]] = m
        ms = list(seen.values())
        _CACHE["disc"] = (now, ms)
    now_ms = int(now * 1000)
    end = day_end_ms if day_end_ms else day_ms_range(now)[1]
    up = [m for m in _CACHE["disc"][1]
          if m.get("kickoff_utc_ms") and now_ms < m["kickoff_utc_ms"] <= end]
    up.sort(key=lambda m: m["kickoff_utc_ms"])
    return up


# ---------------- WC per-match odds + model ----------------
def _is_num(s):
    try:
        v = float(s)
        return 1.01 <= v <= 1000
    except Exception:
        return False


def wc_odds(slug):
    now = time.time()
    if slug in _CACHE["odds"] and now - _CACHE["odds"][slug][0] < CACHE_ODDS:
        return _CACHE["odds"][slug][1]
    d = {"odds": {}, "model": {}}
    try:
        html = get(f"{WC_MATCH_BASE}/{slug}/", timeout=25)
        raw = re.sub(r"<script.*?</script>", "", html, flags=re.S)
        toks = [x.strip() for x in re.sub(r"<[^>]+>", "|", raw).split("|")]
        toks = [x for x in toks if x]

        def after(label, window=25):
            for i, x in enumerate(toks):
                if x == label:
                    for j in range(i + 1, min(i + 1 + window, len(toks))):
                        if _is_num(toks[j]):
                            return float(toks[j])
            return None

        for line in ("1.5", "2.5", "3.5"):
            u, o = after(f"Under {line} goals"), after(f"Over {line} goals")
            if u:
                d["odds"]["U" + line] = u
            if o:
                d["odds"]["O" + line] = o

        # BTTS: 'Yes'/'No' zina junk token kati na odds yake -> tafuta namba ya kwanza baadae
        i = next((k for k, x in enumerate(toks) if x.endswith("BTTS Odds")), None)
        if i:
            seq = toks[i + 1:i + 14]

            def _num_after(word):
                for j, s in enumerate(seq):
                    if s == word:
                        for j2 in range(j + 1, len(seq)):
                            if _is_num(seq[j2]):
                                return float(seq[j2])
                return None
            y, n = _num_after("Yes"), _num_after("No")
            if y:
                d["odds"]["BTTS_Y"] = y
            if n:
                d["odds"]["BTTS_N"] = n

        # 1X2: 'Draw' iliyoundwa na odds (best bookmaker odds)
        for i, x in enumerate(toks[:600]):
            if x == "Draw":
                pre = [t for t in toks[max(0, i - 6):i] if _is_num(t)]
                post = [t for t in toks[i + 1:i + 8] if _is_num(t)]
                if pre and len(post) >= 2:
                    d["odds"]["1"] = float(pre[-1])
                    d["odds"]["X"] = float(post[0])
                    d["odds"]["2"] = float(post[1])
                    break

        # WC model (data-trans): probabilities + prediction words
        for m in re.finditer(r'data-trans="([^"]+)"[^>]*>(.*?)</', html, flags=re.S):
            trans = m.group(1)
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
            pm = re.search(r"(\d{1,2}\.?\d*)%", text)
            prob = float(pm.group(1)) if pm else None
            if "under.over.probability" in trans and prob is not None:
                d["model"]["pou"] = prob
            elif "btts.percent" in trans and prob is not None:
                d["model"]["pbtts"] = prob
            elif trans.startswith("match.probability.1x2.percent") and prob is not None:
                d["model"]["p1x2"] = prob
            if trans.startswith("match.probability.1x2.1") and len(text) <= 30 and "%" not in text:
                d["model"]["w1x2"] = text
            elif trans.startswith("match.probability.under.over.O") and len(text) <= 8:
                d["model"]["wou"] = text  # "+2.5" au "-2.5"
            elif (trans.startswith("mmatch.probability.btts") or trans.startswith("match.probability.btts")) \
                    and text in ("Yes", "No"):
                d["model"]["wbtts"] = text
    except Exception as e:
        d["error"] = str(e)
    _CACHE["odds"][slug] = (now, d)
    return d


# ---------------- WC final scores (auto results, bila API key) ----------------
_WC_RES = {}  # slug -> (checked_at, (h,a) au None, ttl_s)

def wc_result(slug, _now=None):
    """Final score kutoka page ya mechi ya WinComparator — DATA HALISI, hakuna key.
    Rudisha (home_score, away_score) ikiwa mechi IMEMALIZIKA ('End');
    None ikiwa bado hai, haijachezwa, au page haikupokeka."""
    now = _now if _now is not None else time.time()
    c = _WC_RES.get(slug or "")
    if c and now - c[0] < c[2]:
        return c[1]
    score = None
    try:
        html = get(f"{WC_MATCH_BASE}/{slug}/", timeout=25)
        i = html.find("match.event.over")  # status marker 'End' (FT)
        if i >= 0:
            seg = html[i:i + 3000]
            m = re.search(r"<div>\s*(\d{1,2})\s*-\s*(\d{1,2})\s*</div>", seg)
            if m:
                score = (int(m.group(1)), int(m.group(2)))
    except Exception:
        score = None
    # result iliyothibitishwa: cache 6h (haiwezi kubadilika) · siyo: recheck kila 15min
    _WC_RES[slug or ""] = (now, score, 21600 if score else 900)
    return score


# ---------------- FootballPredictions (live) ----------------
def fp_predictions():
    now = time.time()
    if now - _CACHE["fp"][0] < CACHE_FP and _CACHE["fp"][1]:
        return _CACHE["fp"][1]
    out = {}
    urls = {
        "efl": "https://footballpredictions.com/footballpredictions/eflcuppredictions/",
        "uel": "https://footballpredictions.com/footballpredictions/europaleaguepredictions/",
        "uecl": "https://footballpredictions.com/footballpredictions/europa-conference-league-predictions/",
        "pl": "https://footballpredictions.com/footballpredictions/premierleaguepredictions/",
        "championship": "https://footballpredictions.com/footballpredictions/championshippredictions/",
        "laliga": "https://footballpredictions.com/footballpredictions/primeradivisionpredictions/",
        "bundesliga": "https://footballpredictions.com/footballpredictions/bundesligapredictions/",
        "seriea": "https://footballpredictions.com/footballpredictions/serieapredictions/",
        "ligue1": "https://footballpredictions.com/footballpredictions/ligue1predictions/",
    }

    def parse(fp_url):
        try:
            t = re.sub(r"<script.*?</script>", "", get(fp_url, timeout=25), flags=re.S)
            t = re.sub(r"<[^>]+>", "|", t)
            t = re.sub(r"\|+", "|", t)
            res = {}
            for tm in re.finditer(r"([A-Z][A-Za-z0-9 .&\u2019-]{2,40}?)\s+vs\s+([A-Z][A-Za-z0-9 .&\u2019-]{2,40}?)\s+Prediction\|", t):
                home, away = tm.group(1).strip(), tm.group(2).strip()
                back = t[max(0, tm.start() - 2000):tm.start()]
                pm = list(re.finditer(r"Prediction:", back))
                if not pm:
                    continue
                sm = re.search(r"\|(\d)\s*-\s*(\d)\|", back[pm[-1].end():])
                if sm:
                    res[(home, away)] = (int(sm.group(1)), int(sm.group(2)))
            return res
        except Exception:
            return {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(parse, urls.values()):
            out.update(res)
    _CACHE["fp"] = (now, out)
    return out


def fp_for(home, away):
    fp = fp_predictions()
    nh, na = _norm(home), _norm(away)
    for (h, a), cs in fp.items():
        if _teams_match(home, away, h, a):
            return cs
    for (h, a), cs in fp.items():
        if (nh and nh == _norm(h) and na and na == _norm(a)):
            return cs
    return None


# ---------------- Football Whispers (live + cache) ----------------
def _slug_from_url(u):
    return (u or "").rstrip("/").split("/")[-1]


def _url_date_ok(u):
    m = re.search(r"-(\d{2})-(\d{2})-(\d{2,4})(?:-|/|$)", u or "")
    if not m:
        return False
    d, mo = int(m.group(1)), int(m.group(2))
    tz = datetime.timezone(datetime.timedelta(hours=3))  # Africa/Dar es Salaam
    t = datetime.datetime.now(tz)
    for delta in (0, 1):
        dd = t + datetime.timedelta(days=delta)
        if (d, mo) == (dd.day, dd.month):
            return True
    return False


def _fw_cache_posts():
    now = time.time()
    if now - _CACHE["fwf"][0] < 3600 and _CACHE["fwf"][1] is not None:
        return _CACHE["fwf"][1]
    posts = []
    try:
        with open(os.path.join(BASE, "data", "fw_latest.json")) as f:
            data = json.load(f)
        tz = datetime.timezone(datetime.timedelta(hours=3))
        t = datetime.datetime.now(tz)
        ok_dates = {(t + datetime.timedelta(days=i)).strftime("%d/%m/%y") for i in (0, 1)}
        for p in data:
            if p.get("date_gmt") in ok_dates:
                posts.append({"url": p.get("url", ""), "slug": _slug_from_url(p.get("url", "")),
                              "tips": p.get("tips", []), "cs": p.get("cs"),
                              "home_guess": p.get("home_guess"), "away_guess": p.get("away_guess"),
                              "cached": True})
    except Exception:
        pass
    _CACHE["fwf"] = (now, posts)
    return posts


def fw_live_posts():
    now = time.time()
    if now - _CACHE["fw"][0] < CACHE_FW and _CACHE["fw"][1] is not None:
        return _CACHE["fw"][1]
    out = []
    try:
        html = get("https://footballwhispers.com/", timeout=25)
        links = sorted(set(re.findall(r'href="(https://footballwhispers\.com/blog/[^"]+)"', html)))
        links = [u for u in links if _url_date_ok(u)]

        def fetch_post(url):
            try:
                t2 = re.sub(r"<script.*?</script>", "", get(url, timeout=20), flags=re.S)
                t2 = re.sub(r"<style.*?</style>", "", t2, flags=re.S)
                t2 = re.sub(r"<[^>]+>", "\n", t2)
                t2 = H.unescape(t2)
                t2 = re.sub(r"\n\s*\n+", "\n", t2)
                lines = t2.split("\n")
                d = {"tips": [], "cs": None}
                for i, ln in enumerate(lines):
                    if "Correct score" in ln or "Correct Score" in ln:
                        for j in range(i + 1, min(i + 4, len(lines))):
                            mm = re.match(r"\s*([A-Za-z0-9 .\u2019-]+)\s+(\d)\s*-\s*(\d)\s*", lines[j])
                            if mm:
                                d["cs"] = [int(mm.group(2)), int(mm.group(3))]
                                break
                for mm in re.finditer(r">\s*([^\|\n]{3,60}?)\s+at\s+([\d/\.]+)(?:\s*\(([\d\.]+)\))?\s*\|?\s*\n?\s*Likelihood:\s*([^\n]+)", t2):
                    d["tips"].append({"market": mm.group(1).strip(), "frac": mm.group(2),
                                      "dec": float(mm.group(3)) if mm.group(3) else None,
                                      "lik": mm.group(4).strip()})
                d["slug"] = _slug_from_url(url)
                return d
            except Exception:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            for r in ex.map(fetch_post, links):
                if r and r["tips"]:
                    out.append(r)
    except Exception:
        pass
    _CACHE["fw"] = (now, out)
    return out


def _frac(frac):
    if "/" in frac:
        try:
            a, b = frac.split("/")
            return round(1 + int(a) / int(b), 2)
        except Exception:
            return None
    try:
        return float(frac)
    except Exception:
        return None


def _fw_tip(t):
    """Unify tip -> {market, sel, line, odds}."""
    mkt = (t.get("market") or "").lower()
    odds = t.get("odds") or t.get("dec") or _frac(t.get("frac", ""))
    if not odds:
        return None
    sel = t.get("selection")
    line = t.get("line")
    if sel is None and ("over" in mkt or "under" in mkt):
        lm = re.search(r"(\d(?:\.\d)?)", mkt)
        line = float(lm.group(1)) if lm else None
    if mkt == "result" or "match result" in mkt:
        if sel is None:
            sel = mkt.split(":")[-1].strip() if ":" in mkt else None
        return {"market": "result", "sel": sel, "line": None, "odds": float(odds)}
    if "over" in mkt:
        return {"market": "over", "sel": None, "line": float(line) if line else 2.5, "odds": float(odds)}
    if "under" in mkt:
        return {"market": "under", "sel": None, "line": float(line) if line else 2.5, "odds": float(odds)}
    if "btts" in mkt:
        s = (sel or "").lower()
        if not s:
            s = "yes" if "yes" in mkt else ("no" if "no" in mkt else "")
        if s in ("yes", "no"):
            return {"market": "btts", "sel": s, "line": None, "odds": float(odds)}
        return None
    return None  # HT, correct score, combos & — hazitumiwi kwa votes


def fw_for(home, away):
    """Tips kutoka kwa post YOTE inayolingana (live + cache), zimeunganishwa."""
    posts = list(fw_live_posts())
    # cache inajumlishwa hata ukisawa na live — tips zita-dedupe kwa (market, sel, line)
    posts += _fw_cache_posts()
    tips_all, cs, slugs = [], None, []
    for post in posts:
        toks = set()
        for part in (post.get("slug", ""), post.get("home_guess", ""), post.get("away_guess", "")):
            toks |= set(re.split(r"[\s\-]+", _norm(part)))
        toks = {t for t in toks if t}
        th, ta = _toks(home), _toks(away)
        if toks and (th & toks) and (ta & toks):
            slugs.append(post.get("slug") or "")
            if cs is None and post.get("cs"):
                cs = post["cs"]
            for t in post.get("tips", []):
                t2 = _fw_tip(t)
                if t2:
                    tips_all.append(t2)
    if not tips_all:
        return None
    best = {}
    for t in tips_all:
        key = (t["market"], t["sel"], t["line"])
        if key not in best or t["odds"] < best[key]["odds"]:
            best[key] = t
    return {"tips": list(best.values()), "cs": cs, "slug": (slugs[0] if slugs else None),
            "posts": len(slugs)}


# ---------------- Forebet (local cache) ----------------
def forebet_for(home, away, kickoff_utc_ms):
    now = time.time()
    if now - _CACHE["fb"][0] < 3600 and _CACHE["fb"][1] is not None:
        matches = _CACHE["fb"][1]
    else:
        matches = []
        try:
            with open(os.path.join(BASE, "data", "raw.json")) as f:
                matches = json.load(f).get("matches", [])
        except Exception:
            matches = []
        _CACHE["fb"] = (now, matches)
    if not matches:
        return None
    day = datetime.datetime.fromtimestamp(kickoff_utc_ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    for m in matches:
        if m.get("date_gmt") != day:
            continue
        if _teams_match(home, away, m.get("home", ""), m.get("away", "")):
            return m
    return None


# ---------------- probability conversions ----------------
def _dc_from_1x2(o1, ox, o2):
    s = 1 / o1 + 1 / ox + 1 / o2
    return (1 / o1 + 1 / ox) / s, (1 / ox + 1 / o2) / s


def _o15_u35_from_over_odds(o, line):
    lam = lam_from_over(int(float(line) + 0.5), 1.0 / o)
    return over_prob(lam, 2), pois_cdf(3, lam)


def _o15_u35_from_under_odds(u, line):
    lam = lam_from_under(float(line), 1.0 / u)
    return over_prob(lam, 2), pois_cdf(3, lam)


def _soko_goal_probs(odds):
    """P(O1.5) na P(U3.5) kutoka kwa market O/U odds (debiased + Poisson)."""
    p_o15 = p_u35 = None
    o15, u15 = odds.get("O1.5"), odds.get("U1.5")
    if o15 and u15:
        s = 1 / o15 + 1 / u15
        p_o15 = (1 / o15) / s
    elif o15:
        p_o15 = 1 / o15
    o35, u35 = odds.get("O3.5"), odds.get("U3.5")
    if u35 and o35:
        s = 1 / o35 + 1 / u35
        p_u35 = (1 / u35) / s
    elif u35:
        p_u35 = 1 / u35
    if p_o15 is None or p_u35 is None:
        if odds.get("O2.5") and odds.get("U2.5"):
            s = 1 / odds["O2.5"] + 1 / odds["U2.5"]
            p25 = (1 / odds["O2.5"]) / s
            a, b = _o15_u35_from_over_odds(odds["O2.5"], 2.5)
            p_o15 = p_o15 if p_o15 is not None else a
            p_u35 = p_u35 if p_u35 is not None else b
        elif odds.get("O2.5"):
            a, b = _o15_u35_from_over_odds(odds["O2.5"], 2.5)
            p_o15 = p_o15 if p_o15 is not None else a
            p_u35 = p_u35 if p_u35 is not None else b
        elif odds.get("U2.5"):
            a, b = _o15_u35_from_under_odds(odds["U2.5"], 2.5)
            p_o15 = p_o15 if p_o15 is not None else a
            p_u35 = p_u35 if p_u35 is not None else b
    return p_o15, p_u35


# ---------------- research kwa match moja ----------------
def build_match_picks(m):
    """Rudisha votes kwa kila market kutoka kwa vyanzo vyote vinavyopatikana."""
    votes = {}

    def add(mkt, prob, src):
        if prob is None:
            return
        prob = min(0.97, max(0.01, prob))
        votes.setdefault(mkt, []).append((prob, src))

    wc, wc_err = {}, None
    try:
        wc = wc_odds(m["slug"])
        wc_err = wc.get("error")
    except Exception:
        pass
    odds, model = wc.get("odds", {}), wc.get("model", {})
    fp = fp_for(m["home"], m["away"])
    fw = fw_for(m["home"], m["away"])
    fb = forebet_for(m["home"], m["away"], m.get("kickoff_utc_ms", 0))

    # 1) SOKO — bookmakers 30+ (aggregated market odds)
    o1, ox, o2 = odds.get("1"), odds.get("X"), odds.get("2")
    if o1 and ox and o2:
        p1x, px2 = _dc_from_1x2(o1, ox, o2)
        add("DC1X", p1x, SOKO)
        add("DCX2", px2, SOKO)
    p_o15, p_u35 = _soko_goal_probs(odds)
    if p_o15 is not None and p_o15 >= 0.50:
        add("O15", p_o15, SOKO)
    if p_u35 is not None and p_u35 >= 0.50:
        add("U35", p_u35, SOKO)
    by, bn = odds.get("BTTS_Y"), odds.get("BTTS_N")
    if by and bn:
        s = 1 / by + 1 / bn
        add("BTTS", (1 / by) / s, SOKO)

    # 2) WCM — WinComparator model
    w1x2, p1 = model.get("w1x2"), model.get("p1x2")
    if w1x2 and p1:
        t = w1x2.lower()
        nh, na = _norm(m["home"]), _norm(m["away"])
        home_w = ("home" in t) or (nh.split() and any(w in t for w in nh.split() if len(w) > 3))
        away_w = ("away" in t) or (na.split() and any(w in t for w in na.split() if len(w) > 3))
        if "draw" in t:
            pass  # draw prediction: haivote DC yoyote
        elif home_w and not away_w:
            add("DC1X", p1 / 100, WCM)
        elif away_w and not home_w:
            add("DCX2", p1 / 100, WCM)
    pou = model.get("pou")
    if pou is not None and 5 <= pou <= 95:
        lam = lam_from_over(3, pou / 100)  # P(over 2.5) -> Poisson lam -> P(over 1.5) / P(under 3.5)
        if over_prob(lam, 2) >= 0.60:
            add("O15", over_prob(lam, 2), WCM)
        if pois_cdf(3, lam) >= 0.60:
            add("U35", pois_cdf(3, lam), WCM)
    pbtts = model.get("pbtts")
    if pbtts is not None and pbtts >= 55:
        add("BTTS", pbtts / 100, WCM)

    # 3) FPS — FootballPredictions (correct score)
    if fp:
        gh, ga = fp
        g = gh + ga
        if gh > ga:
            add("DC1X", 0.62, FPS)
        elif ga > gh:
            add("DCX2", 0.62, FPS)
        else:
            add("DC1X", 0.55, FPS)
            add("DCX2", 0.55, FPS)
        if g >= 1:
            add("O15", min(0.90, 0.55 + 0.05 * g), FPS)
        if g <= 3:
            add("U35", min(0.90, 0.58 + 0.08 * (3 - g)), FPS)
        if gh > 0 and ga > 0:
            add("BTTS", 0.65, FPS)

    # 4) FWS — Football Whispers (vote 1 kwa market kwa chanzo)
    if fw:
        nh, na = _norm(m["home"]), _norm(m["away"])
        results = [t for t in fw["tips"] if t["market"] == "result"]
        overs = sorted([t for t in fw["tips"] if t["market"] == "over"], key=lambda t: abs(t["line"] - 1.5))
        unders = sorted([t for t in fw["tips"] if t["market"] == "under"], key=lambda t: abs(t["line"] - 3.5))
        btts_y = [t for t in fw["tips"] if t["market"] == "btts" and t["sel"] == "yes"]
        for t in results:
            sel = (t["sel"] or "").lower()
            p = min(0.95, 1 / t["odds"])
            home_w = sel == "1" or any(w in sel for w in nh.split() if len(w) > 3)
            away_w = sel == "2" or any(w in sel for w in na.split() if len(w) > 3)
            if home_w and not away_w:
                add("DC1X", p, FWS)
                break
            if away_w and not home_w:
                add("DCX2", p, FWS)
                break
        if overs:
            t = overs[0]
            p = min(0.95, 1 / t["odds"])
            add("O15", p if t["line"] == 1.5 else over_prob(lam_from_over(int(t["line"] + 0.5), p), 2), FWS)
        if unders:
            t = unders[0]
            p = min(0.95, 1 / t["odds"])
            add("U35", p if t["line"] == 3.5 else pois_cdf(3, lam_from_under(t["line"], p)), FWS)
        if btts_y:
            add("BTTS", min(0.95, 1 / btts_y[0]["odds"]), FWS)

    # 5) FBS — Forebet (local cache, siku ile ile)
    if fb and fb.get("forebet"):
        f = fb["forebet"]
        p1f, pxf, p2f = f.get("p1"), f.get("px"), f.get("p2")
        pred = f.get("pred")
        if None not in (p1f, pxf, p2f):
            if pred == "1":
                add("DC1X", (p1f + pxf) / 100, FBS)
            elif pred == "2":
                add("DCX2", (p2f + pxf) / 100, FBS)
            elif pred == "X":
                add("DC1X", (p1f + pxf) / 100, FBS)
                add("DCX2", (p2f + pxf) / 100, FBS)
        fcs = f.get("cs")
        if fcs:
            try:
                gh, ga = (int(x) for x in str(fcs).split("-"))
                g = gh + ga
                if g >= 1:
                    add("O15", min(0.90, 0.55 + 0.05 * g), FBS)
                if g <= 3:
                    add("U35", min(0.90, 0.58 + 0.08 * (3 - g)), FBS)
                if gh > 0 and ga > 0:
                    add("BTTS", 0.65, FBS)
            except Exception:
                pass
    if fb and fb.get("fw"):
        tips2 = [t2 for t2 in (_fw_tip(t) for t in fb["fw"].get("tips", [])) if t2]
        nh, na = _norm(m["home"]), _norm(m["away"])
        # DC vote: forebet pred ni bingwa; fw tips DC peke yake ukikosa pred
        for t in [t for t in tips2 if t["market"] == "result"] if not (fb.get("forebet") or {}).get("pred") else []:
            sel = (t["sel"] or "").lower()
            p = min(0.95, 1 / t["odds"])
            home_w = sel == "1" or any(w in sel for w in nh.split() if len(w) > 3)
            away_w = sel == "2" or any(w in sel for w in na.split() if len(w) > 3)
            if home_w and not away_w:
                add("DC1X", p, FBS)
                break
            if away_w and not home_w:
                add("DCX2", p, FBS)
                break
        overs = sorted([t for t in tips2 if t["market"] == "over"], key=lambda t: abs(t["line"] - 1.5))
        if overs:
            t = overs[0]
            p = min(0.95, 1 / t["odds"])
            add("O15", p if t["line"] == 1.5 else over_prob(lam_from_over(int(t["line"] + 0.5), p), 2), FBS)
        unders = sorted([t for t in tips2 if t["market"] == "under"], key=lambda t: abs(t["line"] - 3.5))
        if unders:
            t = unders[0]
            p = min(0.95, 1 / t["odds"])
            add("U35", p if t["line"] == 3.5 else pois_cdf(3, lam_from_under(t["line"], p)), FBS)
        if [t for t in tips2 if t["market"] == "btts" and t["sel"] == "yes"]:
            add("BTTS", min(0.95, 1 / [t for t in tips2 if t["market"] == "btts" and t["sel"] == "yes"][0]["odds"]), FBS)

    return {"votes": votes, "odds": odds, "model": model, "fp": fp,
            "fw": fw, "fb": fb, "wc_err": wc_err}


# ---------------- consensus + filter ----------------
def _market_odds(mkt, odds, conf):
    """Odds inayotumika = odds ya market halisi (inayochanajiwa); ukidhibitikana, fair odds."""
    if mkt == "O15" and odds.get("O1.5"):
        return round(odds["O1.5"], 2)
    if mkt == "U35" and odds.get("U3.5"):
        return round(odds["U3.5"], 2)
    if mkt == "BTTS" and odds.get("BTTS_Y"):
        return round(odds["BTTS_Y"], 2)
    return round(1 / conf, 2)


LABEL = {"DC1X": "{home} au draw", "DCX2": "{away} au draw",
         "O15": "Magoli 2+ (Over 1.5)", "U35": "Magoli 3- (Under 3.5)", "BTTS": "Zote zitaungua (BTTS)"}
MKT_NAME = {"DC1X": "Double Chance", "DCX2": "Double Chance",
            "O15": "Over 1.5 goals", "U35": "Under 3.5 goals", "BTTS": "BTTS - Yes"}


def build_picks(matches, researched):
    """1 best pick kwa mechi: n>=4 sources na conf>=70%."""
    out = []
    for m, r in zip(matches, researched):
        votes = r["votes"]
        best = None
        for mkt, vlist in votes.items():
            n = len(vlist)
            if n < N_MIN:
                continue
            avg = sum(v[0] for v in vlist) / n
            conf = min(0.97, avg + 0.03 * (n - 1))
            if conf < CONF_MIN:
                continue
            if best is None or conf > best["conf"]:
                best = {"market": mkt, "conf": conf, "n": n,
                        "srcs": [v[1] for v in vlist], "avg": avg}
        if not best:
            continue
        odds = r["odds"]
        if best["market"] == "DC1X":
            o1, ox, o2 = odds.get("1"), odds.get("X"), odds.get("2")
            if o1 and ox and o2:
                p1x, _ = _dc_from_1x2(o1, ox, o2)
                odds_val = round(1 / p1x, 2)
            else:
                odds_val = round(1 / best["conf"], 2)
        elif best["market"] == "DCX2":
            o1, ox, o2 = odds.get("1"), odds.get("X"), odds.get("2")
            if o1 and ox and o2:
                _, px2 = _dc_from_1x2(o1, ox, o2)
                odds_val = round(1 / px2, 2)
            else:
                odds_val = round(1 / best["conf"], 2)
        else:
            odds_val = _market_odds(best["market"], odds, best["conf"])
        label = LABEL[best["market"]].format(home=m["home"], away=m["away"])
        out.append({
            "id": m["slug"], "home": m["home"], "away": m["away"], "comp": m.get("comp", ""),
            "kickoff": m["kickoff_utc_ms"],
            "picks": [{
                "mid": m["slug"], "match": f"{m['home']} - {m['away']}", "comp": m.get("comp", ""),
                "kickoff": m["kickoff_utc_ms"], "market": MKT_NAME[best["market"]], "selection": label,
                "type": best["market"], "odds": odds_val, "final": round(best["conf"], 3),
                "n": best["n"], "src": best["srcs"], "flag": "ok",
                "home": m["home"], "away": m["away"],
            }],
        })
    return out


def build_accumulator(picks):
    """Bet ya siku: conf >= 80% pekee kwanza; total lazima iwe 1.6–3.0 (legs <= 10).
    Ikiwa 80%+ pekee hazijafikia 1.6 → jumlisha picks 70–80% (zilizopita kiwango cha pick)."""
    seen, uniq = set(), []
    for p in picks:
        if p.get("mid") and p["mid"] not in seen:
            seen.add(p["mid"])
            uniq.append(p)
    primary = [p for p in uniq if p.get("final", 0) >= ACC_CONF]
    secondary = [p for p in uniq if ACC_FALLBACK_CONF <= p.get("final", 0) < ACC_CONF]
    legs, tot = [], 1.0

    def fill(pool):
        nonlocal tot
        for p in pool:
            if len(legs) >= ACC_MAX_LEGS:
                return
            if tot * p["odds"] <= ACC_MAX_TOTAL + 1e-9:
                legs.append(p)
                tot *= p["odds"]
    fill(primary)
    if tot < ACC_MIN_TOTAL - 1e-9:
        fill(secondary)
    min_met = tot >= ACC_MIN_TOTAL - 1e-9
    if not legs:
        return {"legs": [], "total": 1.0, "min_met": False, "min": ACC_MIN_TOTAL,
                "max": ACC_MAX_TOTAL, "max_legs": ACC_MAX_LEGS, "conf_min": ACC_CONF}
    return {"legs": legs, "total": round(tot, 2), "min_met": min_met, "min": ACC_MIN_TOTAL,
            "max": ACC_MAX_TOTAL, "max_legs": ACC_MAX_LEGS, "conf_min": ACC_CONF}


def get_picks(max_research=80):
    """DAILY: mechi zote za siku (GMT+3) zilizobaki → research → picks + accumulator."""
    day_start, day_end, day_date = day_ms_range()
    matches = discover_matches(day_end_ms=day_end)
    research_matches = matches[:max_research]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        researched = list(ex.map(build_match_picks, research_matches))
    matches_with_picks = build_picks(research_matches, researched)
    # sort by confidence desc
    matches_with_picks.sort(key=lambda m: -m["picks"][0]["final"])
    flat = [m["picks"][0] for m in matches_with_picks]
    all_fixtures = [{"id": m["slug"], "home": m["home"], "away": m["away"],
                     "comp": m.get("comp", ""), "kickoff": m.get("kickoff_utc_ms")}
                    for m in matches[:80]]
    return {
        "now": int(time.time() * 1000),
        "date": day_date,
        "all_fixtures": all_fixtures,
        "day_start_ms": day_start,
        "day_end_ms": day_end,
        "generated_ms": int(time.time() * 1000),
        "live": True,
        "matches_total": len(matches),
        "researched": len(research_matches),
        "sources": ALL_SOURCES,
        "rule": (f"siku kamili ({day_date}) · pick: vyanzo {N_MIN}+ na conf >= {int(CONF_MIN*100)}% · "
                 f"accumulator: conf >= {int(ACC_CONF*100)}%, total odds {ACC_MIN_TOTAL}–{ACC_MAX_TOTAL}"),
        "acc": build_accumulator(flat),
        "matches": matches_with_picks,
    }


if __name__ == "__main__":
    print(json.dumps(get_picks(), indent=1, ensure_ascii=False))
