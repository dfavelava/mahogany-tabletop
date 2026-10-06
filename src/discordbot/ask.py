from dataclasses import dataclass
from typing import Any

from connectomeclient import ConnectomeClient

from .campaign import WEST_MARCHES_TOME
from .identity import discord_entity_id
from .llm import LLMClient, LLMUnavailable

ASK_K = 10
DONT_KNOW = "I don't know."
# Discord rejects messages over 2000 characters.
DISCORD_MESSAGE_LIMIT = 2000
# Per-memory cap on the body text put in the prompt or the fallback reply,
# so ten long transcript chunks still fit a small local model's context.
PROMPT_BODY_LIMIT = 1500
FALLBACK_BODY_LIMIT = 300

SYSTEM_PROMPT = (
    "You answer questions about a tabletop campaign using only the numbered memories provided. "
    "Every claim in your answer must be supported by at least one memory, and you must list the "
    "numbers of the memories you used in `sources`. If the memories don't answer the question, "
    f'set `answer` to "{DONT_KNOW}" and `sources` to []. Never guess or use outside knowledge.'
)

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["answer", "sources"],
}


@dataclass(frozen=True)
class RecalledMemory:
    key: str
    citation: str
    body: str


def split_memory_document(content: str) -> tuple[dict[str, str], str]:
    """Split a hydrated memory document into its top-level scalar frontmatter fields and body.

    Only top-level `key: value` lines are read (nested lists/maps are
    skipped), which is all a citation needs, without pulling in a YAML
    dependency. Content with no frontmatter is returned as the whole body.
    """
    if not content.startswith("---\n"):
        return {}, content.strip()
    end = content.find("\n---", 4)
    if end == -1:
        return {}, content.strip()
    meta: dict[str, str] = {}
    for line in content[4:end].splitlines():
        if not line or line[0].isspace() or line.startswith("-") or ":" not in line:
            continue
        name, _, value = line.partition(":")
        value = value.strip().strip("'\"")
        if value:
            meta[name.strip()] = value
    return meta, content[end + 4 :].strip()


def citation_for(key: str, meta: dict[str, str]) -> str:
    """Cite a memory by session plus timestamp when it has a session, else by its key."""
    session = meta.get("session")
    timestamp = meta.get("occurred_at") or meta.get("created_at")
    if session and timestamp:
        return f"session {session} @ {timestamp}"
    return key


def parse_results(response: dict[str, object]) -> list[RecalledMemory]:
    results = response.get("results")
    if not isinstance(results, list):
        return []
    memories: list[RecalledMemory] = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("key"), str):
            continue
        # A hydrated result carries `content`; it falls back to `snippet`
        # (or nothing) when the backend couldn't read the memory body.
        raw = result.get("content") or result.get("snippet") or ""
        meta, body = split_memory_document(raw if isinstance(raw, str) else "")
        if body:
            memories.append(RecalledMemory(key=result["key"], citation=citation_for(result["key"], meta), body=body))
    return memories


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_messages(question: str, memories: list[RecalledMemory]) -> list[dict[str, str]]:
    context = "\n\n".join(
        f"[{i}] ({memory.citation})\n{truncate(memory.body, PROMPT_BODY_LIMIT)}" for i, memory in enumerate(memories, 1)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Memories:\n\n{context}\n\nQuestion: {question}"},
    ]


def format_answer(reply: dict[str, Any] | str, memories: list[RecalledMemory]) -> str:
    """Render the model's answer with its citations, or DONT_KNOW if it cited nothing valid.

    An answer that cites no memory we actually gave the model is treated as a
    guess and dropped, so every non-"I don't know" reply carries a citation.
    """
    if not isinstance(reply, dict):
        return DONT_KNOW
    answer = reply.get("answer")
    sources = reply.get("sources")
    if not isinstance(answer, str) or not answer.strip() or not isinstance(sources, list):
        return DONT_KNOW
    cited: list[str] = []
    for source in sources:
        if isinstance(source, int) and 1 <= source <= len(memories):
            citation = memories[source - 1].citation
            if citation not in cited:
                cited.append(citation)
    if not cited:
        return DONT_KNOW
    sources_line = "\n\nSources: " + "; ".join(f"`{c}`" for c in cited)
    return truncate(answer.strip(), DISCORD_MESSAGE_LIMIT - len(sources_line)) + sources_line


def format_fallback(memories: list[RecalledMemory]) -> str:
    """List the recalled memories verbatim when the LLM can't be reached."""
    lines = ["I can't reach the language model right now, but here's what I found:"]
    for memory in memories:
        lines.append(f"- `{memory.citation}`: {truncate(' '.join(memory.body.split()), FALLBACK_BODY_LIMIT)}")
    reply = ""
    for line in lines:
        candidate = f"{reply}\n{line}" if reply else line
        if len(candidate) > DISCORD_MESSAGE_LIMIT:
            break
        reply = candidate
    return reply


async def handle_ask(connectome: ConnectomeClient, llm: LLMClient, user_id: int, question: str) -> str:
    """Answer a campaign question from memories visible to the calling Discord user.

    recall is scoped with `as_` to the caller's entity id, so the backend only
    returns memories whose acl is empty or names the caller or one of their
    member_of groups. GM-only memories never reach a player's prompt, so the
    model can't leak them. On LLMUnavailable the caller gets the recalled
    snippets instead of an error.
    """
    response = await connectome.recall(
        question, k=ASK_K, as_=discord_entity_id(user_id), tome=WEST_MARCHES_TOME, hydrate=True
    )
    memories = parse_results(response)
    if not memories:
        return DONT_KNOW
    try:
        reply = await llm.chat(build_messages(question, memories), schema=ANSWER_SCHEMA)
    except LLMUnavailable:
        return format_fallback(memories)
    return format_answer(reply, memories)
