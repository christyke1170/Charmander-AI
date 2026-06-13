# Pokémon GO AI Discord Bot

A local-first Pokémon GO Discord bot that gathers event/news information from public sources, stores it in SQLite, and lets Discord users query the local database with slash commands.

The bot keeps event data, Pokémon knowledge, raid attacker rankings, Dynamax/Gigantamax attacker rankings, and LeekDuck egg pools in SQLite. Owner commands and background refresh jobs update those caches, while user-facing commands answer quickly from local data.

## Features

- Scrapes public Pokémon GO information from:
  - <https://leekduck.com/events/>
  - <https://pokemongolive.com/news>
- Stores events locally in `database/pogo_events.sqlite`
- Stores Pokémon-specific Pokémon GO Hub DB knowledge in a separate `pokemon_knowledge` SQLite table
- Stores raid attacker ranking rows in a separate `raid_attacker_rankings` SQLite table, with freshness timestamps in `cache_metadata`
- Stores Dynamax/Gigantamax attacker ranking rows in a separate `dynamax_attackers` SQLite table, with freshness timestamps in `cache_metadata`
- Stores LeekDuck egg pool rows in a separate `egg_pools` SQLite table, with freshness timestamps in `cache_metadata`
- Deduplicates events with `UNIQUE(source, title, start_time)`
- Discord slash commands:
  - `/events` — upcoming events
  - `/today` — active events
  - `/raids` — raid-related events
  - `/communityday` — Community Day related events
  - `/ask` — OpenAI RAG answer when configured, local keyword fallback otherwise
  - `/raidattackers query:` — ask about cached monthly raid attacker rankings/data
  - `/dynamax query:` — ask about cached monthly Dynamax/Gigantamax attacker rankings/data
  - `/eggs query:` — ask about cached current egg pools, distances, Adventure Sync/Route Gift pools, or Pokémon hatch availability
  - `/pokemon query:` — ask about cached Pokémon GO Hub Pokémon knowledge
  - `/update` — owner-only manual update
  - `/updatepokemon` — owner-only manual Pokémon GO Hub DB cache update
  - `/updateraidattackers` — owner-only manual raid attacker cache refresh
  - `/updatedynamax` — owner-only manual Dynamax/Gigantamax attacker cache refresh
  - `/updateeggs` — owner-only manual LeekDuck egg pool cache refresh
  - `/importpokemon` — owner-only local CSV/JSON Pokémon knowledge import fallback
- Mention-based local Q&A, such as `@Pokemon GO AI Bot what raids are active?`
- Optional OpenAI-powered RAG answers for `/ask`, `/pokemon`, and @mention conversations after local rows are retrieved
- Lightweight OpenAI answer helpers for grounded responses when an API key is configured

## Install dependencies

```bash
cd pokemon-go-ai-bot
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

PowerShell without activating the venv:

```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

On macOS/Linux, activate with:

```bash
source .venv/bin/activate
```

## Create a Discord bot token

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application.
3. Open **Bot** in the left sidebar.
4. Add a bot if needed.
5. Copy the bot token. Keep it secret.
6. Enable message content for mention-based conversational responses:
   - **Developer Portal → App → Bot → Privileged Gateway Intents → Message Content Intent ON**
7. Under **OAuth2 → URL Generator**:
   - Select `bot` and `applications.commands` scopes.
   - Select appropriate bot permissions, such as `Send Messages`.
8. Open the generated URL to invite the bot to your server.

## Configure `.env`

Copy `.env.example` to `.env`:

```bash
copy .env.example .env
```

On macOS/Linux:

```bash
cp .env.example .env
```

Edit `.env`:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_OWNER_ID=123456789012345678
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini
POKEMON_DB_SCRAPE_LIMIT=50
RAID_ATTACKER_CACHE_MAX_AGE_DAYS=30
RAID_ATTACKER_AUTO_UPDATE=true
RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS=24
DYNAMAX_CACHE_MAX_AGE_DAYS=30
DYNAMAX_AUTO_UPDATE=true
DYNAMAX_AUTO_UPDATE_CHECK_HOURS=24
EGG_CACHE_MAX_AGE_DAYS=30
EGG_AUTO_UPDATE=true
EGG_AUTO_UPDATE_CHECK_HOURS=24
RAID_ATTACKER_USE_BROWSER_SCRAPER=true
RAID_ATTACKER_BROWSER_HEADLESS=true
RAID_ATTACKER_BROWSER_TIMEOUT_SECONDS=45
RAID_ATTACKER_BROWSER_SLOW_MO_MS=0
RAID_ATTACKER_BROWSER_PROFILE_DIR=
DYNAMAX_USE_BROWSER_SCRAPER=true
DYNAMAX_BROWSER_HEADLESS=false
DYNAMAX_BROWSER_TIMEOUT_SECONDS=60
DYNAMAX_BROWSER_SLOW_MO_MS=50
DYNAMAX_BROWSER_PROFILE_DIR=data/playwright-dynamax-profile
```

To get your Discord user ID, enable Developer Mode in Discord, right-click your user, and choose **Copy User ID**.

Raid attacker, Dynamax attacker, and egg auto-refresh settings control cache age and check frequency. SQLite `cache_metadata.last_updated` stores the actual successful update timestamps.

## Run the manual weekly update

```bash
python weekly_update.py
```

This initializes the SQLite database, scrapes the configured sources once, normalizes results, and upserts them into `database/pogo_events.sqlite`.

## Run the manual Pokémon knowledge update

```bash
python pokemon_knowledge_update.py
```

This initializes the same SQLite database file and updates the separate `pokemon_knowledge` table from Pokémon GO Hub DB pages. The default manual scrape limit is controlled by `POKEMON_DB_SCRAPE_LIMIT=50`.

## Raid attacker cache auto-update behavior

Raid attacker rankings are cached in SQLite and are considered stale after `RAID_ATTACKER_CACHE_MAX_AGE_DAYS` days, which defaults to 30. The bot stores the successful refresh timestamp in the SQLite `cache_metadata` table under the `raid_attackers` cache name. `.env` controls settings such as max age, automatic refresh, and background check frequency.

The ranking cache stores actual ranking rows in `raid_attacker_rankings`, including `ranking_scope`, `rank`, `pokemon_type`, `fast_move`, `charged_move`, `score`, `dps`, `tdo`, `summary`, and source URL when available. `ranking_scope='overall'` is used for top overall raid attackers, and type scopes use values such as `type:fire`, `type:water`, and `type:dragon`.

On bot startup, the bot initializes `cache_metadata` and `raid_attacker_rankings`, checks whether the raid attacker cache is stale, and starts a background refresh when `RAID_ATTACKER_AUTO_UPDATE=true`. A recurring background task checks every `RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS` hours, defaulting to 24. An update lock keeps raid attacker refreshes from overlapping.

User commands such as `/raidattackers`, `/pokemon`, `/ask`, and @mentions answer from the SQLite cache. If cached data is stale while a background update is running, responses may mention that raid attacker data is updating. If the cache is empty, users are told to ask the owner to run `/updateraidattackers`.

Owners can force a refresh with:

```text
/updateraidattackers
```

If an automatic refresh is already running, `/updateraidattackers` returns a friendly in-progress message. Successful refreshes update both the ranking rows and the `cache_metadata.last_updated` timestamp.

### Raid attacker update source order

`raid_attacker_update.py` tries raid attacker data sources in this order:

1. Existing `requests` + BeautifulSoup scraper.
2. Optional Playwright Chromium browser scraper when `RAID_ATTACKER_USE_BROWSER_SCRAPER=true`.
3. Local CSV/JSON seed import with example-data safety checks enabled.

The owner-only `/updateraidattackers` command and the monthly background cache refresh handle live raid attacker updates. User-facing commands use cached ranking rows.

## Dynamax/Gigantamax attacker cache auto-update behavior

Dynamax and Gigantamax attacker rankings are cached separately from normal raid attacker rankings in SQLite table `dynamax_attackers`. The source is Pokémon GO Hub’s Dynamax attacker page:

```text
https://db.pokemongohub.net/best/dynamax-attackers-per-type
```

Type sections use hash anchors such as `#fire` and `#fighting`, and cached rows store source URLs like `https://db.pokemongohub.net/best/dynamax-attackers-per-type#fire`.

The cache is considered stale after `DYNAMAX_CACHE_MAX_AGE_DAYS` days, defaulting to 30. The bot stores the successful refresh timestamp in SQLite `cache_metadata` under cache name `dynamax_attackers`. On startup, the bot initializes `dynamax_attackers`, checks freshness, and starts a background refresh when `DYNAMAX_AUTO_UPDATE=true`. A recurring background task checks every `DYNAMAX_AUTO_UPDATE_CHECK_HOURS` hours. An update lock prevents overlapping refreshes.

Normal user questions never scrape live. `/dynamax`, `/ask`, `/pokemon`, and @mention queries answer only from cached SQLite rows. Queries containing `dynamax`, `gigantamax`, `dmax`, `gmax`, or `max battle` route to Dynamax rankings before normal raid attacker rankings.

Examples:

```text
/dynamax
/dynamax fire
/dynamax fighting
/dynamax best fire attackers
/dynamax top 10 gmax attackers
@Pokemon GO AI Bot best fire dynamax attackers
@Pokemon GO AI Bot best dmax pokemon
```

Owners can force a refresh with:

```text
/updatedynamax
```

Run the updater manually with:

```bash
python dynamax_update.py
```

`dynamax_update.py` tries static `requests` + BeautifulSoup first. If the page is blocked or produces zero rows, it can use a Dynamax-specific Playwright Chromium browser configuration. Install browser support with `python -m playwright install chromium` if required. If the scraper returns zero rows or is blocked, existing cached rows are kept and `cache_metadata.last_updated` is not marked fresh.

### Dynamax persistent browser profile for Cloudflare

Pokémon GO Hub may show Cloudflare security verification to automated browsers. Dynamax scraping can use a persistent headed Playwright profile so the owner can solve Cloudflare once and reuse that browser session for future owner-run updates.

Recommended local `.env` settings:

```env
DYNAMAX_USE_BROWSER_SCRAPER=true
DYNAMAX_BROWSER_HEADLESS=false
DYNAMAX_BROWSER_TIMEOUT_SECONDS=60
DYNAMAX_BROWSER_SLOW_MO_MS=50
DYNAMAX_BROWSER_PROFILE_DIR=data/playwright-dynamax-profile
```

The profile directory is ignored by git. Normal Discord questions still never scrape live; only owner/background update paths use the live scraper.

To debug and manually solve Cloudflare:

```powershell
.\.venv\Scripts\python.exe debug_dynamax_scrape.py --pause
```

If Cloudflare appears, solve it in the opened browser, then press Enter in the terminal. The script saves `debug/dynamax_page.html`, `debug/dynamax_page.txt`, and `debug/dynamax_screenshot.png`, then prints whether type names/table-like rows were visible.

After the browser profile has a valid session, run:

```powershell
.\.venv\Scripts\python.exe dynamax_update.py --pause-browser
```

If rows parse successfully, SQLite and cache metadata are updated. If rows are still zero, `dynamax_update.py` falls back to `data/dynamax_attackers.csv` as before.

### Dynamax manual CSV fallback

Pokémon GO Hub live scraping may be blocked by anti-bot protection. When that happens, you can manually maintain a local CSV fallback. Normal Discord questions still use SQLite only and never read/scrape live pages.

1. Copy or create the local CSV:

   ```bash
   copy data\dynamax_attackers.example.csv data\dynamax_attackers.csv
   ```

2. Replace the example row with real current rankings. The production CSV is ignored by git at `data/dynamax_attackers.csv`.

Required columns:

```csv
ranking_scope,pokemon_type,rank,pokemon_name,form,fast_move,charged_move,score,dps,tdo,summary,url
```

Example real row shape:

```csv
type:fire,fire,1,Charizard,,Fire Spin,Max Flare,28.04,31.98,700,Top cached Fire-type Dynamax attacker.,https://db.pokemongohub.net/best/dynamax-attackers-per-type#fire
```

Import the CSV directly with:

```bash
python dynamax_import.py
```

Or run the update script, which tries the live scraper first and then falls back to `data/dynamax_attackers.csv` if the scraper returns zero rows:

```bash
python dynamax_update.py
```

The importer rejects obvious placeholder/example rows by default. Use `--allow-example-data` only for local formatting tests, never production bot data. Metadata key `dynamax_attackers` is marked fresh only when rows are actually imported/upserted.

## Egg pool cache auto-update behavior

LeekDuck egg pools are cached in SQLite table `egg_pools` and are considered stale after `EGG_CACHE_MAX_AGE_DAYS` days, which defaults to 30. The bot stores the successful refresh timestamp in SQLite `cache_metadata` under cache name `egg_pools`. `.env` controls max age, automatic refresh, and background check frequency.

On bot startup, the bot initializes `egg_pools` and `cache_metadata`, checks whether the egg cache is stale, and starts a background refresh when `EGG_AUTO_UPDATE=true`. A recurring background task checks every `EGG_AUTO_UPDATE_CHECK_HOURS` hours, defaulting to 24. An update lock keeps egg refreshes from overlapping. Successful refreshes replace the cached LeekDuck egg rows and update `cache_metadata.last_updated`.

Egg pool updates run through the owner-only `/updateeggs` command, the monthly background cache refresh, or manual `egg_update.py`. User commands such as `/eggs`, `/ask`, `/pokemon`, and @mentions answer from cached SQLite egg data.

Examples:

```text
/eggs
/eggs 1km
/eggs 10km adventure sync
/eggs route gift
/eggs Larvesta
```

Owners can force a refresh with:

```text
/updateeggs
```

Run the egg updater manually with:

```bash
python egg_update.py --force
```

### Raid attacker browser scraper config

Pokémon GO Hub DB may return Cloudflare challenge pages to plain `requests`. The optional Playwright scraper is enabled by default because it is the practical automated path when the table is visible in a normal browser.

```env
RAID_ATTACKER_USE_BROWSER_SCRAPER=true
RAID_ATTACKER_BROWSER_HEADLESS=true
RAID_ATTACKER_BROWSER_TIMEOUT_SECONDS=45
RAID_ATTACKER_BROWSER_SLOW_MO_MS=0
RAID_ATTACKER_BROWSER_PROFILE_DIR=
```

- Set `RAID_ATTACKER_USE_BROWSER_SCRAPER=false` to use the requests scraper and local seed fallback without Playwright.
- Set `RAID_ATTACKER_BROWSER_HEADLESS=false` to launch a visible Chromium browser if headless mode is blocked.
- Set `RAID_ATTACKER_BROWSER_PROFILE_DIR=data/playwright-profile` to use a persistent browser profile that can retain local cookies/session state for `/updateraidattackers`, the debug script, and background cache refreshes.

Debug one type without writing to SQLite:

```powershell
.\.venv\Scripts\python.exe debug_browser_scrape_best_per_type.py dark
.\.venv\Scripts\python.exe debug_browser_scrape_best_per_type.py fire
.\.venv\Scripts\python.exe debug_browser_scrape_best_per_type.py dark --headed
```

Troubleshooting:

1. If Cloudflare blocks headless mode, set `RAID_ATTACKER_BROWSER_HEADLESS=false` and run `/updateraidattackers` or the debug script once.
2. If headed mode is still blocked, set `RAID_ATTACKER_BROWSER_PROFILE_DIR=data/playwright-profile` and run `/updateraidattackers` or the debug script once so the automated browser can reuse that local profile afterward.
3. If the table is unavailable, the browser scraper returns zero rows, keeps the existing cache, and leaves `cache_metadata.last_updated` unchanged.

### Raid attacker local import fallback

When the live ranking scrapers need a local fallback, `raid_attacker_update.py` checks these seed files:

1. `data/raid_attackers_seed.csv`
2. `data/raid_attackers_seed.json`

CSV columns:

```csv
source,ranking_scope,pokemon_name,form,pokemon_type,rank,fast_move,charged_move,score,dps,tdo,summary,url,scraped_at
```

You can copy the included example file and edit it:

```bash
copy data\raid_attackers_seed.example.csv data\raid_attackers_seed.csv
```

The included example contains overall ranks 1-3, `type:fire` ranks 1-3, and `type:water` ranks 1-3. Replace the example rows with real raid attacker rankings before running an import/update. The update path filters placeholder rows whose `source` contains `example`, whose `pokemon_name` starts with `Example `, or whose `summary` contains the example-data marker.

For local testing only, `raid_attacker_import.py` supports:

```bash
python raid_attacker_import.py --allow-example-data
```

Use `--allow-example-data` for local formatting tests only.

Run the raid attacker updater manually with:

```bash
python raid_attacker_update.py --force
```

Inspection notes from 2026-06-11:

- `robots.txt` allows crawling and lists Pokémon sitemap files.
- curl requests to the Pokémon GO Hub DB home page, Pokédex page, and a Pokémon detail page returned server-rendered HTML with useful text.
- Python `requests` + BeautifulSoup currently receives Cloudflare challenge HTML for Pokémon GO Hub DB in this environment. Raid attacker updates now have an optional Playwright browser scraper fallback; Pokémon knowledge updates use the requests-based scraper and local import path.
- If zero detail links or zero rows are found, the script reports pages checked, link selector/regex matches, blocked pages, and parse failures instead of faking success.

## Import local Pokémon knowledge fallback

When Pokémon GO Hub static scraping returns Cloudflare challenge pages, you can manually provide local Pokémon knowledge data and import it into the existing `pokemon_knowledge` table.

The importer checks for these files, in this order:

1. `data/pokemon_knowledge_seed.csv`
2. `data/pokemon_knowledge_seed.json`

If neither file exists, it prints a clear message telling you where to put the seed file.

### CSV seed format

Create `data/pokemon_knowledge_seed.csv` with these columns:

```csv
source,pokemon_id,name,form,types,max_cp,best_moveset,weaknesses,resistances,pve_summary,pvp_summary,raid_counter_summary,raw_text,url
manual_seed,6,Charizard,,"Fire, Flying",example,"Example moveset","Example weaknesses","Example resistances","Example PvE notes","Example PvP notes","Example counters","Example raw/source notes",https://example.invalid/pokemon/charizard
```

You can copy the included example file and edit it:

```bash
copy data\pokemon_knowledge_seed.example.csv data\pokemon_knowledge_seed.csv
```

On macOS/Linux:

```bash
cp data/pokemon_knowledge_seed.example.csv data/pokemon_knowledge_seed.csv
```

The included rows are examples; replace them with your own current Pokémon knowledge data before importing for a live bot.

### JSON seed format

Create `data/pokemon_knowledge_seed.json` as either a list of objects:

```json
[
  {
    "source": "manual_seed",
    "pokemon_id": "6",
    "name": "Charizard",
    "types": "Fire, Flying",
    "best_moveset": "Example moveset",
    "raw_text": "Example notes; replace with current data before using in a live bot.",
    "url": "https://example.invalid/pokemon/charizard"
  }
]
```

Or an object with a `pokemon` list:

```json
{
  "pokemon": [
    {
      "source": "manual_seed",
      "pokemon_id": "382",
      "name": "Kyogre",
      "types": "Water",
      "raw_text": "Example notes; replace with current data before using in a live bot."
    }
  ]
}
```

If `source` is missing, it defaults to `manual_seed`. If `scraped_at` is missing, the importer sets the current UTC timestamp.

### Run the local import

```bash
python pokemon_knowledge_import.py
```

From Discord, the configured owner can run:

```text
/importpokemon
```

The command reports how many rows were imported/upserted, how many were skipped, and whether CSV or JSON was used.

## Run the Discord bot

```bash
python -m bot.discord_bot
```

Slash commands are synced on startup. It may take a moment for Discord to show new global commands.

For @mention responses to work, the bot must use Message Content Intent in code and in the Discord Developer Portal. This project enables it in code with:

```python
intents = discord.Intents.default()
intents.message_content = True
```

## Example commands

- `/events` — show upcoming local events
- `/today` — show currently active local events
- `/raids` — show raid-related local events
- `/communityday` — show Community Day related events
- `/ask query: raid hour` — ask a local event question with OpenAI support when configured
- `/ask query: best fire attacker` — raid-attacker questions are routed to cached raid attacker rankings first
- `/eggs query: 1km` — show current cached 1 km egg hatches
- `/eggs query: Larvesta` — show which cached egg pools include Larvesta
- `/raidattackers` — show top cached overall raid attackers
- `/raidattackers query: fire` — show top cached Fire-type raid attackers
- `/raidattackers query: best fire attacker` — ask against cached monthly raid attacker ranking data
- `/pokemon query: Charizard best moveset` — ask directly against cached Pokémon GO Hub DB knowledge
- `/pokemon query: Kyogre counters` — retrieve local Pokémon rows, with optional OpenAI wording
- `/aistatus` — show whether OpenAI is configured and which model is selected
- `/update` — owner-only manual scrape/update
- `/updatepokemon` — owner-only manual Pokémon GO Hub DB knowledge update
- `/updateraidattackers` — owner-only manual raid attacker cache refresh
- `/updateeggs` — owner-only manual egg pool cache refresh
- `/importpokemon` — owner-only import from `data/pokemon_knowledge_seed.csv` or `data/pokemon_knowledge_seed.json`
- `@Pokemon GO AI Bot what raids are active?` — route to raid-related local events
- `@Pokemon GO AI Bot when is the next community day?` — route to Community Day events
- `@Pokemon GO AI Bot any events for shiny hunting?` — search local event data for shiny-related text

## Notes and future improvements

- If either event/news source moves important content behind dynamic rendering, consider a separate opt-in browser scraper later.
- The `ai/` folder includes a lightweight RAG-style OpenAI layer that uses retrieved SQLite rows as answer context.
- `/events`, `/today`, `/raids`, and `/communityday` are local event-focused commands.
- Be respectful of public websites: use manual updates, normal headers, timeouts, and avoid aggressive scraping.
- If you see `ModuleNotFoundError` for packages like `requests` or `discord`, activate your virtual environment and run `python -m pip install -r requirements.txt`.
