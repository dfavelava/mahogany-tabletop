from types import SimpleNamespace

from discordbot.ask import (
    ANSWER_SCHEMA,
    ASK_K,
    DISCORD_MESSAGE_LIMIT,
    DONT_KNOW,
    citation_for,
    handle_ask,
    split_memory_document,
)
from discordbot.bot import create_bot
from discordbot.campaign import GM_GROUP_ID, WEST_MARCHES_TOME
from discordbot.identity import discord_entity_id
from discordbot.llm import LLMUnavailable

PLAYER = 1
GM = 99


def memory_doc(body: str, acl: list[str] | None = None, **fields: str) -> str:
    lines = ["---", "type: note", "created_at: 2026-09-01T00:00:00Z", "entities:", "  - someone"]
    lines += [f"{name}: {value}" for name, value in fields.items()]
    if acl is not None:
        lines.append("acl: []" if not acl else "acl:\n" + "\n".join(f"  - {group}" for group in acl))
    return "\n".join(lines) + f"\n---\n{body}\n"


class FakeRecallConnectome:
    """Fake recall that applies the backend's `as` scoping (see resolveACLScope).

    A memory is visible when its acl is empty, or names the `as` entity or
    one of the groups that entity is directly member_of.
    """

    def __init__(self, memories: dict[str, tuple[str, list[str]]], member_of: dict[str, list[str]]) -> None:
        self.memories = memories
        self.member_of = member_of
        self.recall_calls: list[dict[str, object]] = []

    async def recall(
        self,
        query: str,
        k: int = 5,
        hydrate: bool = False,
        as_: str | None = None,
        tome: str | None = None,
    ) -> dict[str, object]:
        assert tome == WEST_MARCHES_TOME
        self.recall_calls.append({"query": query, "k": k, "hydrate": hydrate, "as_": as_})
        scope = {as_, *self.member_of.get(as_ or "", [])}
        results = [
            {"key": key, "score": 1.0, "type": "note", "content": memory_doc(body, acl)}
            for key, (body, acl) in self.memories.items()
            if as_ is None or not acl or scope & set(acl)
        ]
        return {"results": results[:k]}


class FakeLLM:
    def __init__(self, reply: dict[str, object] | str | None = None, fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    async def chat(self, messages, schema=None, model=None):
        self.calls.append({"messages": messages, "schema": schema})
        if self.fail:
            raise LLMUnavailable("down")
        return self.reply

    async def aclose(self) -> None:
        pass

    def prompt(self) -> str:
        return "\n".join(m["content"] for m in self.calls[-1]["messages"])


def campaign_connectome() -> FakeRecallConnectome:
    return FakeRecallConnectome(
        memories={
            "mem_bridge.md": ("The bridge at Hollow Ford is out.", []),
            "mem_duke.md": ("The duke is secretly a lich.", [GM_GROUP_ID]),
        },
        member_of={discord_entity_id(GM): [GM_GROUP_ID]},
    )


async def test_ask_scopes_recall_to_the_callers_entity():
    connectome = campaign_connectome()
    llm = FakeLLM({"answer": "It's out.", "sources": [1]})

    await handle_ask(connectome, llm, PLAYER, "Is the bridge passable?")

    assert connectome.recall_calls == [
        {"query": "Is the bridge passable?", "k": ASK_K, "hydrate": True, "as_": discord_entity_id(PLAYER)}
    ]
    assert llm.calls[0]["schema"] == ANSWER_SCHEMA


async def test_gm_only_memories_never_reach_a_players_prompt():
    connectome = campaign_connectome()
    llm = FakeLLM({"answer": DONT_KNOW, "sources": []})

    await handle_ask(connectome, llm, PLAYER, "Who is the duke?")

    assert "Hollow Ford" in llm.prompt()
    assert "lich" not in llm.prompt()


async def test_gm_sees_gm_only_memories():
    connectome = campaign_connectome()
    llm = FakeLLM({"answer": "A lich.", "sources": [2]})

    reply = await handle_ask(connectome, llm, GM, "Who is the duke?")

    assert "lich" in llm.prompt()
    assert reply == "A lich.\n\nSources: `mem_duke.md`"


async def test_answer_cites_memory_keys():
    llm = FakeLLM({"answer": "The bridge is out.", "sources": [1, 1]})

    reply = await handle_ask(campaign_connectome(), llm, PLAYER, "Is the bridge passable?")

    assert reply == "The bridge is out.\n\nSources: `mem_bridge.md`"


async def test_uncited_or_invalidly_cited_answer_becomes_dont_know():
    for reply in ({"answer": "Probably fine.", "sources": []}, {"answer": "Yes.", "sources": [7]}, "free text"):
        llm = FakeLLM(reply)
        assert await handle_ask(campaign_connectome(), llm, PLAYER, "Is it safe?") == DONT_KNOW


async def test_no_visible_memories_says_dont_know_without_calling_llm():
    connectome = FakeRecallConnectome({"mem_duke.md": ("The duke is secretly a lich.", [GM_GROUP_ID])}, {})
    llm = FakeLLM({"answer": "A lich.", "sources": [1]})

    assert await handle_ask(connectome, llm, PLAYER, "Who is the duke?") == DONT_KNOW
    assert llm.calls == []


async def test_llm_unavailable_falls_back_to_cited_snippets():
    connectome = campaign_connectome()

    reply = await handle_ask(connectome, FakeLLM(fail=True), PLAYER, "Is the bridge passable?")

    assert "`mem_bridge.md`: The bridge at Hollow Ford is out." in reply
    assert "lich" not in reply


async def test_fallback_fits_in_one_discord_message():
    connectome = FakeRecallConnectome({f"mem_{i}.md": ("word " * 500, []) for i in range(ASK_K)}, {})

    reply = await handle_ask(connectome, FakeLLM(fail=True), PLAYER, "anything?")

    assert len(reply) <= DISCORD_MESSAGE_LIMIT
    assert "`mem_0.md`" in reply


def test_split_memory_document_reads_top_level_fields():
    meta, body = split_memory_document(memory_doc("Body text.", [GM_GROUP_ID], session="s3"))

    assert meta["session"] == "s3"
    assert meta["created_at"] == "2026-09-01T00:00:00Z"
    assert "entities" not in meta
    assert body == "Body text."


def test_citation_prefers_session_and_timestamp():
    assert citation_for("mem_a.md", {"session": "s3", "occurred_at": "2026-09-01T20:15:00Z"}) == (
        "session s3 @ 2026-09-01T20:15:00Z"
    )
    assert citation_for("mem_a.md", {"created_at": "2026-09-01T00:00:00Z"}) == "mem_a.md"


class FakeResponse:
    def __init__(self) -> None:
        self.deferred: dict[str, object] | None = None

    async def defer(self, **kwargs) -> None:
        self.deferred = kwargs


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send(self, content: str, ephemeral: bool = False) -> None:
        self.sent.append((content, ephemeral))


async def test_ask_command_defers_ephemerally_then_answers():
    bot = create_bot()
    bot.connectome = campaign_connectome()
    bot.llm = FakeLLM({"answer": "The bridge is out.", "sources": [1]})
    interaction = SimpleNamespace(user=SimpleNamespace(id=PLAYER), response=FakeResponse(), followup=FakeFollowup())

    command = bot.tree.get_command("ask")
    assert command is not None
    await command.callback(interaction, "Is the bridge passable?")

    assert interaction.response.deferred is not None
    assert interaction.response.deferred["ephemeral"] is True
    assert interaction.followup.sent == [("The bridge is out.\n\nSources: `mem_bridge.md`", True)]
