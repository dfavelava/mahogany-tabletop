from discordbot.bot import create_bot, handle_remember
from discordbot.identity import discord_entity_id


class FakeConnectomeClient:
    def __init__(self) -> None:
        self.remember_calls: list[dict[str, object]] = []

    async def remember(self, content: str, entities: list[str] | None = None) -> dict[str, str]:
        self.remember_calls.append({"content": content, "entities": entities})
        return {"key": "mem_test.md"}


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


def test_create_bot_registers_remember_command():
    bot = create_bot()

    command = bot.tree.get_command("remember")

    assert command is not None
    assert command.name == "remember"
