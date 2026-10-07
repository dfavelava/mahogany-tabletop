import json
import shutil
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from discordbot import cli
from discordbot.pipeline.ingest import IngestError, ingest, map_tracks, parse_info
from discordbot.pipeline.transcribe import Segment, read_segments, transcribe_tracks

FIXTURES = Path(__file__).parent / "fixtures" / "craig"
WHISPER_OUTPUT: dict[str, list[dict[str, Any]]] = json.loads((FIXTURES / "whisper_output.json").read_text())
GM_ID = "111111111111111111"
BRAM_ID = "222222222222222222"


class FakeWhisper:
    """Replays pre-made per-track whisper output, keyed by file name."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def transcribe(self, audio: str, **kwargs: Any):
        self.calls.append((audio, kwargs))
        segs = [SimpleNamespace(**s) for s in WHISPER_OUTPUT[Path(audio).name]]
        return iter(segs), SimpleNamespace(language="en")


def make_export(dest: Path) -> Path:
    """A Craig export dir: the fixture info.txt plus a placeholder file per track."""
    dest.mkdir(parents=True)
    shutil.copy(FIXTURES / "info.txt", dest / "info.txt")
    for name in WHISPER_OUTPUT:
        (dest / name).write_bytes(b"")
    return dest


def test_parse_info_reads_tracks_in_order():
    assert parse_info((FIXTURES / "info.txt").read_text()) == [
        ("gm_dave", GM_ID),
        ("bramble#4821", BRAM_ID),
    ]


def test_map_tracks_by_track_number(tmp_path):
    tracks = map_tracks(make_export(tmp_path / "export"))
    assert [(t.path.name, t.user_id) for t in tracks] == [
        ("1-gm_dave.flac", GM_ID),
        ("2-bramble.flac", BRAM_ID),
    ]


def test_map_tracks_falls_back_to_username(tmp_path):
    export = make_export(tmp_path / "export")
    (export / "1-gm_dave.flac").rename(export / "gm_dave.aac")
    (export / "2-bramble.flac").rename(export / "7-bramble.flac")
    by_name = {t.path.name: t.user_id for t in map_tracks(export)}
    assert by_name == {"gm_dave.aac": GM_ID, "7-bramble.flac": BRAM_ID}


def test_unmapped_track_is_reported(tmp_path):
    export = make_export(tmp_path / "export")
    (export / "9-stranger.flac").write_bytes(b"")
    with pytest.raises(IngestError, match="9-stranger.flac"):
        map_tracks(export)


def test_missing_info_is_an_error(tmp_path):
    export = make_export(tmp_path / "export")
    (export / "info.txt").unlink()
    with pytest.raises(IngestError, match="info.txt"):
        map_tracks(export)


def test_ingest_zip(tmp_path):
    export = make_export(tmp_path / "export")
    zip_path = tmp_path / "craig.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in export.iterdir():
            zf.write(p, p.name)
    work = tmp_path / "work"
    tracks = ingest(zip_path, work)
    assert {t.user_id for t in tracks} == {GM_ID, BRAM_ID}
    assert all(t.path.is_relative_to(work) for t in tracks)


def test_transcribe_attributes_and_merges_segments(tmp_path):
    tracks = map_tracks(make_export(tmp_path / "export"))
    whisper = FakeWhisper()
    segments = transcribe_tracks(whisper, tracks)
    assert segments == [
        Segment(GM_ID, 0.0, 3.2, "The bridge creaks under your weight."),
        Segment(BRAM_ID, 4.1, 7.8, "Bram grabs the rope and keeps going."),
        Segment(GM_ID, 9.5, 12.0, "Roll for perception."),
        Segment(BRAM_ID, 13.0, 14.2, "That's a seventeen."),
    ]
    # One transcribe call per track, never a mixed-down file.
    assert sorted(Path(audio).name for audio, _ in whisper.calls) == sorted(WHISPER_OUTPUT)
    assert all(kw["vad_filter"] for _, kw in whisper.calls)


def test_cli_ingest_writes_segments(tmp_path, monkeypatch):
    export = make_export(tmp_path / "export")
    out = tmp_path / "S12.json"
    loaded: dict[str, Any] = {}

    def fake_load_model(model, device, compute_type):
        loaded.update(model=model, device=device, compute_type=compute_type)
        return FakeWhisper()

    monkeypatch.setattr(cli, "load_model", fake_load_model)
    result = CliRunner().invoke(
        cli.app, ["ingest", str(export), "--session", "S12", "--date", "2026-09-27", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert loaded == {"model": "medium.en", "device": "auto", "compute_type": "int8"}
    payload = json.loads(out.read_text())
    assert payload["session"] == "S12"
    assert payload["date"] == "2026-09-27"
    segments = read_segments(out)
    assert [s.user_id for s in segments] == [GM_ID, BRAM_ID, GM_ID, BRAM_ID]


def test_cli_rejects_bad_date(tmp_path):
    export = make_export(tmp_path / "export")
    result = CliRunner().invoke(cli.app, ["ingest", str(export), "--session", "S12", "--date", "27/09/2026"])
    assert result.exit_code != 0


def test_cli_reports_unmapped_tracks(tmp_path):
    export = make_export(tmp_path / "export")
    (export / "9-stranger.flac").write_bytes(b"")
    result = CliRunner().invoke(cli.app, ["ingest", str(export), "--session", "S12", "--date", "2026-09-27"])
    assert result.exit_code == 1
    assert "9-stranger.flac" in result.output


def test_real_whisper_on_silence(tmp_path):
    pytest.importorskip("faster_whisper", reason="needs the gpu extra")
    from discordbot.pipeline.ingest import Track
    from discordbot.pipeline.transcribe import load_model

    audio = tmp_path / "1-gm_dave.wav"
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 2)
    model = load_model("tiny.en", device="cpu", compute_type="int8")
    segments = transcribe_tracks(model, [Track(GM_ID, "gm_dave", audio)])
    # VAD drops pure silence instead of hallucinating text into it.
    assert segments == []
