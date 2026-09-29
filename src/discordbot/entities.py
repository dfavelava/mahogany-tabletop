import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from connectomeclient import ConnectomeClient
from discord import app_commands

from .identity import discord_entity_id

ENTITY_KEY_PREFIX = "ent_"
ENTITY_KEY_SUFFIX = ".json"
CHARACTER_KIND = "character"
PARTY_KIND = "party"
RETIRED_STATUS = "retired"
MEMBER_OF_PREDICATE_GROUP_SUFFIX = "-characters"

# Discord rejects autocomplete responses with more than 25 choices.
MAX_CHOICES = 25
DEFAULT_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class EntityRecord:
    id: str
    name: str
    kind: str | None = None
    owner: str | None = None
    retired: bool = False
    member_of: tuple[str, ...] = ()


@dataclass
class EntityIndex:
    """In-process cache of the tome's ent_*.json records for autocomplete.

    Autocomplete must answer inside Discord's 3 s window, so it only ever reads
    this cache (search/*_choices are synchronous and never call Connectome).
    The cache is filled by refresh(), which the bot runs at startup, after any
    write handler (mark_dirty), and once the TTL lapses (maybe_refresh) - always
    in a background task so a keystroke never waits on the network.
    """

    connectome: ConnectomeClient
    tome: str
    ttl: float = DEFAULT_TTL_SECONDS
    clock: Callable[[], float] = time.monotonic
    _records: list[EntityRecord] = field(default_factory=list)
    _loaded_at: float | None = None
    _dirty: bool = False
    _task: asyncio.Task[None] | None = None

    async def refresh(self) -> None:
        listing = await self.connectome.browse_all(tome=self.tome)
        contents = listing.get("Contents")
        keys = [item["Key"] for item in contents if isinstance(item, dict) and "Key" in item] if isinstance(contents, list) else []
        records: list[EntityRecord] = []
        for key in keys:
            if not (key.startswith(ENTITY_KEY_PREFIX) and key.endswith(ENTITY_KEY_SUFFIX)):
                continue
            entity_id = key[len(ENTITY_KEY_PREFIX) : -len(ENTITY_KEY_SUFFIX)]
            entity = await self.connectome.get_entity(entity_id, tome=self.tome)
            if entity is not None:
                records.append(_to_record(entity_id, entity))
        self._records = records
        self._loaded_at = self.clock()
        self._dirty = False

    def mark_dirty(self) -> None:
        """Flag the cache stale after a write handler and refresh in the background."""
        self._dirty = True
        self.maybe_refresh()

    def maybe_refresh(self) -> None:
        """Start a background refresh if the cache is dirty, unloaded, or past its TTL."""
        if self._task is not None and not self._task.done():
            return
        expired = self._loaded_at is None or self.clock() - self._loaded_at >= self.ttl
        if self._dirty or expired:
            self._task = asyncio.create_task(self._refresh_quietly())

    async def _refresh_quietly(self) -> None:
        try:
            await self.refresh()
        except Exception:
            # Keep serving the stale cache; the next trigger retries.
            pass

    def _match(self, records: list[EntityRecord], current: str) -> list[app_commands.Choice[str]]:
        needle = current.strip().lower()
        matches = [r for r in records if r.name.lower().startswith(needle) or r.id.lower().startswith(needle)]
        matches.sort(key=lambda r: r.name.lower())
        return [app_commands.Choice(name=r.name[:100], value=r.id[:100]) for r in matches[:MAX_CHOICES]]

    def entity_choices(self, current: str) -> list[app_commands.Choice[str]]:
        return self._match(self._records, current)

    def character_choices(self, current: str, user_id: int | None = None) -> list[app_commands.Choice[str]]:
        """Characters matching a prefix; with user_id, only that player's non-retired ones."""
        characters = [r for r in self._records if r.kind == CHARACTER_KIND]
        if user_id is not None:
            owner = discord_entity_id(user_id)
            characters = [r for r in characters if r.owner == owner and not r.retired]
        return self._match(characters, current)

    def party_choices(self, current: str) -> list[app_commands.Choice[str]]:
        """Parties: kind "party" entities plus any non-group entity some record is member_of."""
        party_ids = {r.id for r in self._records if r.kind == PARTY_KIND}
        for record in self._records:
            party_ids.update(g for g in record.member_of if not g.endswith(MEMBER_OF_PREDICATE_GROUP_SUFFIX) and g != "GM")
        by_id = {r.id: r for r in self._records}
        parties = [by_id.get(pid) or EntityRecord(id=pid, name=pid, kind=PARTY_KIND) for pid in party_ids]
        return self._match(parties, current)


def _to_record(entity_id: str, entity: dict[str, object]) -> EntityRecord:
    meta = entity.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    name = entity.get("name")
    kind = entity.get("kind")
    owner = meta.get("owner")
    member_of = entity.get("member_of")
    return EntityRecord(
        id=entity_id,
        name=name if isinstance(name, str) and name else entity_id,
        kind=kind if isinstance(kind, str) else None,
        owner=owner if isinstance(owner, str) else None,
        retired=meta.get("status") == RETIRED_STATUS,
        member_of=tuple(m for m in member_of if isinstance(m, str)) if isinstance(member_of, list) else (),
    )


__all__ = ["EntityIndex", "EntityRecord", "MAX_CHOICES"]
