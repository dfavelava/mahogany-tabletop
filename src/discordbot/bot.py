import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from .connectome_client import ConnectomeClient, RelationshipKind
from .identity import character_entity_id, discord_entity_id

CHARACTER_KIND = "character"
CHARACTERS_GROUP_SUFFIX = "-characters"
MEMBER_OF_PREDICATE = "member_of"

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

GM_GROUP_ID = "GM"


def get_bot_token() -> str | None:
    return os.getenv(DISCORD_BOT_TOKEN_ENV)


def get_gm_user_ids() -> list[int]:
    raw = os.getenv(DISCORD_GM_USER_IDS_ENV, "")
    return [int(piece.strip()) for piece in raw.split(",") if piece.strip()]


async def handle_remember(connectome: ConnectomeClient, user_id: int, content: str) -> str:
    """Store content as a memory attributed to the calling Discord user and return a confirmation."""
    entity_id = discord_entity_id(user_id)
    _ = await connectome.remember(content, entities=[entity_id])
    return f"Remembered: {content}"


async def handle_relationship_claim(
    connectome: ConnectomeClient,
    content: str,
    subject_entity_id: str,
    predicate: str,
    object_entity_id: str,
    kind: RelationshipKind,
) -> str:
    """Record a memory carrying a subject-predicate-object relationship claim.

    Shared by /fact and /rumor, which differ only in kind. Lists subject/object
    in the memory's entities too, mirroring how daybidmcp.server.remember's
    stub_entities_for_relationships folds relationship endpoints into a
    memory's entities field - otherwise recall's entity filter, which reads
    only that field, couldn't find this memory by subject or object id. Also
    asserts the relationship via the shared entity-relationship endpoint so
    subject/object get stub ent_*.json records the same way
    daybidmcp.server.remember does today (see #51, #43).
    """
    entities = [subject_entity_id] if subject_entity_id == object_entity_id else [subject_entity_id, object_entity_id]
    _ = await connectome.remember(
        content,
        entities=entities,
        relationships=[
            {
                "subjectEntityId": subject_entity_id,
                "predicate": predicate,
                "objectEntityId": object_entity_id,
                "kind": kind,
            }
        ],
    )
    _ = await connectome.assert_relationship(subject_entity_id, predicate, object_entity_id, kind=kind)
    return f"Recorded {kind}: {subject_entity_id} {predicate} {object_entity_id}."


async def handle_gm_note(connectome: ConnectomeClient, content: str) -> str:
    """Store content as a GM-only memory and return a confirmation."""
    _ = await connectome.remember(content, acl=[GM_GROUP_ID])
    return f"Noted (GM only): {content}"


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
        MEMBER_OF_PREDICATE,
        f"{player_id}{CHARACTERS_GROUP_SUFFIX}",
        subject_kind=CHARACTER_KIND,
        subject_meta={"owner": player_id},
    )
    return f"{pc_name} is now yours."


async def handle_join_party(
    connectome: ConnectomeClient, user_id: int, character_name: str, party_name: str
) -> str:
    """Assert that the caller's character joined a party, refusing if they don't own that character.

    Party member_of lives on the character entity, not the player - a
    character joins a party, not an account (see #42's design note). Ownership
    is recorded on the character's entity record by /add-character (#45) as
    meta.owner; re-running this for a party the character already belongs to
    is a harmless no-op since the backend's member_of merge dedupes.
    """
    caller_id = discord_entity_id(user_id)
    character_id = character_entity_id(character_name)

    character = await connectome.get_entity(character_id)
    meta = character.get("meta") if character else None
    owner = meta.get("owner") if isinstance(meta, dict) else None
    if owner != caller_id:
        return f'You don\'t own a character named "{character_name}". Create it first with /add-character.'

    _ = await connectome.assert_relationship(character_id, MEMBER_OF_PREDICATE, party_name)
    return f"{character_name} joined {party_name}."


async def assign_gm_relationships(connectome: ConnectomeClient, user_ids: list[int]) -> None:
    """Assert a member_of GM relationship for each configured GM user id.

    Called once at startup from DISCORD_GM_USER_IDS rather than exposed as a
    command - see the module-level note on that env var.
    """
    for user_id in user_ids:
        _ = await connectome.assert_relationship(discord_entity_id(user_id), MEMBER_OF_PREDICATE, GM_GROUP_ID)


class DaybidDiscordBot(discord.Client):
    """Discord client wiring slash commands to the Connectome memory service."""

    def __init__(self, connectome: ConnectomeClient | None = None) -> None:
        super().__init__(intents=discord.Intents.default())
        self.connectome = connectome or ConnectomeClient()
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

    return bot


def main() -> None:
    token = get_bot_token()
    if not token:
        raise RuntimeError(f"{DISCORD_BOT_TOKEN_ENV} is not set")
    create_bot().run(token)
