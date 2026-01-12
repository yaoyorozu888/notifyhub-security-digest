from __future__ import annotations

import logging
import os
from pathlib import Path

import typer

from notifyhub_digest import __version__
from notifyhub_digest.acs_email import build_digest_email_html
from notifyhub_digest.runner import build_digest_outputs, run_digest


app = typer.Typer(add_completion=False, no_args_is_help=True)


def _load_dotenv(*, path: Path = Path(".env")) -> None:
    """Load environment variables from a .env file if present.

    - No dependency (python-dotenv) required.
    - Does not override existing environment variables.
    - Supports simple KEY=VALUE lines; ignores blank lines and comments.
    """

    try:
        if not path.exists() or not path.is_file():
            return

        override = os.getenv("DOTENV_OVERRIDE", "0").strip().lower() in ("1", "true", "yes", "on")
        loaded_keys: list[str] = []

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.lower().startswith("export "):
                line = line[7:].lstrip()

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue

            value = value.strip()
            if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
                value = value[1:-1]

            if override or key not in os.environ:
                os.environ[key] = value
                loaded_keys.append(key)

        if os.getenv("LOG_LEVEL", "").strip().upper() == "DEBUG":
            logging.getLogger(__name__).debug(
                "Loaded .env: path=%s keys=%d override=%s",
                str(path),
                len(loaded_keys),
                override,
            )
    except Exception:
        # .env はローカル便利機能なので、壊れていても実行は続行する
        return


def _configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


@app.command()
def version() -> None:
    """バージョンを表示します。"""

    typer.echo(__version__)


@app.command()
def run(
    out_dir: Path = typer.Option(Path("out"), "--out-dir", help="出力先ディレクトリ"),
    sources_path: Path = typer.Option(Path("sources.json"), "--sources", help="sources.json のパス"),
    run_at_iso: str | None = typer.Option(
        None,
        "--run-at",
        help="任意: 実行時刻ISO (例 2026-01-12T06:00:00+09:00). 未指定なら現在時刻(JST)を使用",
    ),
    send_email: bool = typer.Option(
        False,
        "--send-email/--no-send-email",
        help="任意: ACS Emailで送信する（要: pip install '.[acs]' と環境変数ACS_EMAIL_*）",
    ),
):
    """日次レポートを生成します（ローカル実行版）。"""

    _load_dotenv()
    _configure_logging()

    run_digest(
        out_dir=out_dir,
        sources_path=sources_path,
        run_at_iso=run_at_iso,
        send_email=send_email,
    )


@app.command("email-preview")
def email_preview(
    out_dir: Path = typer.Option(Path("out"), "--out-dir", help="出力先ディレクトリ"),
    sources_path: Path = typer.Option(Path("sources.json"), "--sources", help="sources.json のパス"),
    run_at_iso: str | None = typer.Option(
        None,
        "--run-at",
        help="任意: 実行時刻ISO (例 2026-01-12T06:00:00+09:00). 未指定なら現在時刻(JST)を使用",
    ),
):
    """メールHTML（送信しない）を生成し、ローカルで崩れ確認できるようにします。"""

    _load_dotenv()
    _configure_logging()

    built = build_digest_outputs(out_dir=out_dir, sources_path=sources_path, run_at_iso=run_at_iso)
    html_body = build_digest_email_html(
        day=built.day,
        digest_root_url=built.digest_root_url,
        window_from_jst=built.window_from_jst.isoformat(),
        window_to_jst=built.window_to_jst.isoformat(),
        generated_at_jst=built.run_at_jst.isoformat(),
        items=built.items,
    )

    out_path = built.digest_dir / "email_preview.html"
    out_path.write_text(html_body, encoding="utf-8")
    typer.echo(str(out_path))
