import os
from pathlib import Path

import discord
from connectomeclient import ConnectomeClient
from discord import app_commands
from dotenv import load_dotenv

from .campaign import (
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

# Passed as ConnectomeClient's source_type so memories this bot writes are
# tagged "discord" rather than connectomeclient's generic "api" default.
MEMORY_SOURCE_TYPE = "discord"

_ = load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DISCORD_BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
# Optional: restricts slash-command sync to one guild for instant availability
# during development, instead of the up-to-an-hour propagation delay for a
# global sync. Unset in production so the bot registers commands globally.
DISCORD_GUILD_ID_ENV = "DISCORD_GUILD_ID"

# Comma-separated Discord user ids to grant GM visibility. Fixed config, not
# a slash command: nobody should be able to grant themselves GM visibility by
# typing a command, so this is the only path that asserts it (see #42).
DISCORD_GM_USER_IDS_ENV = "DISCORD_GM_USER_IDS"


def get_bot_token() -> str | None:
    return os.getenv(DISCORD_BOT_TOKEN_ENV)


def get_gm_user_ids() -> list[int]:
    raw = os.getenv(DISCORD_GM_USER_IDS_ENV, "")
    return [int(piece.strip()) for piece in raw.split(",") if piece.strip()]


class DaybidDiscordBot(discord.Client):
    """Discord client wiring slash commands to the Connectome memory service."""

    def __init__(self, connectome: ConnectomeClient | None = None) -> None:
        super().__init__(intents=discord.Intents.default())
        self.connectome = connectome or ConnectomeClient(source_type=MEMORY_SOURCE_TYPE)
        self.current_characters = CurrentCharacterStore()
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await assign_gm_relationships(self.connectome, get_gm_user_ids())

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

    @bot.tree.command(name="fact", description="Record a fact relationship between two entities.")
    @app_commands.describe(
        subject="The subject entity id",
        predicate="The relationship connecting subject to object, e.g. 'likes' or 'member_of'",
        object_entity_id="The object entity id",
        content="The memory content describing this fact",
    )
    @app_commands.rename(object_entity_id="object")
    async def fact(
        interaction: discord.Interaction, subject: str, predicate: str, object_entity_id: str, content: str
    ) -> None:
        message = await handle_relationship_claim(bot.connectome, content, subject, predicate, object_entity_id, "fact")
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="rumor", description="Record a rumored (unverified) relationship between two entities.")
    @app_commands.describe(
        subject="The subject entity id",
        predicate="The relationship connecting subject to object, e.g. 'likes' or 'member_of'",
        object_entity_id="The object entity id",
        content="The memory content describing this rumor",
    )
    @app_commands.rename(object_entity_id="object")
    async def rumor(
        interaction: discord.Interaction, subject: str, predicate: str, object_entity_id: str, content: str
    ) -> None:
        message = await handle_relationship_claim(bot.connectome, content, subject, predicate, object_entity_id, "rumor")
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="gm-note", description="Save a GM-only memory to Connectome.")
    @app_commands.describe(content="What to note")
    async def gm_note(interaction: discord.Interaction, content: str) -> None:
        message = await handle_gm_note(bot.connectome, content)
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="log", description="Log freeform session narration to Connectome.")
    @app_commands.describe(content="The narration text to log")
    async def log(interaction: discord.Interaction, content: str) -> None:
        message = await handle_log(bot.connectome, content)
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="add-character", description="Create a character and claim ownership of it.")
    @app_commands.describe(pc_name="The character's name")
    async def add_character(interaction: discord.Interaction, pc_name: str) -> None:
        message = await handle_add_character(bot.connectome, interaction.user.id, pc_name)
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="join-party", description="Join a party with a character you own.")
    @app_commands.describe(character_name="Your character's name", party_name="The party name to join")
    async def join_party(interaction: discord.Interaction, character_name: str, party_name: str) -> None:
        message = await handle_join_party(bot.connectome, interaction.user.id, character_name, party_name)
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="play-character", description="Set your current active character.")
    @app_commands.describe(pc_name="The character's name")
    async def play_character(interaction: discord.Interaction, pc_name: str) -> None:
        message = await handle_play_character(bot.connectome, bot.current_characters, interaction.user.id, pc_name)
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="retire-character", description="Retire a character you own.")
    @app_commands.describe(pc_name="The character's name")
    async def retire_character(interaction: discord.Interaction, pc_name: str) -> None:
        message = await handle_retire_character(bot.connectome, bot.current_characters, interaction.user.id, pc_name)
        await interaction.response.send_message(message, ephemeral=True)

    return bot


def main() -> None:
    token = get_bot_token()
    if not token:
        raise RuntimeError(f"{DISCORD_BOT_TOKEN_ENV} is not set")
    create_bot().run(token)
