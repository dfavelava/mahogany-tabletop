import os
from pathlib import Path

import discord
from connectomeclient import ConnectomeClient
from discord import app_commands
from dotenv import load_dotenv

from .ask import handle_ask
from .campaign import (
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
from .entities import EntityIndex
from .llm import LLMClient

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


class LogAsRumorModal(discord.ui.Modal, title="Log as rumor"):
    """Collects a subject/predicate/object for a rumor sourced from a message.

    Free text rather than #5's autocomplete suggestions: Discord modals only
    support plain text inputs, not autocomplete.
    """

    subject = discord.ui.TextInput(label="Subject entity id", max_length=100)
    predicate = discord.ui.TextInput(label="Predicate", placeholder="e.g. member_of", max_length=100)
    object_entity_id = discord.ui.TextInput(label="Object entity id", max_length=100)

    def __init__(self, connectome: ConnectomeClient, content: str) -> None:
        super().__init__()
        self.connectome = connectome
        self.content = content

    async def on_submit(self, interaction: discord.Interaction) -> None:
        message = await handle_relationship_claim(
            self.connectome,
            self.content,
            self.subject.value.strip(),
            self.predicate.value.strip(),
            self.object_entity_id.value.strip(),
            "rumor",
        )
        await interaction.response.send_message(message, ephemeral=True)


NO_TEXT_REPLY = "That message has no text to save."
GM_ONLY_REPLY = "Only GMs can save GM notes."


class DaybidDiscordBot(discord.Client):
    """Discord client wiring slash commands to the Connectome memory service."""

    def __init__(self, connectome: ConnectomeClient | None = None, llm: LLMClient | None = None) -> None:
        super().__init__(intents=discord.Intents.default())
        self.connectome = connectome or ConnectomeClient(source_type=MEMORY_SOURCE_TYPE)
        # Constructing LLMClient makes no network call, so the bot starts
        # fine while Ollama is down; /ask degrades on LLMUnavailable.
        self.llm = llm or LLMClient()
        self.current_characters = CurrentCharacterStore()
        self.entity_index = EntityIndex(self.connectome, WEST_MARCHES_TOME)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await assign_gm_relationships(self.connectome, get_gm_user_ids())
        self.entity_index.mark_dirty()

        guild_id = os.getenv(DISCORD_GUILD_ID_ENV)
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            _ = await self.tree.sync(guild=guild)
        else:
            _ = await self.tree.sync()

    async def close(self) -> None:
        await self.llm.aclose()
        await super().close()


def create_bot() -> DaybidDiscordBot:
    bot = DaybidDiscordBot()

    async def character_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        bot.entity_index.maybe_refresh()
        return bot.entity_index.character_choices(current, interaction.user.id)

    async def party_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        bot.entity_index.maybe_refresh()
        return bot.entity_index.party_choices(current)

    async def entity_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        bot.entity_index.maybe_refresh()
        return bot.entity_index.entity_choices(current)

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
    @app_commands.autocomplete(subject=entity_autocomplete, object_entity_id=entity_autocomplete)
    async def fact(
        interaction: discord.Interaction, subject: str, predicate: str, object_entity_id: str, content: str
    ) -> None:
        message = await handle_relationship_claim(bot.connectome, content, subject, predicate, object_entity_id, "fact")
        bot.entity_index.mark_dirty()
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="rumor", description="Record a rumored (unverified) relationship between two entities.")
    @app_commands.describe(
        subject="The subject entity id",
        predicate="The relationship connecting subject to object, e.g. 'likes' or 'member_of'",
        object_entity_id="The object entity id",
        content="The memory content describing this rumor",
    )
    @app_commands.rename(object_entity_id="object")
    @app_commands.autocomplete(subject=entity_autocomplete, object_entity_id=entity_autocomplete)
    async def rumor(
        interaction: discord.Interaction, subject: str, predicate: str, object_entity_id: str, content: str
    ) -> None:
        message = await handle_relationship_claim(bot.connectome, content, subject, predicate, object_entity_id, "rumor")
        bot.entity_index.mark_dirty()
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
        bot.entity_index.mark_dirty()
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="join-party", description="Join a party with a character you own.")
    @app_commands.describe(character_name="Your character's name", party_name="The party name to join")
    @app_commands.autocomplete(character_name=character_autocomplete, party_name=party_autocomplete)
    async def join_party(interaction: discord.Interaction, character_name: str, party_name: str) -> None:
        message = await handle_join_party(bot.connectome, interaction.user.id, character_name, party_name)
        bot.entity_index.mark_dirty()
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="play-character", description="Set your current active character.")
    @app_commands.describe(pc_name="The character's name")
    @app_commands.autocomplete(pc_name=character_autocomplete)
    async def play_character(interaction: discord.Interaction, pc_name: str) -> None:
        message = await handle_play_character(bot.connectome, bot.current_characters, interaction.user.id, pc_name)
        bot.entity_index.mark_dirty()
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="retire-character", description="Retire a character you own.")
    @app_commands.describe(pc_name="The character's name")
    @app_commands.autocomplete(pc_name=character_autocomplete)
    async def retire_character(interaction: discord.Interaction, pc_name: str) -> None:
        message = await handle_retire_character(bot.connectome, bot.current_characters, interaction.user.id, pc_name)
        bot.entity_index.mark_dirty()
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name="ask", description="Ask a question about the campaign.")
    @app_commands.describe(question="What you want to know")
    async def ask(interaction: discord.Interaction, question: str) -> None:
        # Inference can exceed Discord's 3 s interaction deadline, so
        # acknowledge first and answer via a followup.
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await handle_ask(bot.connectome, bot.llm, interaction.user.id, question)
        await interaction.followup.send(message, ephemeral=True)

    @bot.tree.context_menu(name="Remember this")
    async def remember_message(interaction: discord.Interaction, message: discord.Message) -> None:
        if not message.content:
            await interaction.response.send_message(NO_TEXT_REPLY, ephemeral=True)
            return
        # Attributed to the message's author, not the user who right-clicked.
        reply = await handle_remember(bot.connectome, message.author.id, message.content)
        await interaction.response.send_message(reply, ephemeral=True)

    @bot.tree.context_menu(name="Log as rumor")
    async def rumor_message(interaction: discord.Interaction, message: discord.Message) -> None:
        if not message.content:
            await interaction.response.send_message(NO_TEXT_REPLY, ephemeral=True)
            return
        await interaction.response.send_modal(LogAsRumorModal(bot.connectome, message.content))

    @bot.tree.context_menu(name="GM note")
    async def gm_note_message(interaction: discord.Interaction, message: discord.Message) -> None:
        if interaction.user.id not in get_gm_user_ids():
            await interaction.response.send_message(GM_ONLY_REPLY, ephemeral=True)
            return
        if not message.content:
            await interaction.response.send_message(NO_TEXT_REPLY, ephemeral=True)
            return
        reply = await handle_gm_note(bot.connectome, message.content)
        await interaction.response.send_message(reply, ephemeral=True)

    return bot


def main() -> None:
    token = get_bot_token()
    if not token:
        raise RuntimeError(f"{DISCORD_BOT_TOKEN_ENV} is not set")
    create_bot().run(token)
