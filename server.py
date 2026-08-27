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
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

BASE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.environ.get("P96_PIPELINE_DIR", os.path.normpath(os.path.join(BASE, "..", "betting-researcher")))
DB_PATH = os.environ.get("P96_DB_PATH", os.path.join(BASE, "data", "records.db"))
PORT = int(os.environ.get("P96_PORT") or os.environ.get("PORT") or "8030")
sys.path.insert(0, PIPELINE)
sys.path.insert(0, BASE)

import analyze_v3 as A          # maths (poisson) kwa live_research

CACHE_TTL = 15 * 60
CACHE = {"ts": 0.0, "payload": None, "busy": False, "error": None, "counts": {}}
PLOCK = threading.Lock()
RAW_DATA_DIR = os.path.join(PIPELINE, "data")

# ================= DATABASE (SQLite) =================
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
DBLOCK = threading.Lock()

def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def db_init():
    with DBLOCK, db() as c:
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
    with db() as c:
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
    w = sum(1 for p in picks if p["status"] == "win")
    l = sum(1 for p in picks if p["status"] == "loss")
    s = {
        "id": row["id"], "started_at": row["started_at"], "cycle": row["cycle"] or "FLEXIBLE",
        "meta": json.loads(row["meta"] or "{}"), "picks": picks,
        "stats": {"w": w, "l": l, "d": w + l, "rate": (w / (w + l)) if (w + l) else None},
    }
    return s

def all_sessions():
    with db() as c:
        rows = c.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
    return [merged_session(r) for r in rows]

def global_stats(sessions):
    w = sum(s["stats"]["w"] for s in sessions)
    l = sum(s["stats"]["l"] for s in sessions)
    pend = sum(1 for s in sessions for p in s["picks"] if p["status"] == "pending")
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

# ================= LIVE DATA (engine v5: live discovery + consensus) =================
def build_payload():
    t0 = time.time()
    try:
        import live_research as LR
        payload = LR.get_picks(max_age_h=14, max_research=40)
        counts = {"matches": len(payload.get("matches", [])),
                  "researched": payload.get("researched"),
                  "matches_total": payload.get("matches_total"),
                  "built_in_s": round(time.time() - t0, 1)}
    except Exception as e:
        payload = {"generated_ms": int(time.time() * 1000), "live": False, "matches": []}
        counts = {"error": str(e)[:200], "built_in_s": round(time.time() - t0, 1)}
    payload["generated_utc"] = datetime.datetime.now(datetime.UTC).strftime("%d/%m/%Y %H:%M")
    payload["generated_ms"] = int(time.time() * 1000)
    payload["counts"] = counts
    return payload

def get_payload(force=False):
    with PLOCK:
        if not force and CACHE["payload"] and (time.time() - CACHE["ts"]) < CACHE_TTL:
            return CACHE["payload"]
        if CACHE["busy"] and CACHE["payload"]:
            return CACHE["payload"]
        CACHE["busy"] = True
    try:
        payload = build_payload()
        with PLOCK:
            CACHE.update(payload=payload, ts=time.time(), counts=payload["counts"], error=None, busy=False)
        auto_results()
        return payload
    except Exception as e:
        with PLOCK:
            CACHE["error"] = str(e)[:300]; CACHE["busy"] = False
        if CACHE["payload"]:
            return CACHE["payload"]
        raise

# ================= AUTO RESULTS (The Odds API - free tier) =================
import urllib.request
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
_EVENTS_CACHE = {"ts": 0.0, "data": {}}
_ALIAS = {"hearts": {"midlothian"}, "spurs": {"tottenham"}, "wolves": {"wolverhampton"}}

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
    if now - _EVENTS_CACHE["ts"] < 600 and sport in _EVENTS_CACHE["data"]:
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
    """Hujaza scores za mechi zilizomalizika kwenye picks bado 'pending' (auto)."""
    if not ODDS_API_KEY:
        return
    sports = {}
    now_ms = time.time() * 1000
    with db() as c:
        for row in c.execute("SELECT id, picks FROM sessions").fetchall():
            for p in json.loads(row["picks"] or "[]"):
                if p.get("manual"):
                    continue
                if now_ms < (p.get("kickoff") or 0) + 120 * 60000:
                    continue  # mechi bado hai/ya hivi karibuni
                sport = sport_for_comp(p.get("comp"))
                if sport:
                    sports.setdefault(sport, []).append((row["id"], p))
    if not sports:
        return
    with DBLOCK, db() as c:
        for sport, items in sports.items():
            events = _odds_events(sport)
            for sid, p in items:
                names = (p.get("match") or "").split(" \u2013 ")
                if len(names) != 2:
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
                    direct = _same_team(names[0], eh) and _same_team(names[1], ea)
                    swap = _same_team(names[0], ea) and _same_team(names[1], eh)
                    if not (direct or swap):
                        continue
                    h, a = (ev["home_score"], ev["away_score"]) if direct else (ev["away_score"], ev["home_score"])
                    c.execute("INSERT OR IGNORE INTO results(session_id, mid, score, updated_at) VALUES(?,?,?,?)",
                              (sid, p["mid"], "%d-%d" % (h, a), int(now_ms)))
                    break

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
        super().do_GET()

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
            with DBLOCK, db() as c:
                c.execute("INSERT OR REPLACE INTO sessions(id, started_at, cycle, meta, picks) VALUES(?,?,?,?,?)",
                          (sid, int(s.get("started_at") or now / 1000), s.get("cycle") or "FLEXIBLE",
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
            with DBLOCK, db() as c:
                c.execute("INSERT OR REPLACE INTO results(session_id, mid, score, updated_at) VALUES(?,?,?,?)",
                          (sid, mid, score, now))
            ev = []
            with db() as c:
                row = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if row:
                s = merged_session(row)
                for pk in s["picks"]:
                    if pk["mid"] == mid and not pk.get("manual"):
                        ev.append({"pick": pk.get("market"), "status": pk["status"]})
            self._json({"ok": True, "evaluated": ev})
            return
        m = re.match(r"^/api/sessions/([A-Za-z0-9_-]+)/override$", p)
        if m:
            sid = m.group(1)
            pk = str(body.get("pickKey") or "")
            st = str(body.get("status") or "")
            if st not in ("win", "loss", "void"):
                self._json({"ok": False, "error": "status"}, 400); return
            with DBLOCK, db() as c:
                c.execute("INSERT OR REPLACE INTO overrides(session_id, pick_key, status, updated_at) VALUES(?,?,?,?)",
                          (sid, pk, st, now))
            self._json({"ok": True})
            return
        self._json({"ok": False, "error": "route"}, 404)

    def do_DELETE(self):
        m = re.match(r"^/api/sessions/([A-Za-z0-9_-]+)$", self.path.split("?")[0])
        if m:
            with DBLOCK, db() as c:
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
