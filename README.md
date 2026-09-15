## discordbot

Discord bot for the Daybid connectome memory service.

Currently this package holds only `ConnectomeClient`, a minimal async HTTP
client that talks to the Go backend's `/api/connectome` routes directly
(`httpx`, no MCP dependency). The bot itself (Discord library wiring, slash
commands, token config) is not built yet - see [issue
#41](https://github.com/dfavelava/DaybidDev/issues/41).

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
```

Set `CONNECTOME_API_KEY` to the same value as `apikey` in `backend/.env`.
`ConnectomeClient` also falls back to `DAYBID_API_KEY` or `apikey` if
`CONNECTOME_API_KEY` isn't set, so it can share an env file with `daybidMCP`
in local dev.

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
- `GET /api/connectome/memory/?key=...` (`get_memory`)
- `GET /api/connectome/memory/list` (`browse_all`)
- `DELETE /api/connectome/memory/` (`forget`)

`remember` writes a plain memory document (content + entity ids); it does not
replicate `daybidmcp.server`'s entity-record merge logic (`ent_*.json`
records, `member_of`, relationships). That can be added if/when the bot
needs it.
