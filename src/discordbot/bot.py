import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from .connectome_client import ConnectomeClient
from .identity import character_entity_id, discord_entity_id

CHARACTER_KIND = "character"
CHARACTERS_GROUP_SUFFIX = "-characters"

_ = load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DISCORD_BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
# Optional: restricts slash-command sync to one guild for instant availability
# during development, instead of the up-to-an-hour propagation delay for a
# global sync. Unset in production so the bot registers commands globally.
DISCORD_GUILD_ID_ENV = "DISCORD_GUILD_ID"


def get_bot_token() -> str | None:
    return os.getenv(DISCORD_BOT_TOKEN_ENV)


async def handle_remember(connectome: ConnectomeClient, user_id: int, content: str) -> str:
    """Store content as a memory attributed to the calling Discord user and return a confirmation."""
    entity_id = discord_entity_id(user_id)
    _ = await connectome.remember(content, entities=[entity_id])
    return f"Remembered: {content}"


async def handle_add_character(connectome: ConnectomeClient, user_id: int, pc_name: str) -> str:
    """Resolve-or-create a PC entity and assign it to the calling Discord user.

    Refuses if the name is already owned by a different player; re-running
    with a name the caller already owns is a harmless no-op. The ownership
    check happens here rather than in the backend, which stays agnostic of
    what "owner" means - it just persists and merges whatever meta a caller
    passes (see UpsertEntityRelationship in backend/resources/entity.go).
    """
    player_id = discord_entity_id(user_id)
    character_id = character_entity_id(pc_name)

    existing = await connectome.get_entity(character_id)
    existing_meta = existing.get("meta") if existing else None
    existing_owner = existing_meta.get("owner") if isinstance(existing_meta, dict) else None
    if existing_owner is not None and existing_owner != player_id:
        return f"{pc_name} is already claimed by another player."

    _ = await connectome.assert_relationship(
        character_id,
        "member_of",
        f"{player_id}{CHARACTERS_GROUP_SUFFIX}",
        subject_kind=CHARACTER_KIND,
        subject_meta={"owner": player_id},
    )
    return f"{pc_name} is now yours."


class DaybidDiscordBot(discord.Client):
    """Discord client wiring slash commands to the Connectome memory service."""

    def __init__(self, connectome: ConnectomeClient | None = None) -> None:
        super().__init__(intents=discord.Intents.default())
        self.connectome = connectome or ConnectomeClient()
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        guild_id = os.getenv(DISCORD_GUILD_ID_ENV)
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            _ = await self.tree.sync(guild=guild)
        else:
            _ = await self.tree.sync()


def create_bot() -> DaybidDiscordBot:
    bot = DaybidDiscordBot()

    @bot.tree.command(name="remember", description="Save a memory to Connectome.")
    @app_commands.describe(content="What to remember")
    async def remember(interaction: discord.Interaction, content: str) -> None:
        message = await handle_remember(bot.connectome, interaction.user.id, content)
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="add-character", description="Create a character and claim ownership of it.")
    @app_commands.describe(pc_name="The character's name")
    async def add_character(interaction: discord.Interaction, pc_name: str) -> None:
        message = await handle_add_character(bot.connectome, interaction.user.id, pc_name)
        await interaction.response.send_message(message, ephemeral=True)

    return bot


def main() -> None:
    token = get_bot_token()
    if not token:
        raise RuntimeError(f"{DISCORD_BOT_TOKEN_ENV} is not set")
    create_bot().run(token)
