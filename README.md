# Dina — daily Instagram content miner 📈

Dina finds what's working in your niche right now and turns it into **3 post ideas +
3 trial-reel ideas** every day — each written in your brand voice, with a ready-to-post
caption, and tied back to your offer so your content actually funnels people toward
becoming buyers.

It runs on [Claude](https://www.anthropic.com/) (`claude-opus-4-8`) in two passes:

1. **Mine** — Claude uses **live web search** to find recent, high-performing posts,
   formats, and topics from the creators and hashtags you list.
2. **Create** — Claude reframes those patterns into original ideas for *your* brand and
   returns them as reliable structured data, rendered into a clean Markdown report.

Each run writes a dated report to [`reports/`](reports/).

---

## Quick start (local)

```bash
# 1. Install
pip install -r requirements.txt

# 2. Make it yours — edit the brand + accounts to mine
$EDITOR config.yaml

# 3. Add your Anthropic API key (get one at https://console.anthropic.com/)
export ANTHROPIC_API_KEY="sk-ant-..."

# 4. Run
python instagram_miner.py
```

You'll get today's report printed to the terminal and saved as `reports/YYYY-MM-DD.md`.

---

## Make it *yours*: `config.yaml`

Everything that personalizes the output lives in one file. The most important fields:

| Field | Why it matters |
|-------|----------------|
| `brand.niche` | Anchors all research and ideas. Be specific. |
| `brand.voice` | A short tone summary. |
| `brand.target_audience` | Who the content is designed to attract. |
| `brand.offer` | Every idea gets a "funnel tie-in" pointing here. |
| `competitors` | Accounts/creators Claude studies for reusable patterns. |
| `hashtags` | Search terms that surface trending content. |
| `avoid` | Topics/angles you never want suggested. |

### `brand_voice.md` — the full voice guide

For deep control over *how it sounds*, edit **`brand_voice.md`**. If that file
exists, Dina injects it into the generation prompt and every caption, hook, and
reel script must follow it exactly. It's the place for long-form voice rules,
do/don't lists, and signature phrasing (mirroring the brand voice you keep in
your Claude skills). The more precise this file, the more the ideas sound like
*you*. Delete the file to fall back to just the short `brand.voice` line.

---

## Run it daily (automatic)

A GitHub Actions workflow is included at
[`.github/workflows/daily.yml`](.github/workflows/daily.yml). It runs once a day,
generates the report, and commits it back to `reports/`.

To enable it:

1. Push this repo to GitHub.
2. Go to **Settings → Secrets and variables → Actions → New repository secret** and add
   `ANTHROPIC_API_KEY` with your key.
3. (Optional) Edit the `cron:` line in the workflow to pick your run time (it's in UTC).

The workflow also has a **"Run workflow"** button (Actions tab) so you can trigger it
on demand.

### Prefer cron on your own machine?

```cron
# 8am daily — adjust path + Python as needed
0 8 * * *  cd /path/to/Dina && ANTHROPIC_API_KEY=sk-ant-... /usr/bin/python3 instagram_miner.py
```

---

## Notes & honest limitations

- **"Mining" = live web search, not Instagram scraping.** Instagram has no open API for
  searching arbitrary posts, and scraping it violates their terms and breaks constantly.
  Dina instead has Claude search the open web for what your listed creators and niche are
  posting and what's trending — reliable, terms-friendly, and no extra API keys.
- **Want true live IG metrics later?** Options to plug in: the official Instagram Graph
  API (your own business account only), a paid scraper API (Apify/RapidAPI), or a
  connected-analytics service. The `research_trends()` function is the single place to
  swap in a richer data source — it just needs to return a text brief.
- **Cost:** each daily run is a couple of Claude calls (research + generation). Small, but
  it uses your Anthropic credits.

---

## How the code is organized

| File | Purpose |
|------|---------|
| `instagram_miner.py` | The whole tool: `research_trends()` → `generate_ideas()` → render → save. |
| `config.yaml` | Your brand, accounts to mine, and settings. |
| `.github/workflows/daily.yml` | Scheduled daily run + auto-commit of the report. |
| `reports/` | Dated Markdown reports land here. |
