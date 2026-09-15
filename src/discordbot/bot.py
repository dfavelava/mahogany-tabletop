import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from .connectome_client import ConnectomeClient
from .identity import discord_entity_id

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

    return bot


def main() -> None:
    token = get_bot_token()
    if not token:
        raise RuntimeError(f"{DISCORD_BOT_TOKEN_ENV} is not set")
    create_bot().run(token)
