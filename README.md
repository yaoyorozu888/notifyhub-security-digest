# notifyhub-security-digest

サイバーセキュリティ関連の記事を日次で収集・要約し、Webページとメールで配信するためのツールです。

- 公開サイト: https://www.notifyhub.site/

## 概要

このプロジェクトは、ニュースソースから記事を集約し、読みやすい日次ダイジェストとして出力します。

- Web公開用の静的ファイルを生成
- 必要に応じて ACS Email で配信
- OpenAI API キー設定時は AI 分析を利用（未設定時はプレースホルダで継続実行）

## クイックスタート（ローカル実行）

### 1) セットアップ（Python 3.11）

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version  # 3.11.x を確認
python -m pip install -U pip
pip install ".[dev]"
```

- VS Code もこの `.venv` を使う前提です。ワークスペースを開いたまま作成した場合は `Developer: Reload Window` を実行し、必要なら Python インタープリタとして `.venv\Scripts\python.exe` を選択してください。
- ターミナルで直接実行する場合も、未アクティブなら `.\.venv\Scripts\python.exe -m ...` の形で呼び出すと system Python を避けられます。

### 2) `.env` を作成

```powershell
Copy-Item .env.example .env
```

本ツールは起動時にカレントディレクトリの `.env` を自動で読み込みます（存在する場合）。

- すでに同名の環境変数が設定されている場合は、`DOTENV_OVERRIDE=1` を設定するか、新しいターミナルで実行してください。
- `.env` は `.gitignore` 済みです。ACS / OpenAI の設定値を入力して利用してください。

### 3) 実行

```powershell
notifyhub-digest run --out-dir .\out
```

未アクティブのターミナルでは次でも同じです。

```powershell
.\.venv\Scripts\python.exe -m notifyhub_digest.cli run --out-dir .\out
```

### 4) 出力先

`out/digest/YYYY/MM/DD/`

- `index.html`
- `manifest.json`
- `articles/<entry_id>.html`

### 5) Pagefindインデックスを生成（ローカル）

検索UIは `/calendar/` で利用し、検索対象はサイト全体です（`/digest/YYYY/MM/DD/` と `articles` を含む）。
インデックスは生成済みHTMLに対して後段で作成します。

```powershell
npx --yes pagefind --site site --output-path site/pagefind
```

- 上記コマンドは `site/pagefind/` にインデックスを出力します
- 運用はローカル/CIともに毎回の全量再生成で統一します
- `site/calendar/index.html` は生成結果確認用です。直接編集せず、`src/notifyhub_digest/render.py` を修正してください
- 配信物には `site/pagefind/` を含めてコミットします

### Windowsでの注意（日本語パス + editable install）

OneDrive など日本語パス上で `pip install -e` を行うと、`.pth` のエンコーディング不一致により、
Python 起動時に `UnicodeDecodeError: 'cp932'` が発生することがあります。

回避方法:

- 環境変数 `PYTHONUTF8=1` を設定して UTF-8 モードで実行
- 本リポジトリの `src` パスを指す `.pth` を cp932 で書き直す（例: PowerShell で修正）

## メール送信（ACS Email）

接続文字列を使ってメール送信できます（ローカル実行の最小構成）。

### 1) 依存を追加インストール

```powershell
pip install ".[acs]"
```

### 2) 環境変数を設定

必須

- `ACS_EMAIL_CONNECTION_STRING`（ACS リソースの接続文字列）
- `ACS_EMAIL_SENDER`（検証済みドメインの MailFrom アドレス）
- `ACS_EMAIL_TO`（宛先。カンマ区切りで複数指定可）

任意

- `ACS_EMAIL_SUBJECT_PREFIX`（件名プレフィックス）
- `DIGEST_BASE_URL`（Web版ベースURL。既定: `https://notifyhub.site/digest`）

AI分析用（任意）

- `OPENAI_API_KEY`（未設定時は AI 分析をスキップ）
- `OPENAI_MODEL`（既定: `gpt-5.5`）

特集トピック生成用（任意・既存記事生成とは完全に分離）

- `GROK_API_KEY`
- `GROK_MODEL`（既定: `grok-4.3`）
- `FEATURED_TOPIC_COUNT`（既定: `1`）
- `FEATURED_TOPIC_CATEGORIES`（カンマ区切り。例: `Ransomware`。複数指定するなら `FEATURED_TOPIC_COUNT` も増やす）

`GROK_API_KEY` を設定した場合のみ、Grok が `web_search` と `x_search` を使って X 投稿とニュースサイトを横断検索し、「今日の注目トピック」を生成します。通常記事生成は引き続き `OPENAI_*` と `sources.json` を使うため、相互に影響しません。

### 3) 送信を有効化して実行

```powershell
notifyhub-digest run --out-dir .\out --send-email
```

### メールHTMLのローカルプレビュー

送信前に HTML だけ生成して見た目を確認できます。

```powershell
notifyhub-digest email-preview --out-dir .\out
```

出力先: `out/digest/YYYY/MM/DD/email_preview.html`

### 実機確認ポイント

- ログに `ACS Email send result: status=Succeeded ...` が出ること
- ACS 側の送信ログでも `Succeeded` になること
- 受信メールのレイアウトが崩れていないこと（Outlook / モバイルなど）

## SWA + GitHub Actions（日次生成とデプロイ）

GitHub Actions のスケジュール実行で日次生成し、`site/` 配下に生成物を更新して Azure Static Web Apps にデプロイします。

- 実行時刻: 毎日 06:05 JST
- 保持期間: `site/digest/YYYY/MM/DD/` は 90 日を超えたら自動削除
- バックアップ: 初回は digest 全量、その後は当日分の digest 差分を Azure Blob Storage に保存
- 除外対象: `site/pagefind/` と `site/calendar/` はバックアップしません

- ワークフロー: [.github/workflows/digest-generate.yml](.github/workflows/digest-generate.yml)

### 前提

- デフォルトブランチが `main`
- Azure Static Web Apps リソースを作成済み
- SWA デプロイトークンを GitHub Secrets に登録済み

### GitHub Secrets

必須

- `AZURE_STATIC_WEB_APPS_API_TOKEN` : SWAのデプロイトークン（workflow内で参照しているシークレット名）
- `AZURE_STORAGE_ACCOUNT` : Blob backup を格納する Storage Account 名
- `AZURE_STORAGE_KEY` : Blob backup を格納する Storage Account Key

任意（メール送信時）

- `ACS_EMAIL_CONNECTION_STRING`
- `ACS_EMAIL_SENDER`
- `ACS_EMAIL_TO`
- `ACS_EMAIL_SUBJECT_PREFIX`

任意（AI分析時）

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `GROK_API_KEY`
- `GROK_MODEL`
- `FEATURED_TOPIC_COUNT`
- `FEATURED_TOPIC_CATEGORIES`

### Actionsログ出力（vars / secrets）

- `vars` は `name=value` 形式でログ出力
- `secrets` は値を出力せず、参照しているシークレット名のみログ出力

### 公開先

- 生成物は `site/` 配下に出力され、SWA にアップロードされます
- URL 例: `https://notifyhub.site/digest/YYYY/MM/DD/`
- カレンダー: `https://notifyhub.site/calendar/`

### 保持期限とバックアップ

- Blob Storage への通常バックアップが成功したあとでだけ、90 日より古い `site/digest/YYYY/MM/DD/` を削除します
- 削除後の `site/` 全体を基準に `pagefind` を再生成してから commit / deploy します
- Blob backup は `digest-backups` コンテナを使います
- 初回実行では `full/` 配下に digest 全量の tar.gz と manifest を保存します
- 2回目以降は `daily/YYYY-MM-DD/` 配下に当日分の tar.gz と manifest を保存します

Blob Storage への接続は GitHub Secrets の `AZURE_STORAGE_ACCOUNT` と `AZURE_STORAGE_KEY` を使います。

`workflow_dispatch` では `backup_mode` を `auto` / `full` / `daily` から選べます。

- `auto` : Blob Storage の `full/` 配下に既存バックアップがあるかを見て自動判定します。初回実行などで `full/` が空なら `full` と同じ動きになり、すでに full backup があれば `daily` と同じ動きになります。
- `full` : その日の digest だけではなく、保持期間内に残っている `site/digest/` 配下の全日分をまとめて `full/` 配下へ保存します。大きめの再取得を明示的に取りたいとき向けです。
- `daily` : 当日分の `site/digest/YYYY/MM/DD/` と、参照に必要な `site/index.html`、`site/digest/index.html`、`site/digest/latest/index.html` を `daily/YYYY-MM-DD/` 配下へ保存します。通常の日次運用向けです。

重要なのは順序です。workflow は先に Blob Storage への通常バックアップを完了させ、そのあとで古い digest を削除します。したがって backup が失敗した場合、cleanup は実行されません。追加の一時退避先や `retention/...` のような別枠は使いません。

### 手動メンテナンス

ローカルで保持期限削除だけ試す場合:

```powershell
$env:PYTHONPATH = "src"
python scripts/cleanup_old_digests.py --site-dir site --retain-days 90
```

ローカルで backup artifact を作る場合:

```powershell
$env:PYTHONPATH = "src"
python scripts/create_digest_backup.py --site-dir site --output-dir backup-artifacts --mode daily --run-at 2026-06-08T06:00:00+09:00 --day 2026-06-08
```

## セキュリティ

- `summary_html` は許可タグのみ・属性禁止でサーバー側サニタイズを実施
- API キーなどの秘密情報はリポジトリにコミットしないでください（`.env` はローカル専用）
