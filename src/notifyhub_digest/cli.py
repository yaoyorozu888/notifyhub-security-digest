from __future__ import annotations

import logging
import os
from pathlib import Path

import typer

from notifyhub_digest import __version__
from notifyhub_digest.acs_email import build_digest_email_html
from notifyhub_digest.runner import build_digest_outputs, run_digest


app = typer.Typer(add_completion=False, no_args_is_help=True)


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
    state_dir: Path = typer.Option(Path("state"), "--state-dir", help="既読管理の保存先"),
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

    _configure_logging()

    run_digest(
        out_dir=out_dir,
        sources_path=sources_path,
        state_dir=state_dir,
        run_at_iso=run_at_iso,
        send_email=send_email,
    )


@app.command("email-preview")
def email_preview(
    out_dir: Path = typer.Option(Path("out"), "--out-dir", help="出力先ディレクトリ"),
    sources_path: Path = typer.Option(Path("sources.json"), "--sources", help="sources.json のパス"),
    state_dir: Path = typer.Option(Path("state"), "--state-dir", help="既読管理の保存先"),
    run_at_iso: str | None = typer.Option(
        None,
        "--run-at",
        help="任意: 実行時刻ISO (例 2026-01-12T06:00:00+09:00). 未指定なら現在時刻(JST)を使用",
    ),
):
    """メールHTML（送信しない）を生成し、ローカルで崩れ確認できるようにします。"""

    _configure_logging()

    built = build_digest_outputs(out_dir=out_dir, sources_path=sources_path, state_dir=state_dir, run_at_iso=run_at_iso)
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
