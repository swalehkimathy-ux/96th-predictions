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

import analyze_v3 as A          # engine
import build_app as B           # shaping
import fetch_sources as FS      # WC + FP fetch/parse

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

# ================= LIVE DATA =================
def merge_fresh_fw(base_matches):
    fw_path = os.path.join(RAW_DATA_DIR, "fw_latest.json")
    if not os.path.exists(fw_path):
        return 0
    try:
        posts = json.load(open(fw_path, encoding="utf-8"))
    except Exception:
        return 0
    KEYS = {
        "bham-brentford": ("birmingham", "brentford"), "lask-celtic": ("lask", "celtic"),
        "forest-leeds": ("forest", "leeds"), "aek-levski": ("aek", "levski"),
        "bradford-burnley": ("bradford", "burnley"), "celje-slovan": ("celje", "slovan"),
        "lyon-fenerbahce": ("lyon", "fenerbahce"), "newcastle-westbrom": ("newcastle", "brom"),
        "preston-everton": ("preston", "everton"), "rapid-hearts": ("rapid", "midlothian"),
        "realmadrid-sociedad": ("real madrid", "sociedad"), "tottenham-charlton": ("tottenham", "charlton"),
        "viking-dinamo": ("viking", "dinamo"),
    }
    merged = 0
    for m in base_matches:
        kh, ka = KEYS.get(m["id"], (m["home"].lower(), m["away"].lower()))
        for post in posts:
            h = (post.get("home_guess") or "").lower()
            a = (post.get("away_guess") or "").lower()
            if kh in h and ka in a:
                cs = post.get("cs") or ""
                mm = re.search(r"(\d+)\s*-\s*(\d+)", cs)
                tips = [t for t in post.get("tips", []) if t.get("market") in ("result", "over", "under", "btts")]
                m["fw"] = {"tips": tips, "cs": (mm.group(1) + "-" + mm.group(2)) if mm else None,
                           "hot_tip": post.get("hot_tip"), "btts": post.get("btts")}
                merged += 1
                break
    return merged

def build_payload():
    t0 = time.time()
    counts = {}
    try:
        FS.main()
    except Exception as e:
        counts["wc_fp_error"] = str(e)[:200]
    try:
        import scraper_fw
        scraper_fw.main()
    except Exception as e:
        counts["fw_error"] = str(e)[:200]

    raw = json.load(open(os.path.join(RAW_DATA_DIR, "raw.json"), encoding="utf-8"))
    wc = json.load(open(os.path.join(RAW_DATA_DIR, "wc.json"), encoding="utf-8"))
    fp = json.load(open(os.path.join(RAW_DATA_DIR, "fp.json"), encoding="utf-8"))
    try:
        hist = json.load(open(os.path.join(RAW_DATA_DIR, "history.json"), encoding="utf-8"))
    except Exception:
        hist = {}
    n_fw = merge_fresh_fw(raw["matches"])
    counts.update({"matches": len(raw["matches"]), "wc": len(wc), "fp": len(fp),
                   "fw_fresh": n_fw, "built_in_s": round(time.time() - t0, 1)})

    picks, near, failed = A.analyze(raw, wc, fp, hist)
    bp, bn = {}, {}
    for p in picks: bp.setdefault(p["mid"], []).append(B.slim(p))
    for p in near: bn.setdefault(p["mid"], []).append(B.slim(p))

    matches = []
    for m in raw["matches"]:
        matches.append({"id": m["id"], "home": m["home"], "away": m["away"], "comp": m["comp"],
                        "kickoff": B.kickoff_ms(m), "picks": bp.get(m["id"], []), "near": bn.get(m["id"], [])})
    matches.sort(key=lambda x: x["kickoff"])
    return {
        "generated_utc": datetime.datetime.now(datetime.UTC).strftime("%d/%m/%Y %H:%M"),
        "generated_ms": int(time.time() * 1000),
        "live": True, "counts": counts,
        "sources": ["Soko la Bookmakers (30+)", "WinComparator", "Forebet (cached)",
                    "Whispers", "FootballPredictions"],
        "matches": matches,
    }

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
        return payload
    except Exception as e:
        with PLOCK:
            CACHE["error"] = str(e)[:300]; CACHE["busy"] = False
        if CACHE["payload"]:
            return CACHE["payload"]
        raise

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
        if p == "/api/picks":
            try:
                self._json(get_payload())
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
