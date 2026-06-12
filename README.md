# Bird Dashboard — public hosting + persistent sensor history

A single-file dashboard for BirdWeather station `27598`, hosted free on
**GitHub Pages**, with a **GitHub Actions** cron job that polls the BirdWeather
API every 6 hours and accumulates a continuous sensor history into
`birds-sensor-history.json`. The page auto-loads that file on startup, so the
history keeps growing whether or not anyone visits.

```
bird-dashboard/
├── index.html                 # the dashboard (served by GitHub Pages)
├── collector.py               # polls BirdWeather, merges into the JSON
├── birds-sensor-history.json  # accumulated history (committed by the bot)
└── .github/workflows/collect.yml   # cron + manual trigger
```

## One-time setup (~15 min, $0)

You need a GitHub account. Everything below uses the free tier.

### 1. Create the repo and push these files

```sh
cd "bird-dashboard"
git init -b main
git add .
git commit -m "Initial bird dashboard + sensor collector"
# Create an EMPTY repo on github.com first (no README), then:
git remote add origin https://github.com/<you>/bird-dashboard.git
git push -u origin main
```

### 2. Enable GitHub Pages

Repo → **Settings → Pages**:
- **Source:** Deploy from a branch
- **Branch:** `main` / `/ (root)` → **Save**

After a minute your dashboard is live at
`https://<you>.github.io/bird-dashboard/`.

### 3. Allow Actions to commit

Repo → **Settings → Actions → General → Workflow permissions**:
- Select **Read and write permissions** → **Save**.

(The workflow already requests `contents: write`, but this org/account toggle
must also permit it.)

### 4. Kick off the first collection

Repo → **Actions → "Collect sensor history" → Run workflow**.
It will fetch the latest ~10h and commit `birds-sensor-history.json`.
After that it runs automatically every 6 hours.

## How it works

- The API only ever returns the most recent ~1000 readings (~10 hours), and
  ignores `period`/`before`/large `last`. There is **no** way to back-fill
  older sensor data from the API.
- Running the collector every 6h means each fetch window overlaps the last, so
  the merged JSON forms a continuous, ever-growing record.
- `collector.py` merges **field-by-field** by timestamp, so it never loses or
  clobbers existing readings. The output JSON uses the exact same shape the
  dashboard's **Export** button produces (`{station, exportedAt, sensor:[...]}`),
  so the two are fully interchangeable.

## Configuration

The collector reads optional environment variables:

| Variable          | Default                       | Purpose                       |
| ----------------- | ----------------------------- | ----------------------------- |
| `BW_STATION_ID`   | `27598`                       | BirdWeather station id        |
| `BW_HISTORY_FILE` | `birds-sensor-history.json`   | Output file path              |

## Run the collector locally

```sh
python3 collector.py        # fetches + merges into birds-sensor-history.json
```

No dependencies — standard library only.

## Notes / costs

- **GitHub Pages**: free for public repos.
- **GitHub Actions**: free tier is 2,000 min/month for private repos and
  unlimited for public ones. This job runs ~30s × 4/day ≈ 1 min/day.
- The JSON grows ~slowly (~150 readings/hour ≈ a few MB/year). If it ever gets
  large you can prune old entries or roll to monthly files.
- Scheduled workflows can be delayed by GitHub during peak load; if you see a
  gap, a manual **Run workflow** fills it (as long as it's within ~10h).
- GitHub pauses cron workflows after 60 days of repo inactivity — any commit
  (including the bot's own) resets that, so it effectively stays alive.
