def discord_entity_id(user_id: int | str) -> str:
    """Deterministic Connectome entity id for a Discord user.

    Derived directly from the Discord user id rather than a separate
    discord_id field with a lookup step: there's no entity-lookup tool yet
    (Phase 2), so a deterministic id needs no lookup at all.
    """
    return f"discord-{user_id}"
