from connectomeclient import ConnectomeClient, RelationshipKind

from .identity import character_entity_id, discord_entity_id

# Every operation this bot performs is scoped to this tome. Deliberately a
# constant rather than an env var: there's no configuration under which the
# bot should read or write anywhere else.
WEST_MARCHES_TOME = "west-marches"

CHARACTER_KIND = "character"
CHARACTERS_GROUP_SUFFIX = "-characters"
MEMBER_OF_PREDICATE = "member_of"
PLAYS_PREDICATE = "plays"
# No object entity - like the "is_tired" pattern, this predicate exists purely
# to drive a subject_meta-only assert_relationship call (see
# handle_retire_character).
RETIRED_PREDICATE = "retired"
RETIRED_STATUS = "retired"

GM_GROUP_ID = "GM"


async def handle_remember(connectome: ConnectomeClient, user_id: int, content: str) -> str:
    """Store content as a memory attributed to the calling Discord user and return a confirmation."""
    entity_id = discord_entity_id(user_id)
    _ = await connectome.remember(content, entities=[entity_id], tome=WEST_MARCHES_TOME)
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
        tome=WEST_MARCHES_TOME,
    )
    _ = await connectome.assert_relationship(subject_entity_id, predicate, object_entity_id, kind=kind, tome=WEST_MARCHES_TOME)
    return f"Recorded {kind}: {subject_entity_id} {predicate} {object_entity_id}."


async def handle_gm_note(connectome: ConnectomeClient, content: str) -> str:
    """Store content as a GM-only memory and return a confirmation."""
    _ = await connectome.remember(content, acl=[GM_GROUP_ID], tome=WEST_MARCHES_TOME)
    return f"Noted (GM only): {content}"


async def handle_log(connectome: ConnectomeClient, content: str) -> str:
    """Store raw session narration as a memory and return a confirmation.

    Uses the generic "event" MemoryType rather than adding a session_log type
    to daybidmcp.server/ConnectomeClient's MemoryType vocabulary - Connectome
    stays agnostic of caller-specific concepts like session narration (see
    west-marches-overlay preference from #32). acl is omitted so the
    backend's configured DEFAULT_ACL applies, same as /remember.
    """
    _ = await connectome.remember(content, memory_type="event", tome=WEST_MARCHES_TOME)
    return f"Logged: {content}"


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

    existing = await connectome.get_entity(character_id, tome=WEST_MARCHES_TOME)
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
        tome=WEST_MARCHES_TOME,
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

    character = await connectome.get_entity(character_id, tome=WEST_MARCHES_TOME)
    meta = character.get("meta") if character else None
    owner = meta.get("owner") if isinstance(meta, dict) else None
    if owner != caller_id:
        return f'You don\'t own a character named "{character_name}". Create it first with /add-character.'

    _ = await connectome.assert_relationship(character_id, MEMBER_OF_PREDICATE, party_name, tome=WEST_MARCHES_TOME)
    return f"{character_name} joined {party_name}."


class CurrentCharacterStore:
    """In-process cache of each player's current "plays" relationship memory key.

    /play-character needs to find and supersede the memory that asserted a
    player's previous "current" plays relationship, but there's no
    entity-lookup tool yet (Phase 2) to rediscover it later. So the bot caches
    it here at write time instead, per #54's design note. This is not
    persisted across restarts: a player's first /play-character after a
    restart simply has nothing to supersede, same as their very first one.
    """

    def __init__(self) -> None:
        self._current: dict[str, tuple[str | None, str]] = {}

    def get(self, player_id: str) -> tuple[str | None, str] | None:
        """Return (character_id, memory_key) for the player's current character, if any.

        character_id is None when the player's current "plays" relationship
        points at no character (see handle_retire_character) - the memory_key
        is still tracked so a later command has something to supersede.
        """
        return self._current.get(player_id)

    def set(self, player_id: str, character_id: str | None, memory_key: str) -> None:
        self._current[player_id] = (character_id, memory_key)


async def handle_play_character(
    connectome: ConnectomeClient,
    current_characters: CurrentCharacterStore,
    user_id: int,
    pc_name: str,
) -> str:
    """Set the caller's current active character, superseding any prior one.

    Refuses if the caller doesn't own the named character (created via
    /add-character, #45), or if it's retired (#55) - a retired character is no
    longer selectable as current going forward. Re-running this for the
    character that's already current is a harmless no-op, matching
    /add-character and /join-party.
    """
    player_id = discord_entity_id(user_id)
    character_id = character_entity_id(pc_name)

    character = await connectome.get_entity(character_id, tome=WEST_MARCHES_TOME)
    meta = character.get("meta") if character else None
    owner = meta.get("owner") if isinstance(meta, dict) else None
    if owner != player_id:
        return f'You don\'t own a character named "{pc_name}". Create it first with /add-character.'

    if isinstance(meta, dict) and meta.get("status") == RETIRED_STATUS:
        return f"{pc_name} has been retired and can't be set as your current character."

    previous = current_characters.get(player_id)
    if previous is not None and previous[0] == character_id:
        return f"{pc_name} is already your current character."

    result = await connectome.remember(
        f"{pc_name} is now the current character for {player_id}.",
        entities=[player_id, character_id],
        relationships=[
            {
                "subjectEntityId": player_id,
                "predicate": PLAYS_PREDICATE,
                "objectEntityId": character_id,
                "kind": "fact",
            }
        ],
        tome=WEST_MARCHES_TOME,
    )
    new_memory_key = result["key"]

    if previous is not None:
        previous_character_id, previous_memory_key = previous
        _ = await connectome.supersede_relationship(
            previous_memory_key,
            player_id,
            PLAYS_PREDICATE,
            previous_character_id,
            superseded_by=new_memory_key,
            tome=WEST_MARCHES_TOME,
        )

    current_characters.set(player_id, character_id, new_memory_key)
    return f"You are now playing {pc_name}."


async def handle_retire_character(
    connectome: ConnectomeClient,
    current_characters: CurrentCharacterStore,
    user_id: int,
    pc_name: str,
) -> str:
    """Mark the caller's character retired, clearing it as their current character if it was one.

    Refuses if the caller doesn't own the named character. Retirement is
    independent of "current" (#54): a player can retire a PC without a
    successor lined up, so this never sets a new current character - it only
    clears the old one if the retired PC was it. Re-running this on an
    already-retired character is a harmless no-op, matching /add-character,
    /join-party, and /play-character.

    Design decision (#55): clearing "current" means writing a new "plays"
    memory whose object is None (the player currently plays nothing) and
    superseding the retired character's prior "plays" memory with it - not
    leaving that memory un-superseded and stale. Superseding to an inert
    "plays nothing" fact, rather than to nothing at all, keeps the invariant
    that at most one of a player's "plays" memories is ever un-superseded: the
    next /play-character call has this new memory to supersede in turn.
    """
    player_id = discord_entity_id(user_id)
    character_id = character_entity_id(pc_name)

    character = await connectome.get_entity(character_id, tome=WEST_MARCHES_TOME)
    meta = character.get("meta") if character else None
    owner = meta.get("owner") if isinstance(meta, dict) else None
    if owner != player_id:
        return f'You don\'t own a character named "{pc_name}". Create it first with /add-character.'

    if isinstance(meta, dict) and meta.get("status") == RETIRED_STATUS:
        return f"{pc_name} is already retired."

    _ = await connectome.assert_relationship(
        character_id,
        RETIRED_PREDICATE,
        subject_meta={"status": RETIRED_STATUS},
        tome=WEST_MARCHES_TOME,
    )

    previous = current_characters.get(player_id)
    if previous is not None and previous[0] == character_id:
        previous_memory_key = previous[1]
        result = await connectome.remember(
            f"{player_id} has no current character.",
            entities=[player_id],
            relationships=[
                {
                    "subjectEntityId": player_id,
                    "predicate": PLAYS_PREDICATE,
                    "objectEntityId": None,
                    "kind": "fact",
                }
            ],
            tome=WEST_MARCHES_TOME,
        )
        new_memory_key = result["key"]
        _ = await connectome.supersede_relationship(
            previous_memory_key,
            player_id,
            PLAYS_PREDICATE,
            character_id,
            superseded_by=new_memory_key,
            tome=WEST_MARCHES_TOME,
        )
        current_characters.set(player_id, None, new_memory_key)

    return f"{pc_name} has been retired."


async def assign_gm_relationships(connectome: ConnectomeClient, user_ids: list[int]) -> None:
    """Assert a member_of GM relationship for each configured GM user id.

    Called once at startup from DISCORD_GM_USER_IDS rather than exposed as a
    command - see the module-level note on that env var.
    """
    for user_id in user_ids:
        _ = await connectome.assert_relationship(discord_entity_id(user_id), MEMBER_OF_PREDICATE, GM_GROUP_ID, tome=WEST_MARCHES_TOME)
