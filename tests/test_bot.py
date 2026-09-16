from discordbot.bot import (
    GM_GROUP_ID,
    assign_gm_relationships,
    create_bot,
    get_gm_user_ids,
    handle_add_character,
    handle_join_party,
    handle_remember,
)
from discordbot.identity import character_entity_id, discord_entity_id


class FakeConnectomeClient:
    def __init__(self, entities: dict[str, dict[str, object]] | None = None) -> None:
        self.remember_calls: list[dict[str, object]] = []
        self.assert_relationship_calls: list[dict[str, object]] = []
        self.entities: dict[str, dict[str, object]] = entities or {}

    async def remember(self, content: str, entities: list[str] | None = None) -> dict[str, str]:
        self.remember_calls.append({"content": content, "entities": entities})
        return {"key": "mem_test.md"}

    async def get_entity(self, entity_id: str) -> dict[str, object] | None:
        return self.entities.get(entity_id)

    async def assert_relationship(
        self,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str | None = None,
        kind: str | None = None,
        subject_kind: str | None = None,
        subject_meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.assert_relationship_calls.append(
            {
                "subject_entity_id": subject_entity_id,
                "predicate": predicate,
                "object_entity_id": object_entity_id,
                "subject_kind": subject_kind,
                "subject_meta": subject_meta,
            }
        )
        self.entities[subject_entity_id] = {
            "id": subject_entity_id,
            "kind": subject_kind,
            "meta": subject_meta,
        }
        return {"subject": self.entities[subject_entity_id]}


def test_discord_entity_id_is_deterministic():
    assert discord_entity_id(123456) == "discord-123456"
    assert discord_entity_id(123456) == discord_entity_id(123456)


async def test_handle_remember_stores_memory_under_deterministic_entity_id():
    connectome = FakeConnectomeClient()

    message = await handle_remember(connectome, user_id=123456, content="David prefers tea over coffee.")

    assert connectome.remember_calls == [
        {"content": "David prefers tea over coffee.", "entities": ["discord-123456"]}
    ]
    assert "David prefers tea over coffee." in message


async def test_handle_join_party_asserts_relationship_for_owned_character():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-123456"}}})

    message = await handle_join_party(connectome, user_id=123456, character_name="Thorin", party_name="Party A")

    assert connectome.assert_relationship_calls == [
        {
            "subject_entity_id": "thorin",
            "predicate": "member_of",
            "object_entity_id": "Party A",
            "subject_kind": None,
            "subject_meta": None,
        }
    ]
    assert message == "Thorin joined Party A."


async def test_handle_join_party_refuses_when_caller_does_not_own_character():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-999"}}})

    message = await handle_join_party(connectome, user_id=123456, character_name="Thorin", party_name="Party A")

    assert connectome.assert_relationship_calls == []
    assert "don't own" in message


async def test_handle_join_party_refuses_when_character_does_not_exist():
    connectome = FakeConnectomeClient()

    message = await handle_join_party(connectome, user_id=123456, character_name="Thorin", party_name="Party A")

    assert connectome.assert_relationship_calls == []
    assert "don't own" in message


def test_get_gm_user_ids_parses_comma_separated_list(monkeypatch):
    monkeypatch.setenv("DISCORD_GM_USER_IDS", "111, 222,333")

    assert get_gm_user_ids() == [111, 222, 333]


def test_get_gm_user_ids_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("DISCORD_GM_USER_IDS", raising=False)

    assert get_gm_user_ids() == []


async def test_assign_gm_relationships_asserts_member_of_gm_for_each_id():
    connectome = FakeConnectomeClient()

    await assign_gm_relationships(connectome, [111, 222])

    assert connectome.assert_relationship_calls == [
        {
            "subject_entity_id": "discord-111",
            "predicate": "member_of",
            "object_entity_id": GM_GROUP_ID,
            "subject_kind": None,
            "subject_meta": None,
        },
        {
            "subject_entity_id": "discord-222",
            "predicate": "member_of",
            "object_entity_id": GM_GROUP_ID,
            "subject_kind": None,
            "subject_meta": None,
        },
    ]


def test_create_bot_registers_remember_command():
    bot = create_bot()

    command = bot.tree.get_command("remember")

    assert command is not None
    assert command.name == "remember"


def test_create_bot_registers_add_character_command():
    bot = create_bot()

    command = bot.tree.get_command("add-character")

    assert command is not None
    assert command.name == "add-character"


def test_create_bot_registers_join_party_command():
    bot = create_bot()

    command = bot.tree.get_command("join-party")

    assert command is not None
    assert command.name == "join-party"


def test_character_entity_id_is_slugified_and_deterministic():
    assert character_entity_id("Thorin") == "thorin"
    assert character_entity_id("Thorin") == character_entity_id("thorin")
    assert character_entity_id("Dwalin Ironfist") == "dwalin-ironfist"


async def test_handle_add_character_creates_character_owned_by_caller():
    connectome = FakeConnectomeClient()

    message = await handle_add_character(connectome, user_id=123456, pc_name="Thorin")

    assert connectome.assert_relationship_calls == [
        {
            "subject_entity_id": "thorin",
            "predicate": "member_of",
            "object_entity_id": "discord-123456-characters",
            "subject_kind": "character",
            "subject_meta": {"owner": "discord-123456"},
        }
    ]
    assert "Thorin" in message


async def test_handle_add_character_refuses_when_owned_by_a_different_player():
    connectome = FakeConnectomeClient(
        entities={"thorin": {"id": "thorin", "kind": "character", "meta": {"owner": "discord-999"}}}
    )

    message = await handle_add_character(connectome, user_id=123456, pc_name="Thorin")

    assert connectome.assert_relationship_calls == []
    assert "already claimed" in message


async def test_handle_add_character_is_a_no_op_when_caller_already_owns_it():
    connectome = FakeConnectomeClient(
        entities={"thorin": {"id": "thorin", "kind": "character", "meta": {"owner": "discord-123456"}}}
    )

    message = await handle_add_character(connectome, user_id=123456, pc_name="Thorin")

    assert connectome.assert_relationship_calls == [
        {
            "subject_entity_id": "thorin",
            "predicate": "member_of",
            "object_entity_id": "discord-123456-characters",
            "subject_kind": "character",
            "subject_meta": {"owner": "discord-123456"},
        }
    ]
    assert "already claimed" not in message


async def test_handle_add_character_allows_the_same_player_to_add_a_second_character():
    connectome = FakeConnectomeClient(
        entities={"thorin": {"id": "thorin", "kind": "character", "meta": {"owner": "discord-123456"}}}
    )

    message = await handle_add_character(connectome, user_id=123456, pc_name="Balin")

    assert message == "Balin is now yours."
    assert connectome.entities["thorin"]["meta"] == {"owner": "discord-123456"}
    assert connectome.entities["balin"]["meta"] == {"owner": "discord-123456"}
