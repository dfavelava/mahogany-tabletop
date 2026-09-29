from test_bot import WEST_MARCHES_TOME, FakeConnectomeClient

from discordbot.entities import MAX_CHOICES, EntityIndex


class ListingClient(FakeConnectomeClient):
    def __init__(self, entities: dict[str, dict[str, object]]) -> None:
        super().__init__(entities)
        self.browse_calls = 0
        self.get_entity_calls = 0

    async def browse_all(self, tome: str | None = None) -> dict[str, object]:
        assert tome == WEST_MARCHES_TOME
        self.browse_calls += 1
        keys = [f"ent_{eid}.json" for eid in self.entities] + ["mem_1.md"]
        return {"Contents": [{"Key": k} for k in keys]}

    async def get_entity(self, entity_id: str, tome: str | None = None) -> dict[str, object] | None:
        self.get_entity_calls += 1
        return await super().get_entity(entity_id, tome=tome)


def character(name: str, owner: str, status: str | None = None) -> dict[str, object]:
    meta: dict[str, object] = {"owner": owner}
    if status:
        meta["status"] = status
    return {"name": name, "kind": "character", "meta": meta}


async def make_index(entities: dict[str, dict[str, object]]) -> tuple[EntityIndex, ListingClient]:
    client = ListingClient(entities)
    index = EntityIndex(client, WEST_MARCHES_TOME)  # type: ignore[arg-type]
    await index.refresh()
    return index, client


async def test_character_choices_only_owned_and_not_retired():
    index, _ = await make_index(
        {
            "bram": character("Bram", "discord-1"),
            "brin": character("Brin", "discord-1", status="retired"),
            "bree": character("Bree", "discord-2"),
            "discord-1": {"name": "Alice", "kind": "player"},
        }
    )

    choices = index.character_choices("", user_id=1)

    assert [(c.name, c.value) for c in choices] == [("Bram", "bram")]


async def test_choices_prefix_match_is_case_insensitive_and_submits_id():
    index, _ = await make_index({"ent_bram": {"name": "Bram the Bold"}, "orin": {"name": "Orin"}})

    choices = index.entity_choices("bram")

    assert [(c.name, c.value) for c in choices] == [("Bram the Bold", "ent_bram")]


async def test_choices_capped_at_25():
    index, _ = await make_index({f"npc{i:02d}": {"name": f"Npc {i:02d}"} for i in range(40)})

    assert len(index.entity_choices("")) == MAX_CHOICES == 25
    assert len(index.entity_choices("npc")) == 25


async def test_party_choices_include_member_of_targets_but_not_groups():
    index, _ = await make_index(
        {
            "bram": {"name": "Bram", "kind": "character", "member_of": ["The Lanterns", "discord-1-characters", "GM"]},
        }
    )

    assert [c.value for c in index.party_choices("")] == ["The Lanterns"]


async def test_autocomplete_uses_cache_only():
    index, client = await make_index({"bram": character("Bram", "discord-1")})
    browse, gets = client.browse_calls, client.get_entity_calls

    index.character_choices("b", user_id=1)
    index.entity_choices("b")
    index.party_choices("b")
    index.maybe_refresh()  # fresh cache, within TTL: no refresh

    assert (client.browse_calls, client.get_entity_calls) == (browse, gets)


async def test_mark_dirty_refreshes_in_background():
    index, client = await make_index({"bram": character("Bram", "discord-1")})
    client.entities["brin"] = character("Brin", "discord-1")

    index.mark_dirty()
    assert index._task is not None
    await index._task

    assert {c.value for c in index.character_choices("", user_id=1)} == {"bram", "brin"}


async def test_ttl_expiry_triggers_refresh():
    now = [0.0]
    client = ListingClient({"bram": character("Bram", "discord-1")})
    index = EntityIndex(client, WEST_MARCHES_TOME, ttl=10, clock=lambda: now[0])  # type: ignore[arg-type]
    await index.refresh()

    now[0] = 5
    index.maybe_refresh()
    assert client.browse_calls == 1

    now[0] = 11
    index.maybe_refresh()
    assert index._task is not None
    await index._task
    assert client.browse_calls == 2
