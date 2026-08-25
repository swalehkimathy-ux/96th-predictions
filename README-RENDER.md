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
