import datetime as dt
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from .pipeline.ingest import IngestError, ingest
from .pipeline.transcribe import (
    DEFAULT_COMPUTE_TYPE,
    DEFAULT_DEVICE,
    DEFAULT_MODEL,
    load_model,
    transcribe_tracks,
    write_segments,
)

app = typer.Typer(help="MahoganyTableTop post-session pipeline.", no_args_is_help=True)


@app.callback()
def main() -> None:
    # A callback keeps `ingest` a named subcommand while it's the only one.
    pass


def _parse_date(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as e:
        raise typer.BadParameter("expected YYYY-MM-DD") from e


@app.command("ingest")
def ingest_cmd(
    path: Annotated[Path, typer.Argument(exists=True, help="Craig export: a .zip or an extracted directory.")],
    session: Annotated[str, typer.Option(help="Session label, e.g. S12.")],
    date: Annotated[str, typer.Option(help="Session date, YYYY-MM-DD.", callback=_parse_date)],
    out: Annotated[Path | None, typer.Option(help="Segments JSON to write. Default: transcripts/<session>.json")] = None,
    model: Annotated[str, typer.Option(help="faster-whisper model.")] = DEFAULT_MODEL,
    device: Annotated[str, typer.Option(help="cuda, cpu or auto.")] = DEFAULT_DEVICE,
    compute_type: Annotated[str, typer.Option(help="CTranslate2 compute type.")] = DEFAULT_COMPUTE_TYPE,
) -> None:
    """Transcribe a Craig recording into speaker-attributed segments."""
    out = out or Path("transcripts") / f"{session}.json"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            tracks = ingest(path, Path(tmp))
        except IngestError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        for t in tracks:
            typer.echo(f"track {t.path.name} -> {t.username} ({t.user_id})")
        try:
            whisper = load_model(model, device=device, compute_type=compute_type)
        except RuntimeError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        segments = transcribe_tracks(whisper, tracks)
    write_segments(out, segments, session=session, date=date)
    typer.echo(f"wrote {len(segments)} segments to {out}")
