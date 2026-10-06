import importlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .ingest import Track

DEFAULT_MODEL = "medium.en"
# int8 keeps medium.en well inside the 1660 Ti's 6 GB; the LLM never runs
# at the same time (see #3).
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_DEVICE = "auto"


@dataclass(frozen=True)
class Segment:
    """One stretch of speech, attributed to a Discord user via its track."""

    user_id: str
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Segment":
        return cls(user_id=str(d["user_id"]), start=float(d["start"]), end=float(d["end"]), text=str(d["text"]))


class WhisperModel(Protocol):
    """The slice of faster_whisper.WhisperModel this module uses."""

    def transcribe(self, audio: str, *, vad_filter: bool = ...) -> tuple[Iterable[Any], Any]: ...


def load_model(
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
) -> WhisperModel:
    """Load a faster-whisper model. Needs the `gpu` extra installed.

    Imported lazily (and dynamically, so type checking passes without the
    extra) so the bot host never needs faster-whisper or CUDA.
    """
    try:
        faster_whisper = importlib.import_module("faster_whisper")
    except ImportError as e:
        raise RuntimeError("faster-whisper isn't installed; run `uv sync --extra gpu`") from e
    return faster_whisper.WhisperModel(model, device=device, compute_type=compute_type)


def transcribe_track(model: WhisperModel, track: Track) -> list[Segment]:
    """Transcribe one speaker's track; every segment is attributed to that speaker.

    VAD filtering skips the long silences in a per-speaker track, which is
    most of it, so whisper doesn't hallucinate text into them.
    """
    segments, _info = model.transcribe(str(track.path), vad_filter=True)
    out: list[Segment] = []
    for s in segments:
        text = s.text.strip()
        if text:
            out.append(Segment(user_id=track.user_id, start=float(s.start), end=float(s.end), text=text))
    return out


def transcribe_tracks(model: WhisperModel, tracks: Iterable[Track]) -> list[Segment]:
    """Transcribe each track separately, then merge all speakers by start time.

    Craig tracks share a common start, so per-track timestamps are already
    on the session's timeline.
    """
    segments = [seg for track in tracks for seg in transcribe_track(model, track)]
    return sorted(segments, key=lambda s: (s.start, s.end, s.user_id))


def write_segments(path: Path, segments: Iterable[Segment], **meta: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**meta, "segments": [s.to_dict() for s in segments]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_segments(path: Path) -> list[Segment]:
    return [Segment.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))["segments"]]
