# Pokémon GO AI Discord Bot MVP

A local-first Pokémon GO Discord bot that gathers event/news information from public sources, stores it in SQLite, and lets Discord users query the local database with slash commands.

This MVP does **not** train an LLM and does **not** scrape websites on every Discord command. Event scraping is only run manually with `weekly_update.py` or the owner-only `/update` command. Pokémon GO Hub DB knowledge is cached separately with `pokemon_knowledge_update.py` or the owner-only `/updatepokemon` command. Raid attacker rankings and LeekDuck egg pools are stored in SQLite and can auto-refresh monthly in the background; normal user questions still read only from the local cache.

## Features

- Scrapes public Pokémon GO information from:
  - <https://leekduck.com/events/>
  - <https://pokemongolive.com/news>
- Stores events locally in `database/pogo_events.sqlite`
- Stores Pokémon-specific Pokémon GO Hub DB knowledge in a separate `pokemon_knowledge` SQLite table
- Stores raid attacker ranking rows in a separate `raid_attacker_rankings` SQLite table, with freshness timestamps in `cache_metadata`
- Stores LeekDuck egg pool rows in a separate `egg_pools` SQLite table, with freshness timestamps in `cache_metadata`
- Deduplicates events with `UNIQUE(source, title, start_time)`
- Discord slash commands:
  - `/events` — upcoming events
  - `/today` — active events
  - `/raids` — raid-related events
  - `/communityday` — Community Day related events
  - `/ask` — OpenAI RAG answer when configured, local keyword fallback otherwise
  - `/raidattackers query:` — ask about cached monthly raid attacker rankings/data
  - `/eggs query:` — ask about cached current egg pools, distances, Adventure Sync/Route Gift pools, or Pokémon hatch availability
  - `/pokemon query:` — ask about cached Pokémon GO Hub Pokémon knowledge
  - `/update` — owner-only manual update
  - `/updatepokemon` — owner-only manual Pokémon GO Hub DB cache update
  - `/updateraidattackers` — owner-only manual raid attacker cache refresh
  - `/updateeggs` — owner-only manual LeekDuck egg pool cache refresh
  - `/importpokemon` — owner-only local CSV/JSON Pokémon knowledge import fallback
- Mention-based local Q&A, such as `@Pokemon GO AI Bot what raids are active?`
- Optional OpenAI-powered RAG answers for `/ask`, `/pokemon`, and @mention conversations after local rows are retrieved
- Placeholder AI modules for future summaries and retrieval-augmented Q&A

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
EGG_CACHE_MAX_AGE_DAYS=30
EGG_AUTO_UPDATE=true
EGG_AUTO_UPDATE_CHECK_HOURS=24
RAID_ATTACKER_USE_BROWSER_SCRAPER=true
RAID_ATTACKER_BROWSER_HEADLESS=true
RAID_ATTACKER_BROWSER_TIMEOUT_SECONDS=45
RAID_ATTACKER_BROWSER_SLOW_MO_MS=0
RAID_ATTACKER_BROWSER_PROFILE_DIR=
```

To get your Discord user ID, enable Developer Mode in Discord, right-click your user, and choose **Copy User ID**.

Raid attacker and egg auto-refresh settings are configuration only. The actual last successful update timestamps are stored in SQLite `cache_metadata.last_updated`, not in `.env`.

## Run the manual weekly update

```bash
python weekly_update.py
```

This initializes the SQLite database, scrapes the configured sources once, normalizes results, and upserts them into `database/pogo_events.sqlite`.

## Run the manual Pokémon knowledge update

```bash
python pokemon_knowledge_update.py
```

This initializes the same SQLite database file and updates only the separate `pokemon_knowledge` table from cached Pokémon GO Hub DB pages. It does **not** run on every Discord question. The default manual scrape limit is controlled by `POKEMON_DB_SCRAPE_LIMIT=50`.

## Raid attacker cache auto-update behavior

Raid attacker rankings are cached in SQLite and are considered stale after `RAID_ATTACKER_CACHE_MAX_AGE_DAYS` days, which defaults to 30. The bot stores the actual successful refresh timestamp in the SQLite `cache_metadata` table under the `raid_attackers` cache name. Do not put last-updated timestamps in `.env`; `.env` only controls settings such as max age, whether automatic updates are enabled, and how often the background checker runs.

The ranking cache stores actual ranking rows in `raid_attacker_rankings`, including `ranking_scope`, `rank`, `pokemon_type`, `fast_move`, `charged_move`, `score`, `dps`, `tdo`, `summary`, and source URL when available. `ranking_scope='overall'` is used for top overall raid attackers, and type scopes use values such as `type:fire`, `type:water`, and `type:dragon`.

On bot startup, the bot initializes `cache_metadata` and `raid_attacker_rankings`, checks whether the raid attacker cache is stale, and if `RAID_ATTACKER_AUTO_UPDATE=true`, starts a refresh in the background without blocking Discord login. A recurring background task then checks every `RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS` hours, defaulting to 24. Only one raid attacker refresh can run at a time. The startup check is guarded so repeated Discord `on_ready()` events do not start duplicate startup checks.

Normal user commands such as `/raidattackers`, `/pokemon`, `/ask`, and @mention questions never trigger live scraping directly. They answer from existing cached SQLite data. If cached data is stale while a background update is running, responses may mention that raid attacker data is updating. If the cache is empty, users are told to ask the owner to run `/updateraidattackers`.

Owners can force a refresh with:

```text
/updateraidattackers
```

If an automatic refresh is already running, `/updateraidattackers` returns a friendly in-progress message instead of starting a duplicate update. If scraping/import returns zero rows or fails, existing cached data is kept and `cache_metadata.last_updated` is not marked fresh.

### Raid attacker update source order

`raid_attacker_update.py` tries raid attacker data sources in this order:

1. Existing `requests` + BeautifulSoup scraper.
2. Optional Playwright Chromium browser scraper when `RAID_ATTACKER_USE_BROWSER_SCRAPER=true`.
3. Local CSV/JSON seed import with example-data safety checks enabled.

Normal user commands such as `/raidattackers`, `/ask`, `/pokemon`, and @mention responses still never scrape live. Only the owner-only `/updateraidattackers` command and the monthly background cache refresh can invoke the live update flow.

## Egg pool cache auto-update behavior

LeekDuck egg pools are cached in SQLite table `egg_pools` and are considered stale after `EGG_CACHE_MAX_AGE_DAYS` days, which defaults to 30. The bot stores the actual successful refresh timestamp in SQLite `cache_metadata` under cache name `egg_pools`. `.env` only controls max age, whether automatic egg updates are enabled, and how often the background checker runs.

On bot startup, the bot initializes `egg_pools` and `cache_metadata`, checks whether the egg cache is stale, and if `EGG_AUTO_UPDATE=true`, starts an egg refresh in the background without blocking Discord login. A recurring background task checks every `EGG_AUTO_UPDATE_CHECK_HOURS` hours, defaulting to 24. Only one egg refresh can run at a time. If scraping returns zero rows, times out, is blocked, or parsing fails, existing cached egg data is kept and `cache_metadata.last_updated` is not marked fresh.

Normal user commands such as `/eggs`, `/ask`, `/pokemon`, and @mention responses never scrape LeekDuck live. They answer from existing cached SQLite data only. Egg scraping only happens through the owner-only `/updateeggs` command, the monthly/background cache refresh, or manual `egg_update.py`.

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

- Set `RAID_ATTACKER_USE_BROWSER_SCRAPER=false` to skip Playwright entirely and go directly from the requests scraper to the local seed fallback.
- Set `RAID_ATTACKER_BROWSER_HEADLESS=false` to launch a visible Chromium browser if headless mode is blocked.
- Set `RAID_ATTACKER_BROWSER_PROFILE_DIR=data/playwright-profile` to use a persistent browser profile that can retain local cookies/session state. This profile is only used during `/updateraidattackers`, the debug script, or background cache refreshes — never during normal Discord questions.

Debug one type without writing to SQLite:

```powershell
.\.venv\Scripts\python.exe debug_browser_scrape_best_per_type.py dark
.\.venv\Scripts\python.exe debug_browser_scrape_best_per_type.py fire
.\.venv\Scripts\python.exe debug_browser_scrape_best_per_type.py dark --headed
```

Troubleshooting:

1. If Cloudflare blocks headless mode, set `RAID_ATTACKER_BROWSER_HEADLESS=false` and run `/updateraidattackers` or the debug script once.
2. If headed mode is still blocked, set `RAID_ATTACKER_BROWSER_PROFILE_DIR=data/playwright-profile` and run `/updateraidattackers` or the debug script once so the automated browser can reuse that local profile afterward.
3. If the table still cannot be loaded, the browser scraper returns zero rows, the existing cache is kept, and `cache_metadata.last_updated` is not marked fresh.

### Raid attacker local import fallback

If the requests scraper and browser scraper cannot fetch real ranking rows, `raid_attacker_update.py` falls back to local seed files:

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

The included example contains overall ranks 1-3, `type:fire` ranks 1-3, and `type:water` ranks 1-3. These rows are clearly marked as examples and are **not guaranteed current meta truth**. Do **not** use the copied file as-is: replace every example row with real raid attacker rankings before running an import/update. The normal bot auto-update path refuses to import rows whose `source` contains `example`, whose `pokemon_name` starts with `Example `, or whose `summary` says `not guaranteed current meta truth`, and it will not mark the cache fresh for those placeholder rows.

For local testing only, `raid_attacker_import.py` supports:

```bash
python raid_attacker_import.py --allow-example-data
```

Do not use `--allow-example-data` for production bot data or automatic updates.

Run the raid attacker updater manually with:

```bash
python raid_attacker_update.py --force
```

Inspection notes from 2026-06-11:

- `robots.txt` allows crawling and lists Pokémon sitemap files.
- curl requests to the Pokémon GO Hub DB home page, Pokédex page, and a Pokémon detail page returned server-rendered HTML with useful text.
- Python `requests` + BeautifulSoup currently receives Cloudflare challenge HTML for Pokémon GO Hub DB in this environment. Raid attacker updates now have an optional Playwright browser scraper fallback; Pokémon knowledge updates still avoid browser automation and never attempt to bypass captcha challenges.
- If zero detail links or zero rows are found, the script reports pages checked, link selector/regex matches, blocked pages, and parse failures instead of faking success.

## Import local Pokémon knowledge fallback

When Pokémon GO Hub static scraping returns Cloudflare challenge pages, you can manually provide local Pokémon knowledge data and import it into the existing `pokemon_knowledge` table. This does not create a second schema and does not scrape the web.

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

The included example rows are clearly marked as examples and are **not guaranteed current meta truth**.

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
    "raw_text": "Example notes; verify current meta before relying on this row.",
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
      "raw_text": "Example notes; verify current meta before relying on this row."
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

The command reports how many rows were imported/upserted, how many were skipped, and whether CSV or JSON was used. It does not print seed file contents.

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
- `/ask query: raid hour` — ask an AI-grounded local event question when OpenAI is configured, or use local snippets as fallback
- `/ask query: best fire attacker` — raid-attacker questions are routed to cached raid attacker rankings first
- `/raidattackers` — show top cached overall raid attackers
- `/raidattackers query: fire` — show top cached Fire-type raid attackers
- `/raidattackers query: best fire attacker` — ask against cached monthly raid attacker ranking data
- `/pokemon query: Charizard best moveset` — ask directly against cached Pokémon GO Hub DB knowledge
- `/pokemon query: Kyogre counters` — retrieve local Pokémon rows, then optionally use OpenAI only on that context
- `/aistatus` — show whether OpenAI is configured and which model is selected
- `/update` — owner-only manual scrape/update
- `/updatepokemon` — owner-only manual Pokémon GO Hub DB knowledge update
- `/updateraidattackers` — owner-only manual raid attacker cache refresh
- `/importpokemon` — owner-only import from `data/pokemon_knowledge_seed.csv` or `data/pokemon_knowledge_seed.json`
- `@Pokemon GO AI Bot what raids are active?` — route to raid-related local events
- `@Pokemon GO AI Bot when is the next community day?` — route to Community Day events
- `@Pokemon GO AI Bot any events for shiny hunting?` — search local event data for shiny-related text

## Notes and future improvements

- If either event/news source moves important content behind dynamic rendering, consider a separate opt-in browser scraper later.
- The `ai/` folder includes a lightweight RAG-style OpenAI layer. It sends only retrieved local SQLite event or Pokémon rows as context and instructs the model not to invent unsupported live data.
- `/events`, `/today`, `/raids`, and `/communityday` remain local event-focused commands and do not query the Pokémon GO Hub DB cache.
- Be respectful of public websites: use manual updates, normal headers, timeouts, and avoid aggressive scraping.
- If you see `ModuleNotFoundError` for packages like `requests` or `discord`, activate your virtual environment and run `python -m pip install -r requirements.txt`.
