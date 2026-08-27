#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py — 96th Predictions LIVE server + DATABASE
- /            → app (static)
- /api/picks   → data hai (Whispers + WinComparator odds + FP, cache min 15)
- /api/status  → cache info
- /api/sessions        → records (sessions + results) kutoka SQLite
- POST /api/sessions   → runza session (kwa app baada ya START BOT)
- POST /api/sessions/<id>/score    → kiunga score → auto win/loss
- POST /api/sessions/<id>/override → manual W/L/V
- /api/stats           → winning rate ya jumla
Run: python3 server.py   (port 8030)
Env: P96_PORT, P96_DB_PATH, P96_PIPELINE_DIR
"""
import json, os, re, sys, time, sqlite3, threading, calendar, datetime
from contextlib import contextmanager
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

BASE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.environ.get("P96_PIPELINE_DIR", os.path.join(BASE, "betting-researcher"))
DB_PATH = os.environ.get("P96_DB_PATH", os.path.join(BASE, "data", "records.db"))
PORT = int(os.environ.get("P96_PORT") or os.environ.get("PORT") or "8030")
sys.path.insert(0, PIPELINE)
sys.path.insert(0, BASE)

try:
    import analyze_v3 as A  # noqa: F401  (maths/poisson; live_research pia huihitaji)
except Exception:
    A = None  # pipeline haipo — server bado huanze, /api/picks itaonyesha error

CACHE_TTL = 15 * 60
CACHE = {"ts": 0.0, "payload": None, "busy": False, "error": None, "counts": {}}
PLOCK = threading.Lock()
PCOND = threading.Condition(PLOCK)
RAW_DATA_DIR = os.path.join(PIPELINE, "data")

# ================= DATABASE (SQLite) =================
if os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
DBLOCK = threading.Lock()

def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c

@contextmanager
def dbx():
    """SQLite connection inayefungwa kila wakati (commit/rollback + close)."""
    c = db()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def db_init():
    with DBLOCK, dbx() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY, started_at INTEGER, cycle TEXT, meta TEXT, picks TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS results(
            session_id TEXT, mid TEXT, score TEXT, updated_at INTEGER,
            PRIMARY KEY(session_id, mid))""")
        c.execute("""CREATE TABLE IF NOT EXISTS overrides(
            session_id TEXT, pick_key TEXT, status TEXT, updated_at INTEGER,
            PRIMARY KEY(session_id, pick_key))""")

def eval_pick(p_type, h, a):
    if p_type == "DC1X": return "win" if h >= a else "loss"
    if p_type == "DCX2": return "win" if a >= h else "loss"
    if p_type == "O15":  return "win" if h + a >= 2 else "loss"
    if p_type == "U35":  return "win" if h + a <= 3 else "loss"
    if p_type == "BTTS": return "win" if (h > 0 and a > 0) else "loss"
    return "pending"

def merged_session(row):
    picks = json.loads(row["picks"] or "[]")
    with dbx() as c:
        res = {r["mid"]: r["score"] for r in c.execute(
            "SELECT mid, score FROM results WHERE session_id=?", (row["id"],))}
        ovr = {r["pick_key"]: r["status"] for r in c.execute(
            "SELECT pick_key, status FROM overrides WHERE session_id=?", (row["id"],))}
    for p in picks:
        pk = p["mid"] + "|" + p.get("market", "")
        if pk in ovr:
            p["status"] = ovr[pk]; p["manual"] = True
        elif p["mid"] in res:
            m = re.match(r"(\d+)\s*[-:]\s*(\d+)", res[p["mid"]])
            if m:
                h, a = int(m.group(1)), int(m.group(2))
                p["score"] = res[p["mid"]]
                if not p.get("manual"):
                    p["status"] = eval_pick(p.get("type"), h, a)
        p.setdefault("status", "pending")  # pick isha score/override -> pending (bug: ilikuwa KeyError)
    w = sum(1 for p in picks if p.get("status") == "win")
    l = sum(1 for p in picks if p.get("status") == "loss")
    s = {
        "id": row["id"], "started_at": row["started_at"], "cycle": row["cycle"] or "FLEXIBLE",
        "meta": json.loads(row["meta"] or "{}"), "picks": picks,
        "stats": {"w": w, "l": l, "d": w + l, "rate": (w / (w + l)) if (w + l) else None},
    }
    return s

def all_sessions():
    with dbx() as c:
        rows = c.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
    return [merged_session(r) for r in rows]

def global_stats(sessions):
    w = sum(s["stats"]["w"] for s in sessions)
    l = sum(s["stats"]["l"] for s in sessions)
    pend = sum(1 for s in sessions for p in s["picks"] if p.get("status") in (None, "pending"))
    per_mkt = {}
    for s in sessions:
        for p in s["picks"]:
            k = p.get("market", "—")
            per_mkt.setdefault(k, {"w": 0, "l": 0})
            if p["status"] == "win": per_mkt[k]["w"] += 1
            if p["status"] == "loss": per_mkt[k]["l"] += 1
    return {"wins": w, "losses": l, "pending": pend,
            "rate": (w / (w + l)) if (w + l) else None,
            "sessions": len(sessions), "per_market": per_mkt}

# ================= LIVE DATA (engine v6: DAILY discovery + consensus) =================
TZ3 = datetime.timezone(datetime.timedelta(hours=3))  # GMT+3 (Tanzania)

def tz3_today():
    return datetime.datetime.now(TZ3).strftime("%Y-%m-%d")

def build_payload():
    t0 = time.time()
    try:
        import live_research as LR
        payload = LR.get_picks(max_research=80)
        counts = {"matches": len(payload.get("matches", [])),
                  "researched": payload.get("researched"),
                  "matches_total": payload.get("matches_total"),
                  "built_in_s": round(time.time() - t0, 1)}
    except Exception as e:
        payload = {"generated_ms": int(time.time() * 1000), "live": False, "matches": []}
        counts = {"error": str(e)[:200], "built_in_s": round(time.time() - t0, 1)}
    payload["generated_utc"] = datetime.datetime.now(TZ3).strftime("%d/%m/%Y %H:%M")
    payload["generated_ms"] = int(time.time() * 1000)
    payload["counts"] = counts
    return payload

def get_payload(force=False):
    # siku mpya (GMT+3) → cache ya zamani haimtumiki: research ya siku hiyo hiyo
    today = tz3_today()
    with PCOND:
        if (not force and CACHE["payload"] and CACHE.get("date") == today
                and (time.time() - CACHE["ts"]) < CACHE_TTL):
            return CACHE["payload"]
        if CACHE["busy"]:
            if CACHE["payload"]:
                return CACHE["payload"]  # build inayoendelea — rudi bila kupinga
            # payload ya kwanza haijakuja — subiri build hiyo (hadi 150s; cold start + retry za leagues)
            PCOND.wait(timeout=150)
            if CACHE["payload"]:
                return CACHE["payload"]
            raise TimeoutError("payload build busy")
        CACHE["busy"] = True
    try:
        payload = build_payload()
        with PCOND:
            CACHE.update(payload=payload, ts=time.time(), date=tz3_today(),
                         counts=payload["counts"], error=None, busy=False)
            PCOND.notify_all()
        auto_results()
        return payload
    except Exception as e:
        with PCOND:
            CACHE["error"] = str(e)[:300]; CACHE["busy"] = False
            PCOND.notify_all()
        if CACHE["payload"]:
            return CACHE["payload"]
        raise

# ================= AUTO RESULTS (WC scores 1st — no key; Odds API 2nd — fallback) =================
import urllib.request
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
_EVENTS_CACHE = {"ts": 0.0, "data": {}}
_EVENTS_TTL = 1800  # 30min kwa sport (kuokoa quota ya Odds API free: 500 req/mwezi)
_AUTORES_MIN_GAP = int(os.environ.get("P96_AUTORES_GAP") or 900)  # sekunde kati ya auto-results (default 15min)
_AUTORES_LAST = [0.0]
_ALIAS = {"hearts": {"midlothian"}, "spurs": {"tottenham"}, "wolves": {"wolverhampton"}}

def _split_match(name):
    """'A - B' / 'A – B' / 'A vs B' -> ('A','B'). (v7.1: live picks hutumia '-' — bug ya awali ilikuwa '-' pekee.)"""
    for sep in (" \u2013 ", " - ", " vs "):
        if sep in name:
            a, b = name.split(sep, 1)
            a, b = a.strip(), b.strip()
            if a and b:
                return a, b
    return None, None

def _norm_team(name):
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "").lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(fc|fk|cf|afc|sc|sk|bk|ac)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _same_team(a, b):
    na, nb = _norm_team(a), _norm_team(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    ta, tb = set(na.split()), set(nb.split())
    common = ta & tb
    if common and len(common) >= min(len(ta), len(tb)) * 0.5:
        return True
    for al, extra in _ALIAS.items():
        if al in ta and (tb & extra):
            return True
        if al in tb and (ta & extra):
            return True
    return False

def sport_for_comp(comp):
    c = (comp or "").lower()
    rules = [
        (["uecl", "conference"], "soccer_europe_europa_conference_league"),
        (["uel", "europa league"], "soccer_europe_europa_league"),
        (["ucl", "champions"], "soccer_europe_champions_league"),
        (["efl", "carabao", "league cup"], "soccer_england_carabao_cup"),
        (["la liga", "laliga", "primera"], "soccer_spain_primera_division"),
        (["championship"], "soccer_england_championship"),
        (["premier"], "soccer_england_premier"),
    ]
    for keys, sport in rules:
        if any(k in c for k in keys):
            return sport
    return None

def _odds_events(sport):
    now = time.time()
    if now - _EVENTS_CACHE["ts"] < _EVENTS_TTL and sport in _EVENTS_CACHE["data"]:
        return _EVENTS_CACHE["data"][sport]
    url = ("https://api.the-odds-api.com/v4/sports/%s/events?status=completed"
           "&region=eu&marketType=american&oddsMarket=h2h&dateFormat=iso&apiKey=%s") % (sport, ODDS_API_KEY)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "96th-predictions/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode())
        _EVENTS_CACHE["data"][sport] = data
        _EVENTS_CACHE["ts"] = now
        return data
    except Exception:
        return _EVENTS_CACHE["data"].get(sport, [])

def auto_results():
    """Hujaza scores za mechi zilizomalizika kwenye picks bado 'pending' (auto).
    v7.3: chanzo 1 = WinComparator final scores (hakuna key) · chanzo 2 = Odds API (fallback,
    inahitaji ODDS_API_KEY). Rate-limited (P96_AUTORES_GAP, default 15min).
    Best-effort: makosa hayaimarisi request ya /api/sessions."""
    try:
        _auto_results_inner()
    except Exception as e:
        sys.stderr.write("[auto_results] error (imependwa): %s\n" % e)

def _pending_picks(now_ms):
    """[(sid, pick)] — si manual, kickoff +120min < now, bado hakuna result."""
    with dbx() as c:
        done = {(r[0], r[1]) for r in c.execute("SELECT session_id, mid FROM results")}
    out = []
    with dbx() as c:
        for row in c.execute("SELECT id, picks FROM sessions").fetchall():
            for p in json.loads(row["picks"] or "[]"):
                if p.get("manual"):
                    continue
                if now_ms < (p.get("kickoff") or 0) + 120 * 60000:
                    continue  # mechi bado hai/ya hivi karibuni
                if (row["id"], p.get("mid")) in done:
                    continue
                out.append((row["id"], p))
    return out

def _auto_results_wc(pending, now_ms, cap=15):
    """Chanzo 1: final score kutoka page ya mechi ya WinComparator (hakuna key).
    Pick ya live ina mid = slug ya WC, hivyo hii hufanya kazi kila wakati."""
    try:
        import live_research as LR
    except Exception:
        return 0
    found = 0
    with DBLOCK, dbx() as c:
        for sid, p in pending:
            if found >= cap:
                break
            slug = p.get("mid") or ""
            if " " in slug or "/" in slug or not slug:
                continue
            score = LR.wc_result(slug)
            if score:
                c.execute("INSERT INTO results(session_id, mid, score, updated_at) VALUES(?,?,?,?)",
                          (sid, p["mid"], "%d-%d" % score, int(now_ms)))
                found += 1
    return found

def _auto_results_odds(pending, now_ms):
    """Chanzo 2 (fallback, inahitaji ODDS_API_KEY): The Odds API events."""
    if not ODDS_API_KEY:
        return 0
    sports = {}
    for sid, p in pending:
        sport = sport_for_comp(p.get("comp"))
        if sport:
            sports.setdefault(sport, []).append((sid, p))
    if not sports:
        return 0
    found = 0
    with DBLOCK, dbx() as c:
        for sport, items in sports.items():
            events = _odds_events(sport)
            for sid, p in items:
                nh, na = _split_match(p.get("match") or "")
                if not nh or not na:
                    continue
                for ev in events:
                    eh, ea = ev.get("home_team"), ev.get("away_team")
                    if not eh or not ea or ev.get("home_score") is None:
                        continue
                    try:
                        et = int(datetime.datetime.fromisoformat(
                            ev["commence_time"].replace("Z", "+00:00")).timestamp() * 1000)
                    except Exception:
                        continue
                    if abs(et - (p.get("kickoff") or 0)) > 2 * 86400000:
                        continue
                    direct = _same_team(nh, eh) and _same_team(na, ea)
                    swap = _same_team(nh, ea) and _same_team(na, eh)
                    if not (direct or swap):
                        continue
                    h, a = (ev["home_score"], ev["away_score"]) if direct else (ev["away_score"], ev["home_score"])
                    c.execute("INSERT INTO results(session_id, mid, score, updated_at) VALUES(?,?,?,?)",
                              (sid, p["mid"], "%d-%d" % (h, a), int(now_ms)))
                    found += 1
                    break
    return found

def _auto_results_inner():
    now = time.time()
    if now - _AUTORES_LAST[0] < _AUTORES_MIN_GAP:
        return
    now_ms = now * 1000
    pending = _pending_picks(now_ms)
    if not pending:
        return
    found = _auto_results_wc(pending, now_ms)
    if ODDS_API_KEY:
        pending2 = _pending_picks(now_ms)  # baada ya WC pass — tazama zile zilizobaki
        found += _auto_results_odds(pending2, now_ms)
    if found:
        _AUTORES_LAST[0] = now
    else:
        # hakuna score mpya — punguza gap ili rufu ijayo iwe karibu
        _AUTORES_LAST[0] = now - _AUTORES_MIN_GAP / 2

# ================= HTTP =================
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).replace("</", "<\\/").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_GET(self):
        p = self.path.split("?")[0]
        force = "force=1" in self.path
        if p == "/api/picks":
            try:
                self._json(get_payload(force=force))
            except Exception as e:
                self._json({"error": str(e)[:200]}, 500)
            return
        if p == "/api/status":
            self._json({"ok": True,
                        "cache_age_s": round(time.time() - CACHE["ts"]) if CACHE["ts"] else None,
                        "cache_ttl_s": CACHE_TTL, "busy": CACHE["busy"],
                        "last_error": CACHE["error"], "counts": CACHE["counts"],
                        "db": os.path.basename(DB_PATH)})
            return
        if p == "/api/sessions":
            auto_results()
            sessions = all_sessions()
            self._json({"ok": True, "sessions": sessions, "global": global_stats(sessions)})
            return
        if p == "/api/stats":
            self._json({"ok": True, "stats": global_stats(all_sessions())})
            return
        if p == "/api/score":
            # Auto results kwa client: final score ya mechi kutoka WC (hakuna key).
            # ?mid=<wc-slug>  →  {"ok":true,"mid":..,"score":"1-6","home":1,"away":6,"finished":true}
            mid = ""
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            for kv in q.split("&"):
                if kv.startswith("mid="):
                    mid = kv[4:]
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,60}", mid or ""):
                self._json({"ok": False, "error": "bad mid"}, 400); return
            try:
                import live_research as LR
                r = LR.wc_result(mid)
                if r:
                    self._json({"ok": True, "mid": mid, "score": "%d-%d" % r,
                                "home": r[0], "away": r[1], "finished": True})
                else:
                    self._json({"ok": True, "mid": mid, "score": None,
                                "finished": False})
            except Exception as e:
                self._json({"ok": False, "mid": mid, "error": str(e)[:150]}, 500)
            return
        # Static: app ni index.html pekee — zingine zote 404
        # (usalama: /server.py, /betting-researcher/..., /.git/ hasitolewi)
        if p in ("/", "/index.html"):
            return super().do_GET()
        self.send_error(404, "Not Found")

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            body = self._body()
        except Exception:
            self._json({"ok": False, "error": "json"}, 400); return
        now = int(time.time() * 1000)
        if p == "/api/sessions":
            s = body.get("session") or {}
            sid = s.get("id") or ("s" + str(now).lower())
            picks = s.get("picks") or []
            with DBLOCK, dbx() as c:
                # started_at daima ms (client hucompute date kutoka hapa)
                st_at = s.get("started_at")
                st_at = int(st_at) if st_at else now
                if st_at < 10**11:  # ikitumika seconds (old data) -> ms
                    st_at *= 1000
                c.execute("INSERT OR REPLACE INTO sessions(id, started_at, cycle, meta, picks) VALUES(?,?,?,?,?)",
                          (sid, st_at, s.get("cycle") or "FLEXIBLE",
                           json.dumps(s.get("meta") or {}), json.dumps(picks)))
            self._json({"ok": True, "id": sid})
            return
        m = re.match(r"^/api/sessions/([A-Za-z0-9_-]+)/score$", p)
        if m:
            sid = m.group(1)
            mid = str(body.get("mid") or "")
            score = str(body.get("score") or "").strip()
            mm = re.match(r"^(\d{1,2})\s*[-:]\s*(\d{1,2})$", score)
            if not mid or not mm:
                self._json({"ok": False, "error": "mid/score"}, 400); return
            with DBLOCK, dbx() as c:
                c.execute("INSERT OR REPLACE INTO results(session_id, mid, score, updated_at) VALUES(?,?,?,?)",
                          (sid, mid, score, now))
            ev = []
            with dbx() as c:
                row = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if row:
                s = merged_session(row)
                for pk in s["picks"]:
                    if pk["mid"] == mid and not pk.get("manual"):
                        ev.append({"pick": pk.get("market"), "status": pk.get("status")})
            self._json({"ok": True, "evaluated": ev})
            return
        m = re.match(r"^/api/sessions/([A-Za-z0-9_-]+)/override$", p)
        if m:
            sid = m.group(1)
            pk = str(body.get("pickKey") or "")
            st = str(body.get("status") or "")
            if st not in ("win", "loss", "void"):
                self._json({"ok": False, "error": "status"}, 400); return
            with DBLOCK, dbx() as c:
                c.execute("INSERT OR REPLACE INTO overrides(session_id, pick_key, status, updated_at) VALUES(?,?,?,?)",
                          (sid, pk, st, now))
            self._json({"ok": True})
            return
        self._json({"ok": False, "error": "route"}, 404)

    def do_DELETE(self):
        m = re.match(r"^/api/sessions/([A-Za-z0-9_-]+)$", self.path.split("?")[0])
        if m:
            with DBLOCK, dbx() as c:
                c.execute("DELETE FROM sessions WHERE id=?", (m.group(1),))
                c.execute("DELETE FROM results WHERE session_id=?", (m.group(1),))
                c.execute("DELETE FROM overrides WHERE session_id=?", (m.group(1),))
            self._json({"ok": True})
            return
        self._json({"ok": False, "error": "route"}, 404)


def main():
    db_init()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"96th Predictions LIVE + DB → http://0.0.0.0:{PORT}  (db: {DB_PATH})")
    srv.serve_forever()

if __name__ == "__main__":
    main()
