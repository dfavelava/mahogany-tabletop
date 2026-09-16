import json
import os
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, get_args

import httpx
import yaml
from dotenv import load_dotenv

DEFAULT_API_BASE_URL = "http://localhost:8080/api/connectome"
DEFAULT_TIMEOUT_SECONDS = 30.0
USER_AGENT = "discordbot/0.1.0"
MEMORY_SCHEMA_VERSION = "connectome/memory/0.1"

MemoryType = Literal["note", "fact", "preference", "event"]
MEMORY_TYPES: tuple[str, ...] = get_args(MemoryType)
DEFAULT_MEMORY_TYPE: MemoryType = "note"

# Mirrors RelationshipKind/DEFAULT_RELATIONSHIP_KIND in daybidmcp.server: the
# truth-status of a relationship claim, distinct from MemoryType even though
# "fact" is a value both happen to share (see #43).
RelationshipKind = Literal["fact", "hypothesis", "rumor"]
DEFAULT_RELATIONSHIP_KIND: RelationshipKind = "fact"


class Relationship(TypedDict):
    """A directed relationship between entities, mirroring daybidmcp.server's
    Relationship model - see format_memory."""

    subjectEntityId: str
    predicate: str
    objectEntityId: NotRequired[str | None]
    kind: NotRequired[RelationshipKind]


# Identifies where a memory came from, mirroring daybidmcp.server's
# MEMORY_SOURCE_TYPE convention for its own client identity.
MEMORY_SOURCE_TYPE = "discord"

_ = load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def entity_key(entity_id: str) -> str:
    """Blob store key for an entity id, matching the ent_<id>.json convention
    entityKey uses in backend/resources/entity.go."""
    return f"ent_{entity_id}.json"


class ConnectomeClient:
    """Minimal async HTTP client for the connectome backend's /api/connectome routes.

    Talks to the Go backend directly over HTTP, independent of daybidmcp - no
    import of the MCP package and no MCP/stdio transport in the path. This
    intentionally re-derives just enough of daybidmcp.server's memory
    frontmatter format (see format_memory) to write a valid memory.
    Entity-record merge logic (stub entities, member_of) lives server-side in
    the Go backend's /entity/relationship endpoint - see assert_relationship -
    rather than being re-implemented here, so daybidmcp and discordbot share
    one primitive instead of two drifting Python implementations.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = (base_url or os.getenv("CONNECTOME_API_BASE_URL", DEFAULT_API_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.getenv("CONNECTOME_API_KEY") or os.getenv("DAYBID_API_KEY") or os.getenv("apikey")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        files: dict[str, tuple[str, BytesIO, str]] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                self._url(path),
                headers=self._headers(),
                params=params,
                files=files,
                json=json_body,
            )
            _ = response.raise_for_status()
            return response

    async def remember(
        self,
        content: str,
        memory_type: MemoryType = DEFAULT_MEMORY_TYPE,
        entities: list[str] | None = None,
        relationships: list[Relationship] | None = None,
        acl: list[str] | None = None,
    ) -> dict[str, str]:
        """Write a memory document and return its key."""
        memory_id = f"mem_{uuid.uuid4()}.md"
        now = datetime.now(UTC).isoformat()
        metadata: dict[str, object] = {
            "version": MEMORY_SCHEMA_VERSION,
            "id": memory_id,
            "type": memory_type,
            "created_at": now,
            "source": {"type": MEMORY_SOURCE_TYPE, "created_at": now},
            "entities": list(entities or []),
            "relationships": [dict(relationship) for relationship in (relationships or [])],
        }
        if acl is not None:
            metadata["acl"] = acl
        yaml_data = yaml.dump(metadata, sort_keys=False).strip("\n")
        document = f"---\n{yaml_data}\n---\n{content}\n"

        _ = await self._request(
            "POST",
            "/memory/",
            files={"file": (memory_id, BytesIO(document.encode("utf-8")), "text/plain; charset=utf-8")},
        )
        return {"key": memory_id}

    async def recall(
        self,
        query: str,
        k: int = 5,
        memory_type: MemoryType | None = None,
        entity: str | None = None,
        since: str | None = None,
        until: str | None = None,
        hydrate: bool = False,
        as_: str | None = None,
    ) -> dict[str, object]:
        """Search memory by semantic similarity to query and return ranked results."""
        filters: dict[str, str] = {}
        if memory_type is not None:
            filters["type"] = memory_type
        if entity is not None:
            filters["entity"] = entity
        if since is not None:
            filters["since"] = since
        if until is not None:
            filters["until"] = until

        body: dict[str, object] = {"query": query, "k": k, "hydrate": hydrate}
        if filters:
            body["filters"] = filters
        if as_ is not None:
            body["as"] = as_

        response = await self._request("POST", "/memory/search", json_body=body)
        return response.json()

    async def assert_relationship(
        self,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str | None = None,
        kind: str | None = None,
        subject_kind: str | None = None,
        subject_meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Assert a relationship between two entities via the Go backend's shared
        merge endpoint, so ent_*.json state (stub entities, member_of) ends up the
        same as an equivalent daybidmcp.server.remember call would produce - see
        UpsertEntityRelationship in backend/resources/entity.go.

        subject_kind/subject_meta optionally stamp the subject entity's
        kind/meta fields in the same call (overwrite and shallow-merge
        respectively, mirroring daybidmcp.server's Entity.kind/Entity.meta)."""
        body: dict[str, object] = {"subjectEntityId": subject_entity_id, "predicate": predicate}
        if object_entity_id is not None:
            body["objectEntityId"] = object_entity_id
        if kind is not None:
            body["kind"] = kind
        if subject_kind is not None:
            body["subjectKind"] = subject_kind
        if subject_meta is not None:
            body["subjectMeta"] = subject_meta

        response = await self._request("POST", "/entity/relationship", json_body=body)
        return response.json()

    async def get_memory(self, key: str) -> dict[str, object]:
        """Fetch a stored memory document or entity record by key."""
        response = await self._request("GET", "/memory/", params={"key": key})
        return response.json()

    async def get_entity(self, entity_id: str) -> dict[str, object] | None:
        """Fetch an ent_<id>.json entity record, or None if it doesn't exist yet."""
        try:
            payload = await self.get_memory(entity_key(entity_id))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        content = payload["content"]
        assert isinstance(content, str)
        return json.loads(content)

    async def browse_all(self) -> dict[str, object]:
        """List all stored memory and entity keys."""
        response = await self._request("GET", "/memory/list")
        return response.json()

    async def forget(self, key: str) -> dict[str, str]:
        """Delete a stored memory or entity record by key."""
        _ = await self._request("DELETE", "/memory/", json_body={"key": key})
        return {"message": "deleted", "key": key}
