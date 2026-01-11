# notifyhub-security-digest (ローカル実行版 / A案)

仕様: `SPEC_NOTIFYHUB_CSIRT_DAILY_REPORT.md`（添付仕様に準拠）

## セットアップ（Python 3.11）

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version  # 3.11.x を確認
python -m pip install -U pip
pip install ".[dev]"
```

### Windowsでの注意（日本語パス + editable install）

OneDrive等の日本語パス上で `pip install -e` を行うと、`.pth` のエンコーディング不一致で
Python起動時に `UnicodeDecodeError: 'cp932'` が発生することがあります。

その場合は以下のどちらかで回避できます。

- 環境変数 `PYTHONUTF8=1` を設定してUTF-8モードで実行
- 本リポジトリの `src` パスを指す `.pth` をcp932で書き直す（例: PowerShellで修正）

## 実行

```powershell
notifyhub-digest run --out-dir .\out
```

## 既読管理をAzure Table Storageへ切替（B案の先行実装）

1) Azure依存を追加インストール

```powershell
pip install ".[azure]"
```

2) 環境変数でバックエンドを切替

- `READ_STORE_BACKEND=azure`
- 認証はいずれか
	- `AZURE_STORAGE_CONNECTION_STRING`（最も簡単。FunctionsではKey Vault経由推奨）
	- `AZURE_TABLE_ACCOUNT_URL`（例: `https://<account>.table.core.windows.net`） + Managed Identity / Entra ID（`DefaultAzureCredential`）
- 任意
	- `AZURE_TABLE_NAME`（既定: `notifyhubRead`）
	- `AZURE_TABLE_PARTITION_KEY`（既定: `read`）

## ACS Emailで送信（B案の先行実装）

接続文字列で送信します（ローカル実行の最小構成）。

1) ACS Email依存を追加インストール

```powershell
pip install ".[acs]"
```

2) 環境変数を設定

- 必須
	- `ACS_EMAIL_CONNECTION_STRING`（ACS リソースの接続文字列）
	- `ACS_EMAIL_SENDER`（検証済みドメインの MailFrom アドレス）
	- `ACS_EMAIL_TO`（宛先。カンマ区切りで複数可）
- 任意
	- `ACS_EMAIL_SUBJECT_PREFIX`（件名プレフィックス）
	- `DIGEST_BASE_URL`（Web版のベースURL。既定: `https://notifyhub.site/digest`）

3) 送信を有効にして実行

```powershell
notifyhub-digest run --out-dir .\out --send-email
```

### 実機確認（Succeeded確認 + 表示崩れ確認）

1) 依存を入れる

```powershell
pip install ".[acs]"
```

2) 環境変数を設定（例: `.env.example` を参照）

3) 送信を1回通す

```powershell
notifyhub-digest run --out-dir .\out --send-email
```

- ログに `ACS Email send result: status=Succeeded ...` が出ること
- ACS 側（Emailの送信ログ）でも `Succeeded` になっていること
- 受信したメールのレイアウトが崩れていないこと（Outlook/モバイル等）

### メールHTMLのローカルプレビュー

送信前にHTMLだけ生成して、ブラウザで見た目確認できます。

```powershell
notifyhub-digest email-preview --out-dir .\out
```

出力先は `out/digest/YYYY-MM-DD/email_preview.html` です。

※ `--send-email` 指定時は「送信が成功した分だけ既読更新」します（送信失敗時は次回に再送され得ます）。

### 環境変数

- `OPENAI_API_KEY` : OpenAI APIキー（未設定の場合はAI分析をスキップしプレースホルダを出力）
- `OPENAI_MODEL` : モデル名（例: `gpt-4o-mini`）

## 出力

`out/digest/YYYY-MM-DD/`
- `index.html`
- `manifest.json`
- `articles/<entry_id>.html`

## SWA + GitHub Actions（スケジュール生成→デプロイ）

SWAでの公開が必須の前提で、GitHub Actionsのスケジュール実行で日次生成し、生成物を `site/` に蓄積してコミット→SWAへデプロイします。

- 日次生成（スケジュール）: [.github/workflows/digest-generate.yml](.github/workflows/digest-generate.yml)
- SWAデプロイ（pushトリガ）: [.github/workflows/swa-deploy.yml](.github/workflows/swa-deploy.yml)

### 前提

- リポジトリのデフォルトブランチが `main`
- Azure Static Web Apps リソースを作成済み
- SWAのデプロイトークンを GitHub Secrets に登録済み

### GitHub Secrets（必須/推奨）

必須
- `AZURE_STATIC_WEB_APPS_API_TOKEN` : SWAのデプロイトークン

推奨（既読の重複送信を避けるため）
- `AZURE_STORAGE_CONNECTION_STRING` : Table Storage を含むストレージ接続文字列

任意（Email送信をする場合）
- `ACS_EMAIL_CONNECTION_STRING`
- `ACS_EMAIL_SENDER`
- `ACS_EMAIL_TO`
- `ACS_EMAIL_SUBJECT_PREFIX`（任意）

任意（AI分析を有効化する場合）
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

### 生成物

- 公開物は `site/` 配下に生成され、SWAへアップロードされます
- URL例: `https://notifyhub.site/digest/YYYY-MM-DD/`

## セキュリティ

- `summary_html` は許可タグのみ・属性禁止でサーバ側サニタイズします。
- APIキー等の秘密情報は `.env` に保存せず、環境変数で渡してください。
