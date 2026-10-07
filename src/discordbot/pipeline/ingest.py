import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

AUDIO_SUFFIXES = {".flac", ".aac", ".m4a", ".ogg", ".opus", ".wav", ".mp3"}
INFO_FILE = "info.txt"
# Craig names each track `<n>-<username>.<ext>`, numbered in join order.
TRACK_NAME_RE = re.compile(r"^(\d+)-(.+)$")
# info.txt lists one `<username> (<discord user id>)` line per track, in
# the same order, under a `Tracks:` heading.
INFO_TRACK_RE = re.compile(r"^\s*(.+?)\s+\((\d{15,20})\)\s*$")


class IngestError(Exception):
    """The export can't be read, or some of its tracks can't be attributed."""


@dataclass(frozen=True)
class Track:
    """One speaker's audio from a Craig export."""

    user_id: str
    username: str
    path: Path


def parse_info(text: str) -> list[tuple[str, str]]:
    """Return `(username, user_id)` per track, in track order, from info.txt."""
    tracks: list[tuple[str, str]] = []
    in_tracks = False
    for line in text.splitlines():
        if line.strip().lower() == "tracks:":
            in_tracks = True
            continue
        if not in_tracks:
            continue
        if not line.strip():
            if tracks:
                break
            continue
        m = INFO_TRACK_RE.match(line)
        if m is None:
            break
        tracks.append((m.group(1), m.group(2)))
    return tracks


def _strip_discriminator(username: str) -> str:
    # Older exports write `name#1234` in info.txt but `name` in file names.
    return re.sub(r"#\d{1,4}$", "", username)


def map_tracks(export_dir: Path) -> list[Track]:
    """Attribute each audio file in an extracted Craig export to a Discord user id.

    A file is matched to its info.txt entry by track number, falling back to
    username. Raises IngestError listing every file that can't be matched,
    rather than silently dropping a speaker.
    """
    info_path = next(export_dir.rglob(INFO_FILE), None)
    if info_path is None:
        raise IngestError(f"No {INFO_FILE} in {export_dir}; can't map tracks to Discord users")
    info = parse_info(info_path.read_text(encoding="utf-8", errors="replace"))
    if not info:
        raise IngestError(f"No tracks listed in {info_path}")
    by_name = {_strip_discriminator(name).lower(): (name, uid) for name, uid in info}

    audio = sorted(p for p in export_dir.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES)
    if not audio:
        raise IngestError(f"No audio tracks in {export_dir}")

    tracks: list[Track] = []
    unmapped: list[str] = []
    for path in audio:
        entry: tuple[str, str] | None = None
        m = TRACK_NAME_RE.match(path.stem)
        if m is not None:
            n = int(m.group(1))
            if 1 <= n <= len(info):
                entry = info[n - 1]
            else:
                entry = by_name.get(m.group(2).lower())
        else:
            entry = by_name.get(path.stem.lower())
        if entry is None:
            unmapped.append(path.name)
        else:
            tracks.append(Track(user_id=entry[1], username=entry[0], path=path))
    if unmapped:
        raise IngestError(f"Can't map these tracks to a Discord user via {INFO_FILE}: {', '.join(unmapped)}")
    return tracks


def ingest(path: Path, workdir: Path) -> list[Track]:
    """Read a Craig export (a .zip or an extracted directory) into Tracks.

    A zip is extracted under `workdir`, which must outlive the returned
    Tracks since their paths point into it.
    """
    if path.is_dir():
        return map_tracks(path)
    if zipfile.is_zipfile(path):
        dest = workdir / path.stem
        with zipfile.ZipFile(path) as zf:
            zf.extractall(dest)
        return map_tracks(dest)
    raise IngestError(f"{path} is neither a directory nor a zip file")
