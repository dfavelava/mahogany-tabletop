import re


def discord_entity_id(user_id: int | str) -> str:
    """Deterministic Connectome entity id for a Discord user.

    Derived directly from the Discord user id rather than a separate
    discord_id field with a lookup step: there's no entity-lookup tool yet
    (Phase 2), so a deterministic id needs no lookup at all.
    """
    return f"discord-{user_id}"


def character_entity_id(pc_name: str) -> str:
    """Deterministic Connectome entity id for a player character name.

    Slugified so re-running a command with the same name (whitespace/case
    aside) resolves to the same entity - "Thorin" and "thorin" are the same
    character, not two.
    """
    return re.sub(r"[^a-z0-9]+", "-", pc_name.strip().lower()).strip("-")
