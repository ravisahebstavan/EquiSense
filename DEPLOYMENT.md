# Deploying EquiSense — free, end to end: Neon + Vercel

Exactly two external services, both free, no credit card:

```
┌─────────────────────────────────┐      ┌──────────────────────────────┐
│  VERCEL (Hobby, free)           │      │  NEON (free Postgres)        │
│  FastAPI + workstation UI as a  │──────│  0.5 GB · no expiry          │
│  serverless function            │      │  prices · filings · macro    │
│  + Vercel Cron (daily refresh)  │      │  base rates · LEDGER · VAULT │
└───────────────┬─────────────────┘      └──────────────────────────────┘
                │ outbound HTTPS
        Yahoo Finance (keyless)     optional: Anthropic API (AI narration)
```

Why this split: Vercel's filesystem is ephemeral and read-only, but
EquiSense's soul is persistent state — the market DB, the hash-chained
decision ledger, the raw vault. With `DATABASE_URL` set, **all of it lives in
Neon** (`EQUISENSE_STORAGE=db` engages automatically); the Vercel side is
stateless and disposable. Local development is unchanged (SQLite + files).

Serverless adaptations already in the code:
- **Bootstrap without background threads**: the first click of ⟳ Refresh on
  an empty database runs the full 10-year ingest as a streamed, chunked,
  per-chunk-committed pipeline. If the function time limit interrupts it,
  nothing is lost — run Refresh again and it resumes where it stopped.
- **Demo seed is hard-gated off hosted databases** — Neon only ever holds
  live data.
- **Auth**: everything is behind your `EQUISENSE_ACCESS_TOKEN`
  (login page sets a 90-day cookie; APIs accept `Authorization: Bearer`).
- **Cron**: Vercel Cron hits `GET /api/cron/refresh` weekdays 19:00 IST
  (configured in `vercel.json`); Vercel authenticates it by sending
  `Authorization: Bearer $CRON_SECRET`.

---

## YOUR TASKS (one-time, ~15 minutes)

### 1 — Push the repo to GitHub
```bash
cd /workspaces/EquiSense
git add -A && git commit -m "EquiSense"
# create a repo at github.com (private is fine), then:
git remote add origin https://github.com/<you>/EquiSense.git
git push -u origin main
```
Before pushing, confirm nothing personal is staged: `git status` must show
no `data/`, no `.env`.

### 2 — Create the free database (Neon)
1. Sign up at **neon.tech** → create a project
   (region **AWS ap-southeast-1, Singapore** — closest to India).
2. On the project dashboard, copy the **connection string**
   (`postgresql://user:pass@ep-….neon.tech/neondb?sslmode=require`).

### 3 — Deploy on Vercel
1. Sign up at **vercel.com** with your GitHub account (Hobby plan, free).
2. **Add New → Project** → import your `EquiSense` repo. Vercel reads
   `vercel.json` and `requirements.txt` automatically — no build settings
   to change.
3. Before clicking Deploy, add **Environment Variables**:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string from step 2 |
   | `EQUISENSE_ACCESS_TOKEN` | a strong secret you choose — this is your login |
   | `CRON_SECRET` | **the same value** as `EQUISENSE_ACCESS_TOKEN` (lets Vercel Cron through the auth gate) |
   | `ANTHROPIC_API_KEY` | *(optional)* enables AI narration; omit to run without |

4. Deploy.

### 4 — First boot (the one-time bootstrap)
1. Open `https://<your-project>.vercel.app` → enter your access token.
2. Click **⟳ Refresh** (top right). The drawer streams the full bootstrap:
   universe → 10y prices → macro → fundamentals (in chunks of 10 companies)
   → validating → running hypotheses → scoring → publishing.
   Takes ~3–5 minutes. **If the stream stops early** (serverless time limit),
   simply click Refresh again — it resumes; already-ingested data is never
   re-downloaded.
3. Check **Lab → Data Health**: quality score should be green.

### 5 — Verify the cron (optional)
Vercel dashboard → your project → **Settings → Cron Jobs**: you should see
`/api/cron/refresh` at `30 13 * * 1-5` (19:00 IST, weekdays). It refreshes
prices/macro, recomputes base rates, and scores any due claims daily.

### 6 — Ongoing
- Redeploy = `git push` (Vercel auto-deploys `main`).
- Secrets live **only** in Vercel's dashboard — never in the repo.
- Neon dashboard shows storage; this universe uses a small fraction of the
  free 0.5 GB.

---

## Known constraints (stated, not hidden)

| Constraint | Effect | Handling |
|---|---|---|
| Serverless function time limit (Hobby: up to 300s with fluid compute; some accounts 60s) | First bootstrap may need 2–3 Refresh clicks on a 60s limit | Pipeline is chunk-committed and resumable by design |
| Cold starts (function idle) | First request after idle takes a few seconds | Nothing is lost; Neon auto-wakes similarly (~1s) |
| Yahoo may throttle cloud IPs | Occasional refresh failures | Failures surface in the drawer; retry; adapters are isolated for future provider swaps |
| SSE streaming through serverless | If your plan buffers streams, the drawer may show stages late or error | The cron endpoint and `POST /api/live/refresh` do the same work without streaming |
| Vercel Cron (Hobby) runs are best-effort daily | Refresh time may drift | Fine for EOD data |

A `Dockerfile` is kept in the repo purely as portability insurance — the app
is a standard container + standard Postgres and can move anywhere in minutes
if these free tiers ever change. It is not needed for the Neon + Vercel path.
