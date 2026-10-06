# MahoganyTableTop

Discord bot for a tabletop campaign, backed by the
[Connectome](https://github.com/dfavelava/connectome) memory service. Built on
[discord.py](https://discordpy.readthedocs.io/), it talks to Connectome's Go
backend via [`connectomeclient`](https://github.com/dfavelava/connectome/tree/main/connectomeClient)'s
`ConnectomeClient`, a minimal async HTTP client that hits the backend's
`/api/connectome` routes directly (`httpx`, no MCP dependency) - see [issue
#41](https://github.com/dfavelava/DaybidDev/issues/41).

The bot package is still named `discordbot` internally; only the repo/product
is MahoganyTableTop.

### Identity

Each Discord user is mapped to a Connectome entity id deterministically:
`discord-<user_id>` (see `discord_entity_id` in
[`identity.py`](src/discordbot/identity.py)). There's no entity-lookup tool
yet (Phase 2), so a deterministic id needs no separate lookup step.

### Commands

- `/remember <content>` - stores `content` as a memory attributed to the
  calling user's entity id.
- `/add-character <pc_name>` - resolves or creates a `character`-kind PC
  entity (`ent_<slugified pc_name>.json`), records the calling user's entity
  id as `meta.owner`, and adds the character to that player's
  `<player entity id>-characters` group. Refuses if the name is already
  owned by a different player; re-running it with a name the caller already
  owns is a no-op (see [issue
  #45](https://github.com/dfavelava/DaybidDev/issues/45)).
- `/join-party <character_name> <party_name>` - asserts a `member_of`
  relationship from the character's entity id (`ent_<slugified
  character_name>.json`) to the party `party_name`, via the backend's
  `/entity/relationship` endpoint (see [issue
  #51](https://github.com/dfavelava/DaybidDev/issues/51)). Refuses if the
  caller's entity id isn't recorded as `meta.owner` on that character (set by
  `/add-character`, [issue
  #45](https://github.com/dfavelava/DaybidDev/issues/45)) - a player can't
  assert party membership for a PC they don't own. Re-running it for a party
  the character already belongs to is a harmless no-op; `recall`'s `as`
  scoping reads this membership (one level, no recursion) to decide whether a
  memory whose `acl` names that party is visible to the character's owner.

- `/ask <question>` - answers a campaign question from memory (see
  [`ask.py`](src/discordbot/ask.py)). It `recall`s the top 10 hydrated
  memories with `as_` set to the caller's entity id, so a player's answer is
  built only from memories they can see. GM-only memories never reach their
  prompt. The model must cite the memories it used, by key, or by session
  plus timestamp when a memory has a `session`. If it can't, the bot answers
  "I don't know." If Ollama is unavailable, the reply lists the recalled
  snippets instead. The reply is ephemeral and deferred, because inference
  can take longer than Discord's 3 s deadline.

GM visibility is fixed config, not a command: set `DISCORD_GM_USER_IDS` (see
below) to a comma-separated list of Discord user ids, and the bot asserts a
`member_of: "GM"` relationship directly on each of their player entities once
on startup. There is no self-service path to grant GM visibility.

### Run the bot

```bash
uv sync
uv run discordbot
```

Requires `DISCORD_BOT_TOKEN` (see below) and a reachable Connectome backend.

### Why a standalone HTTP client instead of reusing Connectome's MCP server

Connectome ships its own Python MCP server (`daybidmcp.server`, in the
[Connectome repo](https://github.com/dfavelava/connectome)), whose tool
functions could in principle be imported and called in-process, reusing that
module's `format_memory`/entity-merge logic rather than re-deriving it. This
bot depends on `connectomeclient` instead: a fresh client independent of
`daybidmcp`, hitting `/api/connectome/...` directly. That keeps the bot's
only dependency on the memory service being the same HTTP API any other
client would use, rather than an in-process import of the MCP server package
- which also means this bot can live in its own repo, deployed and versioned
independently of Connectome.

### Environment

Copy the example env file and set the API key:

```bash
cp .env.example .env
```

```dotenv
CONNECTOME_API_BASE_URL=http://localhost:8080/api/connectome
CONNECTOME_API_KEY=your-api-key
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_GUILD_ID=
DISCORD_GM_USER_IDS=
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:4b
```

Set `CONNECTOME_API_KEY` to the same value as `apikey` in `backend/.env`.
`ConnectomeClient` also falls back to `DAYBID_API_KEY` or `apikey` if
`CONNECTOME_API_KEY` isn't set, so it can share an env file with `daybidMCP`
in local dev.

`DISCORD_BOT_TOKEN` comes from your bot's application in the [Discord
Developer Portal](https://discord.com/developers/applications). Set
`DISCORD_GUILD_ID` to a test server's id to sync slash commands there
instantly during development; leave it unset in production so commands sync
globally (which can take up to an hour to propagate).

`OLLAMA_BASE_URL` and `OLLAMA_MODEL` (defaults shown above) configure
`LLMClient` in [`llm.py`](src/discordbot/llm.py), a thin async `httpx` client
for Ollama's `/api/chat` with thinking disabled and optional JSON-schema
output. Ollama is optional: nothing contacts it at startup, and failures raise
`LLMUnavailable` so callers can degrade.

### Session pipeline: ingest + transcribe

Craig records one track per speaker, so speaker attribution comes free:
track → Discord user id → `discord-<id>`. The `mahogany` CLI turns a Craig
export into speaker-attributed transcript segments:

```bash
uv sync --extra gpu   # faster-whisper; the bot host doesn't need it
uv run mahogany ingest craig-export.zip --session S12 --date 2026-09-27
```

- [`pipeline/ingest.py`](src/discordbot/pipeline/ingest.py) reads the export
  (a `.zip` or an extracted directory of per-user FLAC/AAC files) and maps
  each `<n>-<username>.<ext>` track to a Discord user id via the `Tracks:`
  list in Craig's `info.txt`: by track number, falling back to username.
  A track it can't map is an error that names the file, never silently dropped.
- [`pipeline/transcribe.py`](src/discordbot/pipeline/transcribe.py) wraps
  faster-whisper (default `medium.en`, `compute_type=int8`, VAD on). It
  transcribes each track separately and emits
  `Segment(user_id, start, end, text)`, merged across speakers by start time.
- Output goes to `transcripts/<session>.json` (override with `--out`):
  `{"session", "date", "segments": [...]}`. Chunking and storing it is #10.

Override the model with `--model`, `--device` (`cuda`/`cpu`/`auto`), and
`--compute-type`.

### Run the tests

```bash
uv sync
uv run pytest
```

### The `west-marches` tome

The bot only ever reads and writes the `west-marches` tome (`WEST_MARCHES_TOME`
in `campaign.py`, a constant rather than an env var). `connectomeclient` takes an
optional `tome` on each method rather than on the client, so every call in
`campaign.py` passes `tome=WEST_MARCHES_TOME` explicitly; omitting it would silently
fall through to the default tome. The test suite's `FakeConnectomeClient`
rejects any call without it, so a new call site that forgets fails tests.
Data written before this change lives in the default tome and won't be
visible to the bot.

### `ConnectomeClient`

`ConnectomeClient` comes from the
[`connectomeclient`](https://github.com/dfavelava/connectome/tree/main/connectomeClient)
package (a git dependency on the Connectome repo, not code owned by this
repo); `discordbot`'s `__init__.py` re-exports it for convenience.

```python
from connectomeclient import ConnectomeClient

client = ConnectomeClient(source_type="discord")
await client.remember("David prefers tea over coffee.", entities=["david"])
await client.recall("what does david drink")
```

Covers the routes needed to write and search memory:

- `POST /api/connectome/memory/` (`remember`)
- `POST /api/connectome/memory/search` (`recall`)
- `GET /api/connectome/memory/?key=...` (`get_memory`, and `get_entity` which
  parses the JSON body of an `ent_<id>.json` key)
- `GET /api/connectome/memory/list` (`browse_all`)
- `DELETE /api/connectome/memory/` (`forget`)
- `POST /api/connectome/entity/relationship` (`assert_relationship`)

`remember` writes a plain memory document (content + entity ids); it does not
itself replicate Connectome's `daybidmcp.server`'s entity-record merge logic.
Instead, `assert_relationship` calls the Go backend's shared
`/entity/relationship` endpoint (see [issue
#51](https://github.com/dfavelava/DaybidDev/issues/51)), which upserts stub
`ent_*.json` records for the subject/object entities, for the `member_of`
predicate merges the object entity id into the subject's `member_of` list,
and - when `subject_kind`/`subject_meta` are given - stamps those onto the
subject entity (overwrite and shallow-merge respectively) - the same state
`daybidmcp.server.remember` would produce, without re-deriving that logic in
Python here. `get_entity` reads an `ent_*.json` record back (e.g. to check
`meta.owner` before deciding whether to write), returning `None` if it
doesn't exist yet.
