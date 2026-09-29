from types import SimpleNamespace

import discord
import pytest
from connectomeclient import Relationship

from discordbot.bot import create_bot, get_gm_user_ids
from discordbot.campaign import (
    GM_GROUP_ID,
    WEST_MARCHES_TOME,
    CurrentCharacterStore,
    assign_gm_relationships,
    handle_add_character,
    handle_gm_note,
    handle_join_party,
    handle_log,
    handle_play_character,
    handle_relationship_claim,
    handle_remember,
    handle_retire_character,
)
from discordbot.identity import character_entity_id, discord_entity_id


def require_west_marches(tome: str | None) -> None:
    """Fail any client call that omits the tome or targets a different one.

    Every FakeConnectomeClient method calls this, so every handler test also
    verifies the bot never touches a tome other than west-marches.
    """
    assert tome == WEST_MARCHES_TOME, f"expected tome {WEST_MARCHES_TOME!r}, got {tome!r}"


class FakeConnectomeClient:
    def __init__(self, entities: dict[str, dict[str, object]] | None = None) -> None:
        self.remember_calls: list[dict[str, object]] = []
        self.assert_relationship_calls: list[dict[str, object]] = []
        self.supersede_relationship_calls: list[dict[str, object]] = []
        self.entities: dict[str, dict[str, object]] = entities or {}

    async def remember(
        self,
        content: str,
        memory_type: str = "note",
        entities: list[str] | None = None,
        relationships: list[Relationship] | None = None,
        acl: list[str] | None = None,
        tome: str | None = None,
    ) -> dict[str, str]:
        require_west_marches(tome)
        self.remember_calls.append(
            {
                "content": content,
                "memory_type": memory_type,
                "entities": entities,
                "relationships": relationships,
                "acl": acl,
            }
        )
        return {"key": f"mem_test_{len(self.remember_calls)}.md"}

    async def supersede_relationship(
        self,
        key: str,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str | None = None,
        superseded_by: str | None = None,
        tome: str | None = None,
    ) -> dict[str, object]:
        require_west_marches(tome)
        self.supersede_relationship_calls.append(
            {
                "key": key,
                "subject_entity_id": subject_entity_id,
                "predicate": predicate,
                "object_entity_id": object_entity_id,
                "superseded_by": superseded_by,
            }
        )
        return {"message": "success", "key": key}

    async def get_entity(self, entity_id: str, tome: str | None = None) -> dict[str, object] | None:
        require_west_marches(tome)
        return self.entities.get(entity_id)

    async def assert_relationship(
        self,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str | None = None,
        kind: str | None = None,
        subject_kind: str | None = None,
        subject_meta: dict[str, object] | None = None,
        tome: str | None = None,
    ) -> dict[str, object]:
        require_west_marches(tome)
        self.assert_relationship_calls.append(
            {
                "subject_entity_id": subject_entity_id,
                "predicate": predicate,
                "object_entity_id": object_entity_id,
                "kind": kind,
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
        {
            "content": "David prefers tea over coffee.",
            "memory_type": "note",
            "entities": ["discord-123456"],
            "relationships": None,
            "acl": None,
        }
    ]
    assert "David prefers tea over coffee." in message


async def test_handle_relationship_claim_records_memory_and_asserts_relationship():
    connectome = FakeConnectomeClient()

    message = await handle_relationship_claim(
        connectome,
        content="David likes tea.",
        subject_entity_id="discord-123456",
        predicate="likes",
        object_entity_id="tea",
        kind="fact",
    )

    assert connectome.remember_calls == [
        {
            "content": "David likes tea.",
            "memory_type": "note",
            "entities": ["discord-123456", "tea"],
            "relationships": [
                {
                    "subjectEntityId": "discord-123456",
                    "predicate": "likes",
                    "objectEntityId": "tea",
                    "kind": "fact",
                }
            ],
            "acl": None,
        }
    ]
    assert connectome.assert_relationship_calls == [
        {
            "subject_entity_id": "discord-123456",
            "predicate": "likes",
            "object_entity_id": "tea",
            "kind": "fact",
            "subject_kind": None,
            "subject_meta": None,
        }
    ]
    assert message == "Recorded fact: discord-123456 likes tea."


async def test_handle_relationship_claim_records_rumor_kind():
    connectome = FakeConnectomeClient()

    message = await handle_relationship_claim(
        connectome,
        content="Word is the mayor is a vampire.",
        subject_entity_id="mayor",
        predicate="is",
        object_entity_id="vampire",
        kind="rumor",
    )

    assert connectome.remember_calls[0]["relationships"] == [
        {
            "subjectEntityId": "mayor",
            "predicate": "is",
            "objectEntityId": "vampire",
            "kind": "rumor",
        }
    ]
    assert connectome.assert_relationship_calls[0]["kind"] == "rumor"
    assert message == "Recorded rumor: mayor is vampire."


async def test_handle_relationship_claim_dedupes_entities_when_subject_equals_object():
    connectome = FakeConnectomeClient()

    _ = await handle_relationship_claim(
        connectome,
        content="The mirror reflects itself.",
        subject_entity_id="mirror",
        predicate="reflects",
        object_entity_id="mirror",
        kind="fact",
    )

    assert connectome.remember_calls[0]["entities"] == ["mirror"]


async def test_handle_gm_note_scopes_memory_to_gm_group():
    connectome = FakeConnectomeClient()

    message = await handle_gm_note(connectome, "The BBEG's real name is Vecna.")

    assert connectome.remember_calls == [
        {
            "content": "The BBEG's real name is Vecna.",
            "memory_type": "note",
            "entities": None,
            "relationships": None,
            "acl": [GM_GROUP_ID],
        }
    ]
    assert "The BBEG's real name is Vecna." in message


async def test_handle_log_stores_content_as_an_event_memory():
    connectome = FakeConnectomeClient()

    message = await handle_log(connectome, "The party enters the ruined keep.")

    assert connectome.remember_calls == [
        {
            "content": "The party enters the ruined keep.",
            "memory_type": "event",
            "entities": None,
            "relationships": None,
            "acl": None,
        }
    ]
    assert "The party enters the ruined keep." in message


async def test_handle_join_party_asserts_relationship_for_owned_character():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-123456"}}})

    message = await handle_join_party(connectome, user_id=123456, character_name="Thorin", party_name="Party A")

    assert connectome.assert_relationship_calls == [
        {
            "subject_entity_id": "thorin",
            "predicate": "member_of",
            "object_entity_id": "Party A",
            "kind": None,
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


async def test_handle_play_character_sets_current_character_for_owned_character():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-123456"}}})
    current_characters = CurrentCharacterStore()

    message = await handle_play_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.remember_calls[0]["relationships"] == [
        {
            "subjectEntityId": "discord-123456",
            "predicate": "plays",
            "objectEntityId": "thorin",
            "kind": "fact",
        }
    ]
    assert connectome.remember_calls[0]["entities"] == ["discord-123456", "thorin"]
    assert connectome.supersede_relationship_calls == []
    assert current_characters.get("discord-123456") == ("thorin", "mem_test_1.md")
    assert message == "You are now playing Thorin."


async def test_handle_play_character_refuses_when_caller_does_not_own_character():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-999"}}})
    current_characters = CurrentCharacterStore()

    message = await handle_play_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.remember_calls == []
    assert "don't own" in message


async def test_handle_play_character_refuses_when_character_does_not_exist():
    connectome = FakeConnectomeClient()
    current_characters = CurrentCharacterStore()

    message = await handle_play_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.remember_calls == []
    assert "don't own" in message


async def test_handle_play_character_refuses_when_character_is_retired():
    connectome = FakeConnectomeClient(
        entities={"thorin": {"meta": {"owner": "discord-123456", "status": "retired"}}}
    )
    current_characters = CurrentCharacterStore()

    message = await handle_play_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.remember_calls == []
    assert "retired" in message


async def test_handle_play_character_is_a_no_op_when_already_current():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-123456"}}})
    current_characters = CurrentCharacterStore()
    current_characters.set("discord-123456", "thorin", "mem_existing.md")

    message = await handle_play_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.remember_calls == []
    assert connectome.supersede_relationship_calls == []
    assert message == "Thorin is already your current character."


async def test_handle_play_character_supersedes_the_prior_current_character():
    connectome = FakeConnectomeClient(
        entities={
            "thorin": {"meta": {"owner": "discord-123456"}},
            "balin": {"meta": {"owner": "discord-123456"}},
        }
    )
    current_characters = CurrentCharacterStore()
    current_characters.set("discord-123456", "thorin", "mem_thorin.md")

    message = await handle_play_character(connectome, current_characters, user_id=123456, pc_name="Balin")

    assert connectome.supersede_relationship_calls == [
        {
            "key": "mem_thorin.md",
            "subject_entity_id": "discord-123456",
            "predicate": "plays",
            "object_entity_id": "thorin",
            "superseded_by": "mem_test_1.md",
        }
    ]
    assert current_characters.get("discord-123456") == ("balin", "mem_test_1.md")
    assert message == "You are now playing Balin."


async def test_handle_retire_character_marks_owned_character_retired():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-123456"}}})
    current_characters = CurrentCharacterStore()

    message = await handle_retire_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.assert_relationship_calls == [
        {
            "subject_entity_id": "thorin",
            "predicate": "retired",
            "object_entity_id": None,
            "kind": None,
            "subject_kind": None,
            "subject_meta": {"status": "retired"},
        }
    ]
    assert message == "Thorin has been retired."


async def test_handle_retire_character_refuses_when_caller_does_not_own_character():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-999"}}})
    current_characters = CurrentCharacterStore()

    message = await handle_retire_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.assert_relationship_calls == []
    assert "don't own" in message


async def test_handle_retire_character_refuses_when_character_does_not_exist():
    connectome = FakeConnectomeClient()
    current_characters = CurrentCharacterStore()

    message = await handle_retire_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.assert_relationship_calls == []
    assert "don't own" in message


async def test_handle_retire_character_is_a_no_op_when_already_retired():
    connectome = FakeConnectomeClient(
        entities={"thorin": {"meta": {"owner": "discord-123456", "status": "retired"}}}
    )
    current_characters = CurrentCharacterStore()

    message = await handle_retire_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.assert_relationship_calls == []
    assert connectome.remember_calls == []
    assert message == "Thorin is already retired."


async def test_handle_retire_character_leaves_other_owned_characters_untouched():
    connectome = FakeConnectomeClient(
        entities={
            "thorin": {"meta": {"owner": "discord-123456"}},
            "balin": {"meta": {"owner": "discord-123456"}},
        }
    )
    current_characters = CurrentCharacterStore()

    _ = await handle_retire_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.entities["balin"] == {"meta": {"owner": "discord-123456"}}


async def test_handle_retire_character_clears_current_character_when_retiring_it():
    connectome = FakeConnectomeClient(entities={"thorin": {"meta": {"owner": "discord-123456"}}})
    current_characters = CurrentCharacterStore()
    current_characters.set("discord-123456", "thorin", "mem_thorin.md")

    message = await handle_retire_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.remember_calls[0]["relationships"] == [
        {
            "subjectEntityId": "discord-123456",
            "predicate": "plays",
            "objectEntityId": None,
            "kind": "fact",
        }
    ]
    assert connectome.supersede_relationship_calls == [
        {
            "key": "mem_thorin.md",
            "subject_entity_id": "discord-123456",
            "predicate": "plays",
            "object_entity_id": "thorin",
            "superseded_by": "mem_test_1.md",
        }
    ]
    assert current_characters.get("discord-123456") == (None, "mem_test_1.md")
    assert message == "Thorin has been retired."


async def test_handle_retire_character_leaves_current_character_untouched_when_retiring_a_different_one():
    connectome = FakeConnectomeClient(
        entities={
            "thorin": {"meta": {"owner": "discord-123456"}},
            "balin": {"meta": {"owner": "discord-123456"}},
        }
    )
    current_characters = CurrentCharacterStore()
    current_characters.set("discord-123456", "balin", "mem_balin.md")

    message = await handle_retire_character(connectome, current_characters, user_id=123456, pc_name="Thorin")

    assert connectome.remember_calls == []
    assert connectome.supersede_relationship_calls == []
    assert current_characters.get("discord-123456") == ("balin", "mem_balin.md")
    assert message == "Thorin has been retired."


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
            "kind": None,
            "subject_kind": None,
            "subject_meta": None,
        },
        {
            "subject_entity_id": "discord-222",
            "predicate": "member_of",
            "object_entity_id": GM_GROUP_ID,
            "kind": None,
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


def test_create_bot_registers_fact_command():
    bot = create_bot()

    command = bot.tree.get_command("fact")

    assert command is not None
    assert command.name == "fact"


def test_create_bot_registers_rumor_command():
    bot = create_bot()

    command = bot.tree.get_command("rumor")

    assert command is not None
    assert command.name == "rumor"


def test_create_bot_registers_gm_note_command():
    bot = create_bot()

    command = bot.tree.get_command("gm-note")

    assert command is not None
    assert command.name == "gm-note"


def test_create_bot_registers_log_command():
    bot = create_bot()

    command = bot.tree.get_command("log")

    assert command is not None
    assert command.name == "log"


def test_create_bot_registers_play_character_command():
    bot = create_bot()

    command = bot.tree.get_command("play-character")

    assert command is not None
    assert command.name == "play-character"


def test_create_bot_registers_retire_character_command():
    bot = create_bot()

    command = bot.tree.get_command("retire-character")

    assert command is not None
    assert command.name == "retire-character"


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
            "kind": None,
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
            "kind": None,
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


def test_west_marches_tome_id():
    assert WEST_MARCHES_TOME == "west-marches"


async def test_fake_client_rejects_calls_without_the_west_marches_tome():
    connectome = FakeConnectomeClient()

    with pytest.raises(AssertionError):
        _ = await connectome.remember("no tome given")
    with pytest.raises(AssertionError):
        _ = await connectome.get_entity("thorin", tome="some-other-tome")


class FakeResponse:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []
        self.modal: discord.ui.Modal | None = None

    async def send_message(self, content: str, ephemeral: bool = False) -> None:
        self.sent.append((content, ephemeral))

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self.modal = modal


def make_interaction(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(id=user_id), response=FakeResponse())


def make_message(author_id: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(author=SimpleNamespace(id=author_id), content=content)


def message_command(bot, name: str):
    command = bot.tree.get_command(name, type=discord.AppCommandType.message)
    assert command is not None
    return command


async def test_remember_this_attributes_memory_to_message_author():
    bot = create_bot()
    bot.connectome = FakeConnectomeClient()
    interaction = make_interaction(1)

    await message_command(bot, "Remember this").callback(interaction, make_message(2, "The bridge is out"))

    assert bot.connectome.remember_calls[0]["entities"] == [discord_entity_id(2)]
    assert interaction.response.sent == [("Remembered: The bridge is out", True)]


async def test_log_as_rumor_opens_modal_and_records_rumor_on_submit():
    bot = create_bot()
    bot.connectome = FakeConnectomeClient()
    interaction = make_interaction(1)

    await message_command(bot, "Log as rumor").callback(interaction, make_message(2, "Bob serves the duke"))
    modal = interaction.response.modal
    assert modal is not None
    modal.subject._value = "bob"
    modal.predicate._value = "serves"
    modal.object_entity_id._value = "duke"
    submit = make_interaction(1)
    await modal.on_submit(submit)

    assert bot.connectome.assert_relationship_calls[0]["kind"] == "rumor"
    assert bot.connectome.remember_calls[0]["content"] == "Bob serves the duke"
    assert submit.response.sent[0][1] is True


async def test_gm_note_rejects_non_gm(monkeypatch):
    monkeypatch.setenv("DISCORD_GM_USER_IDS", "99")
    bot = create_bot()
    bot.connectome = FakeConnectomeClient()
    interaction = make_interaction(1)

    await message_command(bot, "GM note").callback(interaction, make_message(2, "secret"))

    assert bot.connectome.remember_calls == []
    assert interaction.response.sent == [("Only GMs can save GM notes.", True)]


async def test_gm_note_saves_for_gm(monkeypatch):
    monkeypatch.setenv("DISCORD_GM_USER_IDS", "99")
    bot = create_bot()
    bot.connectome = FakeConnectomeClient()
    interaction = make_interaction(99)

    await message_command(bot, "GM note").callback(interaction, make_message(2, "secret"))

    assert bot.connectome.remember_calls[0]["acl"] == [GM_GROUP_ID]
    assert interaction.response.sent == [("Noted (GM only): secret", True)]


async def test_message_commands_reply_ephemerally_when_message_has_no_text(monkeypatch):
    monkeypatch.setenv("DISCORD_GM_USER_IDS", "1")
    bot = create_bot()
    bot.connectome = FakeConnectomeClient()
    for name in ("Remember this", "Log as rumor", "GM note"):
        interaction = make_interaction(1)
        await message_command(bot, name).callback(interaction, make_message(2, ""))
        assert interaction.response.sent == [("That message has no text to save.", True)]
    assert bot.connectome.remember_calls == []
