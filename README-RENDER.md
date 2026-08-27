# 96th Predictions — Deploy kwenye RENDER (free tier)

Hapa chini ni hatua kwa hatua. Mchango: ~10–15 min baada ya kuundua akaunti.

## Kabla ya kuanza
- Akaunti ya **GitHub** (bure) — github.com
- Akaunti ya **Render** (bure) — render.com → *Sign up* (unaweza "Continue with GitHub")

## HATUA 1 — Push repo kwenye GitHub

Folder `96th-render-repo/` iko tayari (server + app + pipeline + render.yaml).

```bash
cd 96th-render-repo
git init
git add .
git commit -m "96th Predictions — live server + app"
```
1. GitHub → **New repository** → jina: `96th-predictions` → **Create** (bure, public au private — vyote vinafanya kazi kwa free tier)
2. GitHub itakuonyesha amri za kwanza za push — zifanyie:
```bash
git branch -M main
git remote add origin https://github.com/USERNAME/96th-predictions.git
git push -u origin main
```

## HATUA 2 — Deploy kwenye Render

1. Render.com → **Log in** (GitHub)
2. Kisha: **New → Blueprint** (Render itatambua `render.yaml` yako yenyewe)
3. Chagua repo `96th-predictions` → **Create Blueprint**
4. Itabaki na deploy (Python runtime, free plan) — subiri ~2 min
5. Ukimaliza: itakuwa na URL kama `https://96th-predictions.onrender.com`

**Thibitisha:** funua URL hiyo kwenye browser — unapata app 96th Predictions (LIVE badge).

## HATUA 3 — Kuunganisha APP (web au Android) na server

1. Kwenye `96th-predictions/template.html`, umbali juu:
   ```js
   var API_URL = "https://96th-predictions.onrender.com";   // ← URL yako
   ```
2. Kwenye `96th-predictions/capacitor.config.json` → `server.allowNavigation` ongeza domain:
   ```json
   "allowNavigation": ["96th-predictions.onrender.com", "localhost", "127.0.0.1"]
   ```
3. Rebuild + sync:
   ```bash
   cd 96th-predictions
   npm run dist
   ```
4. Web: `index.html` sasa itaendelea na server yako.
   Android: Build APK tena (Android Studio → Build APK).

## HATUA 4 — Baada ya kubadilisha code

Kila unapobadilisha app/pipeline:
```bash
bash 96th-predictions/deploy/sync-repo.sh   # huhamisha faili mpya kwenye 96th-render-repo
cd 96th-render-repo && git add . && git commit -m "update" && git push
```
Render **ita-deploy yenyewe** ikiona push mpya (auto-deploy).

## Kumbuka kwa free tier (ujue kabla)

| Kitu | Hali kwenye free |
|---|---|
| Cold start | Baada ya ~15 min isiyo tumiwa, Render hulaiwa; request ya kwanza inachukua **30–60s** (app inasubiri hadi 60s — inafanya kazi) |
| **Database** | /tmp **si persistent** — records zinauwa baada ya redeploy/spin-down. Kwa sasa hii ni bora kuanzia; baadaye (ikaanza kuzingatia records): (a) Render paid + persistent disk (~$7/mo), au (b) badilisha SQLite → Neon/Supabase free Postgres (nitaifanya) |
| Data live | Inapokea request tu (fetch-on-demand) — haitumii CPU bila mtu |
| URL | `*.onrender.com` ni bure; custom domain inahitaji paid |

## Troubleshooting

- **502 Bad Gateway** → check Render logs; hakikisha `startCommand` inafanya kazi (python3 server.py)
- **404 /api/picks** → hakikisha `P96_PIPELINE_DIR` inaelekea folder halisi (repo root: `betting-researcher/`)
- **App inasema OFFLINE** → URL ya API_URL si sahihi, au cold start ilitokea tena (subiri sekunde 45 na refresh)

## Auto Results — v7.3: BILA API KEY (WinComparator final scores)

**Habari njema: hakuna key tena inayohitajika.** Scores za mechi ziliyomalizika sasa huzajika **kifupi**
kutoka **page ya mechi ya WinComparator** (nchi inayotumika tayari kwa research — data halisi, bure, bila quota).

**Jinsi inavyofanya kazi:**
1. Pick yoyote inapopita `kickoff + 120 min` na bado iwe "pending", server huenda kwenye
   page ya mechi hiyo (`mid` = slug ya WC) na kuangalia final score.
2. Ikiwa mechi imeendelea (`End` marker + score block), score inahifadhiwa (`1-6`) na
   status hupimwa yenyewe (win/loss) — **History tab inajaa yenyewe baada ya siku kupinduka**.
3. Limits (kuokoa CPU/bandwidth): max 15 scores kwa kurutubishwa moja · kurutubishwa kimoja
   kila **dakika 15** (`P96_AUTORES_GAP`, default 900s) · score iliyothibitishwa hifadhiwa kwa masaa 6 ·
   mechi isiyomalizika huchekwa tena baada ya 15 min.

**The Odds API sasa ni FALLBACK tu (hiari):** ikiwa umeweka `ODDS_API_KEY` kwenye Render
(Environment), picks ambazo slug ya WC haifanyi kazi zitatibiwa kupitia Odds API
(free tier: 500 req/mwezi, events cached kwa dakika 30). Bila key — **kila kitu bado kinafanya kazi**.

## v7.3 — Daily mode + UI + Auto results bila key

**Kanuni mpya ya bet (kwa user):**
- **Siku kamili, si saa 14:** mechi ZOTE za siku (GMT+3) ambazo bado hazianza huchunguzwa kila siku —
  siku ipipindukia, research mpya huanza kiotomatiki (cache inakunjika kwa tarehe).
- **Accumulator:** legs makuu = picks zenye **conf ≥ 80%**; total odds lazima iwe **1.6 – 3.0**;
  legs ≤ 10. Ikiwa 80%+ hazitoshi kufikia 1.6, inajumlisha picks 70–80% (inakiuka 1.6 →
  app huonyesha onyo dhahiri, hakuficha).
- **UI:** research bar (siku, mechi zilizochunguzwa, picks), total-odds gauge (green zone 1.6–3.0),
  status ya kila leg, History inayojaa yenyewe (legs x/y, per-market W/L, streak).
- **Hakuna mock/demo data:** embedded payload ni tupu — app inaonyesha tu real data (au inasema
  OFFLINE na kutorudi tena).
- **Auto results bila key** (angalia sehemu hapo juu): WC final scores → History inajaa yenyewe
  ndani ya ~15 min baada ya mechi kumalizika.

## Vilevile (changes in v7.2)

- `server.py` — fixed: default `P96_PIPELINE_DIR` path ilipenda `../betting-researcher` (nje ya repo) — sasa inaelekea `betting-researcher/` ndani ya repo, hivyo `python3 server.py` inafanya kazi bila env.
- `server.py` — auto results: ilianza kucheka split kwa en-dash (`–`) tu; live picks hutumia `-`. Sasa inacheka `-`, `–`, `vs`.
- `server.py` — SQLite: connections sasa hufungwa kila wakati (hakuna leak).
- `server.py` — static: `/` na `/index.html` pekee zinapatikana; source/data/.git zimeguzwa (security).
- `server.py` — `get_payload`: hakuna zaidi ya 1 build wakati mmoja (concurrent requests zinasubiri).
- `live_research.py` — `_wc_to_utc_ms`: ilikuwa inatumia `+7h` hardcoded (PDT tu). Sasa inatumia `zoneinfo America/Los_Angeles` (DST sahihi) + inaenda kupita mwaka (Dec→Jan) + inakubali month abbreviations (Jan, Aug...).
- `build_app.py` — ilikuwa inatumia `/home/user/betting-researcher` hardcoded; sasa inatumia relative path.
- `live_research.py`/`build_app.py` — `datetime.utcnow()` (deprecated) → `datetime.now(UTC)`.
