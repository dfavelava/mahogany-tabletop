## discordbot

Discord bot for the Daybid connectome memory service, built on
[discord.py](https://discordpy.readthedocs.io/). It talks to the Go backend
via `ConnectomeClient`, a minimal async HTTP client that hits the backend's
`/api/connectome` routes directly (`httpx`, no MCP dependency) - see [issue
#41](https://github.com/dfavelava/DaybidDev/issues/41).

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

### Why a standalone HTTP client instead of reusing `daybidmcp.server`

`daybidmcp.server`'s tool functions could be imported and called in-process
instead - same repo, same language, and it would reuse that module's
`format_memory`/entity-merge logic rather than re-deriving it. This package
takes the other option instead: a fresh client independent of `daybidmcp`,
hitting `/api/connectome/...` directly. That keeps the bot's only dependency
on the memory service being the same HTTP API any other client would use,
rather than an in-process import of the MCP server package.

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

### Run the tests

```bash
uv sync
uv run pytest
```

### `ConnectomeClient`

```python
from discordbot import ConnectomeClient

client = ConnectomeClient()
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
itself replicate `daybidmcp.server`'s entity-record merge logic. Instead,
`assert_relationship` calls the Go backend's shared `/entity/relationship`
endpoint (see [issue #51](https://github.com/dfavelava/DaybidDev/issues/51)),
which upserts stub `ent_*.json` records for the subject/object entities,
for the `member_of` predicate merges the object entity id into the subject's
`member_of` list, and - when `subject_kind`/`subject_meta` are given - stamps
those onto the subject entity (overwrite and shallow-merge respectively) -
the same state `daybidmcp.server.remember` would produce, without
re-deriving that logic in Python here. `get_entity` reads an `ent_*.json`
record back (e.g. to check `meta.owner` before deciding whether to write),
returning `None` if it doesn't exist yet.
