# Deployment

GeoDiff runs on three free services. Nothing here requires a paid plan or a payment method.

| Part | Provider | Plan | Region | Config in this repo |
|---|---|---|---|---|
| Web app (static) | GitHub Pages | free, public repo | GitHub CDN | `.github/workflows/pages.yml` |
| API (FastAPI, Docker) | Render web service | Free | Ohio (`ohio`) | `render.yaml`, `api/Dockerfile` |
| Database (PostgreSQL + PostGIS) | Supabase | Free | East US (Ohio), `us-east-2` | `api/migrations/` |

The guided sample is plain files on GitHub Pages. It keeps working when the API or the
database is asleep, paused or gone. Only **Live workspace** and **Import public layer** need
the API.

## Free-tier behaviour (checked 2026-11-18; recheck the provider pages)

- **Render Free:** the service spins down after 15 minutes without traffic, and the next
  request waits about a minute while it starts. 750 free instance hours per workspace per
  month, which is enough for this one service. Free services can also restart at any time.
  There is no pre-deploy step on Free, so migrations run when the container starts
  (`RUN_MIGRATIONS_ON_START=1`), serialized by a Postgres advisory lock. The UI says "a free
  server can take up to a minute to wake up" while it waits.
- **Supabase Free:** 500 MB database. A project with low activity for 7 days is **paused**.
  Restore it from the dashboard; Supabase says paused Free projects stay restorable for a
  limited period (90 days at the time of writing), so check the dashboard. **No automatic
  backups on Free.** The direct database host is IPv6-only on Free, so the API uses the
  **session pooler** (IPv4, port 5432), which also supports the advisory lock used by
  migrations.
- **GitHub Pages:** public site at `https://hannan2102.github.io/geodiff-gis/`, HTTPS
  enforced, files served from the build artifact.

Data lifetime: a workspace and everything in it is deleted 72 hours after its last use. The
demo stores nothing that must be kept.

## Security settings

- `DATABASE_URL` exists only in Render's environment settings (`sync: false` in
  `render.yaml`). It is never in Git, the web bundle, a URL or the logs.
- The database is reachable only by the API. Supabase's Data API (PostgREST) is switched off
  in the dashboard (step 1). Migration `0003` also enables row-level security on every
  GeoDiff table and revokes the `anon`/`authenticated` grants, so the tables stay closed
  even if the Data API is switched back on.
- `CORS_ORIGINS=https://hannan2102.github.io`: only the Pages site can call the API from a
  browser.
- `CLIENT_IP_HEADER=cf-connecting-ip`: rate limits use the client IP from Render's
  Cloudflare edge, not `X-Forwarded-For` (Render appends to that header, so its first entry is
  client-controlled).
- The API returns structured errors without stack traces, and never returns another
  workspace's data or anyone's original upload (see [API.md](API.md)).

## First deployment

### 1. Supabase (you, in the browser)

1. Sign in at https://supabase.com/dashboard. Choose **New project** in an organization on the
   **Free** plan.
   - Name: `geodiff`
   - Database password: use **Generate a password** and save it in your password manager.
   - Region: **East US (Ohio)**
   - Leave everything else at the defaults. Create the project and wait until it is ready.
2. Switch off the Data API: **Project Settings → Data API** (some dashboard versions put it
   under **Integrations → Data API**), turn **Enable Data API** off, then save. GeoDiff never
   uses it.
3. Copy the connection string: select **Connect** at the top of the project, then **Session
   pooler**. Copy the URI. It looks like
   `postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-0-us-east-2.pooler.supabase.com:5432/postgres`.
   Replace `[YOUR-PASSWORD]` with the password from step 1, and add `?sslmode=require` to
   the end. Keep it somewhere safe for step 2. **Do not paste it into chat, an issue or a
   commit.**

PostGIS is enabled by the first migration (`CREATE EXTENSION IF NOT EXISTS postgis`). You do not
need to enable it by hand.

### 2. Render (you, in the browser)

1. Sign in at https://dashboard.render.com with GitHub. Select **New → Blueprint**.
2. Connect the repository **Hannan2102/geodiff-gis** (grant Render access to this repository
   only). Branch `main`. Render reads `render.yaml` and shows one Free web service,
   `geodiff-api`.
3. When Render asks for **DATABASE_URL**, paste the Supabase session pooler URI from step 1.3.
   Select **Apply**. No payment method is needed for the Free plan; if Render asks for one,
   stop there.
4. Wait for the first deploy to show **Live**. The health check is `/ready`, so the deploy
   only goes live after the API has connected, found PostGIS and applied migrations.
5. Copy the service URL from the top of the service page (for example
   `https://geodiff-api.onrender.com`). It is public and not a secret.

### 3. GitHub Pages (automated)

With the service URL from step 2.5:

```sh
gh api -X POST repos/Hannan2102/geodiff-gis/pages -f build_type=workflow   # once
gh variable set GEODIFF_API_URL --repo Hannan2102/geodiff-gis --body https://geodiff-api.onrender.com
gh workflow run pages.yml --repo Hannan2102/geodiff-gis
```

The Pages workflow builds `web/` with `VITE_BASE=/geodiff-gis/` and
`VITE_API_URL=$GEODIFF_API_URL`, writes the commit SHA to `version.txt`, and deploys. Until
`GEODIFF_API_URL` is set, the workflow skips instead of failing.

## Checking a deployment

```sh
curl -s https://geodiff-api.onrender.com/ready        # status, PostGIS, migrations, git_sha
curl -s https://hannan2102.github.io/geodiff-gis/version.txt
curl -sI -H 'Origin: https://evil.example' https://geodiff-api.onrender.com/v1/sources | grep -i access-control || echo "no CORS for other origins"
```

`/ready` reports `git_sha` (Render's `RENDER_GIT_COMMIT`). Compare it, and `version.txt`, with
`git rev-parse HEAD`.

## Updating

Push to `main`. CI runs. Render rebuilds the API (auto-deploy) and the Pages workflow
republishes the web app. Migrations are forward-only and must be backward compatible with the
previous release, because the old container keeps serving until the new one passes `/ready`.

## Rollback

- **API:** Render → `geodiff-api` → **Events** → pick the last good deploy → **Rollback**.
  Migrations are not rolled back. Write a new forward migration if a schema change was wrong.
- **Web:** `git revert <bad commit>` and push, or run the Pages workflow from an earlier commit:
  **Actions → Pages → Run workflow** on a branch or tag pointing at that commit.
- **Database:** see Backups.

## Backups and restore

Supabase Free has no automatic backups. Workspaces expire after 72 hours anyway, so the
practical restore is a clean schema. To take a manual dump, without installing Postgres
locally:

```sh
read -rs DATABASE_URL && export DATABASE_URL   # paste the session pooler URI; not echoed
docker run --rm -e DATABASE_URL postgres:17 sh -c 'pg_dump "$DATABASE_URL" --no-owner --no-privileges -Fc' > geodiff-$(date +%F).dump
```

Restore into an empty project:

```sh
docker run --rm -i -e DATABASE_URL postgres:17 sh -c 'pg_restore --no-owner --no-privileges -d "$DATABASE_URL"' < geodiff-YYYY-MM-DD.dump
unset DATABASE_URL
```

To rebuild from nothing: create a new Supabase project (step 1), update `DATABASE_URL` in
Render (**Environment**), and redeploy. Migrations recreate the schema on start.

## If the live demo is down

- Pages works but live mode says "Live API unavailable": the Render service is waking (wait a
  minute and select **Retry**), suspended (monthly hours used up), or the database is paused.
  Check `/ready`: `"database": false` means Supabase is paused. Restore it in the dashboard.
- Everything in the guided sample keeps working throughout.
