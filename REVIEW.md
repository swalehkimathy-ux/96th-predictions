# 96th Predictions — Code Review (27 Aug 2026)

Repo: `swalehkimathy-ux/96th-predictions` · v7.1 → marekebisho ya v7.2

---

## 1. Muhtasari wa System

| Falaki | Kazi |
|---|---|
| `server.py` | HTTP server (stdlib pekee) + SQLite. Inatoa app (`/`), `/api/picks` (live engine, cache 15min), `/api/sessions*` (records), auto results via The Odds API. |
| `live_research.py` | **Engine v5**: inagundua mechi LIVE kutoka WinComparator league pages (10 ligu), kisha kila mechi inachunguzwa kwa vyanzo 5 (Soko 30+ bookmakers, WC Model, FootballPredictions, Football Whispers, Forebet cache). Pick inapita ipele vyanzo 4+ na conf ≥ 70%. |
| `analyze_v3.py` | Engine ya zamani ya offline (history/form/H2H) — inatumika na `build_app.py` tu. |
| `index.html` | App ya wateja (PWA-style): localStorage records, START BET, auto-sync kila 60s. |
| `render.yaml` | Deploy kwenye Render free tier. |

**Ushinikizo wa review:** engine ya live, DB/records, usalama, na utendaji kwenye Render free tier.

---

## 2. BUGS — ZILIZOGUNDULIWA NA KUREKEBISHWA

### 🔴 B1 — `GET /api/sessions` ilikuwa 500 (CRITICAL, existing)
`merged_session()` ilikuwa ikifanya `p["status"]` — lakini picks zinazotumwa na client **hazina** field ya `status` hadi mechi kumalizika (client huiweka tu kwenye localStorage baada ya score). Hivyo:

- Kila session mpya (hii "siku ya kwanza" baada ya START BET) → `KeyError: 'status'` → **500**.
- Client inagesha kwa `.catch(function(){})` — hivyo **auto-sync haikupita kabisa** na hakuna aliyeelewa (silent failure).
- `POST /score` pia ilipoteza response yake (DB imetolewa, lakini `evaluated` response ilikufa).

**Fix:** `p.setdefault("status", "pending")` + `p.get("status")` kila mahali. Imethibitishwa kwa test: session mpya → 200, picks `pending`, score → `{"ok":true,"evaluated":[...]}`.

### 🔴 B2 — Default path ya `PIPELINE` ilikosa (server haichami bila env)
`os.path.join(BASE, "..", "betting-researcher")` = folder **nye** ya repo. `python3 server.py` ilikufa `ModuleNotFoundError`. (Kwenye Render haionekani kwa sababu `render.yaml` inapeleka env sahihi.)

**Fix:** default → `BASE/betting-researcher`. Import ya `analyze_v3` sasa iko ndani ya try/except (server huanze hata bila pipeline).

### 🔴 B3 — Auto results haikupokea live picks kabisa
`auto_results()` iligawanya jina la mechi kwa **en-dash** (`" – "`), lakini live engine (`build_picks`) hutumia **hyphen** (`" - "`). Hivyo `len(names) != 2` → kila pick ilipoteleka → **auto-score sync haifanyi kazi kwa picks za live** (nilithibitisha kwa unit test: baada ya fix, `-`, `–`, `vs` zote zinapokea, pamoja na teams zilizobadilishwa kwa mpangilio).

**Fix:** helper `_split_match()` inayokubala ` - `, ` – `, ` vs `.

### 🟠 B4 — Saa za kickoff: DST ilihardcode (PDT tu)
`_wc_to_utc_ms()` iliongeza `+7h` daima. Katika Novemba–Machi (US Pacific = PST, UTC−8), **mechi zote zilitakuwa off kwa saa 1** — na hivyo filter ya "upcoming" na guard ya kickoff (120min) zingekuwa na hatua.

**Fix:** `zoneinfo.ZoneInfo("America/Los_Angeles")` (fallback: heuristics ya DST — 2nd Sunday March → 1st Sunday November). Imethibitishwa kwa test (Mar 7 vs Mar 9 2026, Dec 2026, rollover).

### 🟠 B5 — Mwaka ukizunguka (Dec → Jan)
Wakati ulikutwa `datetime.utcnow().year` pekee — mechi za "January" zinazoonekana mapajuli zingehesabiwa mwaka huu (siku zilizopita) na kutotemwa.

**Fix:** jaribu mwaka huu, kisha +1; kupokea tu ikiwa iko siku zijao (au ndani ya siku 2 zilizopita). Pia month sasa inakubali short forms (`Jan`, `Aug`, ...) na `datetime.utcnow()` (deprecated) imebadilishwa kwa `now(UTC)`.

### 🟠 B6 — Double build ya payload
Rufu mbili za pamoja (client inapiga `/api/picks` kila 2min + refresh butoni) zingeweza kuanzisha **build mbili pamoja** (research 40 mechi × fetch za nje) — load nje kwa mada.

**Fix:** `threading.Condition` — rufu ya pili inasubiri (hadi 60s) badala ya kupinga.

### 🟡 B7 — SQLite connection leak
`with db() as c` (sqlite3) inafanya commit/rollback **isichafungwi** — kila request iliongeza connection hadi GC.

**Fix:** `dbx()` contextmanager — commit/rollback + `close()` kila wakati.

### 🟡 B8 — `build_app.py` inatumia path hardcoded
`sys.path.insert(0, "/home/user/betting-researcher")` — haiendi kwenye kompyuta nyingine/Render.

**Fix:** relative path kutoka `BASE`.

### 🟡 B9 — Quota ya Odds API (free tier = 500 req/mwezi)
Client inapiga `/api/sessions` kila **60s**; kila rufu ilikuta `auto_results()` → ikikuna key, sports hadi 7 × kila 10min ≈ **1000+ req/mo** → quota inang'ata ndani ya wiki.

**Fix:** (a) min-gap ya sekunde 1800 kati ya auto-results (env `P96_AUTORES_GAP`), (b) events cache 30min, (c) gap inapunguzwa nusu ikiwa hakuna score mpya (ili kukita haraka baada ya mechi kumalizika), (d) score iliyopo (manual au auto) haitaubaliwi.

### 🟡 B10 — `auto_results` haikuwa na ulinzi
Ilikitwa ndani ya `do_GET` bila try/except — kosa lolote lingekuwa 500 kwa request yote.

**Fix:** wrapper ya try/except + log; best-effort tu.

---

## 3. USALAMA (ZIMEFANYIWA)

| # | Tatizo | Hali |
|---|---|---|
| S1 | Server ilikuwa ikitoa **faili yoyote** ya repo: `/server.py`, `/template.html`, `/build_app.py`, `/betting-researcher/data/*.json`, na hata `/.git/config` (ikiwa inakuwepo, inaweza kutoa secrets) | ✅ Sasa: `/` na `/index.html` pekee; zingine zote 404 (imepitishwa kwa test) |
| S2 | API endpoints (POST/DELETE sessions) hazina auth — mtu yeyote anayejua URL anaweza kufuta/kuongeza records | ⚠️ Bado (app ni ndogo ya binafsi; unaweza kuongeza `X-96TH-KEY` header secret baadaye — nimeandika hii katika §5) |
| S3 | SQL injection | ✅ Parameterized queries kila mahali |
| S4 | XSS kwenye JSON | ✅ `_json()` inafunga `</`; client inapesha kwa `esc()` |
| S5 | Body size ya POST haijingazwi (DoS mdogo) | ⚠️ Bado — si hatari kubwa kwa traffic ya pumziki; unaweza kuongeza `Content-Length` cap |

---

## 4. OPERATIONAL (Render free tier) — JUIE

1. **Records haina storage ya kudumu.** `P96_DB_PATH=/tmp/96th/records.db` — `/tmp` hubadilishwe kila deploy/restart/spin-down. Hivyo **history ya win/loss inaanguka** kila unaposanya push. (README inaelezea hii; chochote badala: Render paid disk, au Neon/Supabase free Postgres — niko tayari kufanya baadaye.)
2. **Cold start 30–60s** baada ya spin-down (15min isiyo tumiwa). Client tayari ina timeout 100s — inafanya kazi.
3. **`ODDS_API_KEY` haiupo kwenye `render.yaml`** — ikiwa unataka auto results, ongeza kwenye Render dashboard (taarifa katika `README-RENDER.md` §"Auto Results"). Bila key, scores huzajika manual tu (default sasa).
4. **URL ya app:** `index.html` ina `API_URL = "https://nine6th-predictions.onrender.com"` — hakikisha inaolingana na service name halisi kwenye Render (ikiwa umeibadilisha).
5. **Parser fragility:** WC/FP/FW zinaandikwa kwa regex juu ya HTML ya public sites — zitaanguka kwenye layout change. Engine tayari inafanya kazi (soko 4 vyanzo vya kutosha kwa `n>=4`), lakini ukiangalia picks zimepungua ghafla, anza kuanza kuanza kwenye parser.
6. **`fetch_sources.py` / `fetch_wc_be.py`** zina slugs hardcoded kwa mechi za Ag 25–26 — hizi ni scripts za *offline research* (zimetumiwa v1–v3). Si hatari kwa LIVE app (engine v5 inatumia live_research.py tu), lakini zimebaki kwenye repo — unaweza kuzibuka kwenye folder `legacy/` ili kuweza kutofautisha.

---

## 5. MAPENDEKEZO YA KWA AJILI (v8+)

1. **Session integrity:** `INSERT OR REPLACE INTO sessions` inabadilisha picks lakini inabaki na results/overrides za zamani — badilisha: kama picks zimebadilika, safisha results/overrides za mids zisizo bado ndani.
2. **Auth nzito kidogo:** `X-96TH-KEY` env + header check (mimea 5) ili kuzuia mtu wa nje kutumia/kufuta records.
3. **Postgres badala ya SQLite** (kama history itaanza kushangaza) — interface ya `dbx()` tayari imebaki rahisi kubadilisha.
4. **Health check ya parser:** `/api/status` iweze kuonyesha vyanzo vilivyofanya kazi (nawili FC kati ya 5) ili uweze kuona kwa haraka kama source imefeli.
5. **Cache ya score:** `results` iweze kuwa na `source` ('odds-api' vs 'manual') — sasa `INSERT OR IGNORE`/guard imeko, lakini kumbukumbu ya source itasaidia debug.
6. **`picks` za soko lenye odds ≥ 1.5:** accumulator sasa inachukua picks hadi 8 @ total ≤ 3.0 — unaweza kuongeza kiwango cha chini (mfano total ≥ 1.5) kama ilivyokuwa v1 (`build_combos` ilikuwa na `prod < 1.5` top-up logic).

---

## 6. JINSI ZIMETESTIWA

- **Unit:** `_wc_to_utc_ms` (DST PDT/PST, rollover, month abbrev, unknown month), `_us_dst_active` (boundaries 2026/2027), `_split_match` (3 separators), `auto_results` kwa mock Odds API (hyphen/en-dash/vs, swapped teams, rate-limit, manual score preserved, unexpected error swallowed), `eval_pick` (O15/BTTS/DC1X).
- **E2E HTTP:** status, root 200, security 404s (server.py, template.html, .git/config, betting-researcher/), POST/GET/DELETE sessions, score + override, validation 400s, session mpya bila `status` (B1), cache ya picks (2nd call 25ms), live engine (36 mechi → 4 picks, 5.7s).

**Matokeo: yote yamepita. Server iko tayari kuitumika.**

---

# v7.3 — Daily mode, UI, auto results bila key (27/08/2026)

## Changamoto (requirements za user)

1. **Deploy kupitia repository** (Render auto-deploys from GitHub) — si manual.
2. **UI bora.**
3. **Kila siku mpya → research mpya ya siku hiyo hiyo** → prediction ya siku hiyo.
4. **Ondoa mockups zote** — real data tu.
5. **Tips kwa tarehe maalum** (siku kamili), si window ya saa 14: uanze siku (mf. saa 6) →
   mechi ZOTE za siku hiyo → accumulator.
6. **NB: total odds ya accumulator: ≥ 1.6 na ≤ 3.0.**
7. **History tab ijaye yenyewe:** siku ikipinduka, results za accumulator ya siku iliyopita
   zichekiwe, win/loss zijazwe, win rate ionekane kama **"x out of 1–10"** (legs zilizokita).
8. **Accumulator iundwe kutoka picks zenye certainty > 80%.**
9. **App iwe LIVE pale inapofunguliwa** — research halisi, si embedded fallback.

## Maamuzi ya uhandisi

- **Siku = calendar day GMT+3** (Tanzania). Discovery sasa huchuja `now < kickoff ≤ end_of_day(GMT+3)`.
- **Accumulator:** pool mkuu = `conf ≥ 0.80`; ikiwa total < 1.6 → top-up kutoka 0.70–0.80
  (inakiuka 1.6 → `min_met=false`, UI huonyesha onyo dhahiri). Total 1.6–3.0 · legs ≤ 10 · 1 pick/match.
- **Auto results BILA API key:** `wc_result(slug)` hucheka **final score kutoka page ya mechi ya
  WC** (marker `match.event.over` = "End" + score block). Verified kwa mechi halisi
  (Birmingham–Brentford 1-6, 25/08). Odds API sasa ni fallback tu (ikiwa key ipo).
- **Cache ya day:** cache ya payload inakunjika inapobadilika siku GMT+3; `?force=1` inaburuta
  research upya; client inapiga `force=1` ikibadilika tarehe.
- **Client:** inapendelea `DATA.acc` iliyohesabiwa na server (source ya kweli 1); fallback ya
  local (offline) hutumia kanuni sawa. `fetchLive` inakubali payload halisi lenye picks 0
  (siku ya kimya ≠ "data incomplete").
- **Embedded payload ni tupu** (`live:false`) — hakuna data ya demo. App inasema OFFLINE na
  kutorudi tena kila min 2 / 60s.

## Test evidence

- **Unit (12/12):** `day_ms_range` (GMT+3); `build_accumulator` — A (primary-only 1.72),
  B (top-up 1.71), C (fallback-only 1.69), C2 (honest min_met=False @1.58), D (single leg),
  E (cap 3.0), F (empty), G (1-per-match), H (10-legs cap 2.59), I (conf-desc order);
  `wc_result` real final score (1,6) + invalid slug → None.
- **E2E HTTP (server halisi, port 8030, fresh DB):**
  - `/api/picks` live: **siku 2026-08-27, mechi 35 zote zimechunguzwa, 3 picks, ACC 1.89
    (min_met=true, 3 legs)** — 5.4s cold, ~0.1s cached.
  - `?force=1` → rebuild · 2nd call → cached.
  - **Day-rollover invalidation PASS** (fake next-day → cache inaburudika, new build).
  - **Auto results E2E (no key):** session iliyoweka mechi iliyomalizika (slug ya WC) →
    GET /api/sessions → score `1-6` imejaa **yenyewe**, status `win` imepimwa yenyewe.
  - Mock imeondoka: embedded payload tupu (verified kwa curl).
- **Frontend smoke (node + fake DOM + payload halisi):** today rendered (research bar, ACC hero,
  gauge, 3 legs = server acc), history rendered (legs-hit stat, win rate), no mock data. PASS.

## Zinazobaki / zilizofafanuliwa kwa user

- Ikiwa siku haina pick yoyote ya conf ≥ 80%, accumulator hutumia picks 70–80% zilizopo
  (kila conf inaonekana UI). Siku ile ile 80%+ zipo, ndizo zinaingia kwanza. (Policy — inaweza kushongwa.)
- Forebet source: inahitaji raw data ya siku hiyo (browser) — katika live mode huchangia tu
  ikiwa raw.json imepimwa siku ile ile; sources 4 nyingine (Soko/WC/FP/FW) zinaendelea kushika N_MIN=4.
- Deploy: commit → push → Render auto-deploy (branch inayofuatiwa).

---

# v7.4 — Marekebisho ya UI ya siku + auto results ya client (27/08/2026, jioni)

## Tatizo lililotajwa na user (screenshots)

1. **BET YA LEO ilionyesha fixtures za zamani** (Viking–Dinamo, Real Madrid–Sociedad, Bradford–Burnley,
   Rapid–Hearts, Tottenham–Charlton) ambazo si za siku ile — session ya localStorage ililokwa
   "lock" na picks za awali (pamoja na W manual 2), hivyo data mpya ya server haikibadilisha hero.
2. **History ilionyesha manual input** — auto-results haikujaa sababu DB ya server ilipotea
   kwenye deploy (Render /tmp ephemeral) na client haikutafuti scores yenyewe.
3. **"19/NaN" kwenye research bar** — bug: `/` ya ziada baada ya quote ilifanya JS ikiche
   string ÷ number = NaN: `' <span class="sub2">('/ + d.matches_total` →
   `' <span class="sub2">(' / (+d.matches_total)` → NaN.

## Marekebisho

- **BET YA LEO = LIVE research** (`DATA.acc`) — si session iliyolokwa. Session ya awali bado
  iko kwenye HISTORY (snapshot ya "combo iliyotolewa siku hiyo"). Ikiwa tofauti, hero inaonyesha
  maelezo madogo (ⓘ).
- **MECHI ZA LEO** — sehemu mpya: fixtures ZOTE za siku zilizochunguzwa (payload `all_fixtures`),
  za kujibadilisha kiotomatiki siku ikipinduka; mechi zenye pick zina badge.
- **Auto results ya client**: endpoint mpya `GET /api/score?mid=<wc-slug>` (WC final scores,
  cache: finished 6h, pending 15min, validation ya slug). Client inaita kwa kila pick
  (siku hii + zile zamani) iliyopita kickoff+120min na bado pending — **hufanya kazi hata
   ikiwa DB ya server imetoweka** (deploy). Manual (W/L/V, score) sasa imelemba kama BACKUP.
- **History**: button ya kufuta siku (cleanup), legs-hit x/y, AUTO/MANUAL tags.
- **NaN**: imeondolewa (`' <span class="sub2">(' + d.matches_total`).

## Test evidence

- `/api/score`: mechi iliyomalizika → `1-6` finished · slug si sahihi → 400 · mechi ijayo → pending.
- `/api/picks`: `all_fixtures` 17 (UEL/UECL 27/08, 18:00 UTC) + picks 2, ACC 1.47 min_met=False (onyo dhahiri).
- Node smoke (scenario ya user: picks ya jana, 1 W manual + 1 pending): **9/9 PASS** —
  resbar bila NaN, LIVE acc 1.47 + onyo, fixtures 17 + badges, autoScorePicks ilipiga
  /api/score, score `1-6` imejaa yenyewe (status win, AUTO), manual W imehifadhiwa,
  history baada ya tab switch: "2/2 LEGS ZIMEITA" + AUTO tag + delete + backup label.
