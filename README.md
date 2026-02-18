# notifyhub-security-digest

サイバーセキュリティ関連の記事を日次で収集・要約し、Webページとメールで配信するためのツールです。

- 公開サイト: https://www.notifyhub.site/
## 概要

このプロジェクトは、ニュースソースから記事を集約し、読みやすい日次ダイジェストとして出力します。

- Web公開用の静的ファイルを生成
- 必要に応じて ACS Email で配信
- OpenAI API キー設定時は AI 分析を利用（未設定時はプレースホルダで継続実行）

## UI

### 日次レポート画面
#### ① 元記事タイトル、② ニュースソース、 ③ 概要
<img width="1860" height="1276" alt="image" src="https://github.com/user-attachments/assets/9a5d0f4f-96e8-4df3-8211-5e3c18dda25a" />

### 個別記事の要約画面
#### ④ 元記事タイトル、⑤ ニュースソース
<img width="1888" height="2552" alt="image" src="https://github.com/user-attachments/assets/4bc57685-ceb2-43ff-adbb-63bc7318d979" />

### 更新通知メール
#### ⑥ 元記事タイトル、⑦ ニュースソース
<img width="1888" height="2200" alt="image" src="https://github.com/user-attachments/assets/c9ab4ab9-28c4-426a-82ca-8a4560d4e0d1" />

## クイックスタート（ローカル実行）

### 1) セットアップ（Python 3.11）

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version  # 3.11.x を確認
python -m pip install -U pip
pip install ".[dev]"
```

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

### 4) 出力先

`out/digest/YYYY-MM-DD/`

- `index.html`
- `manifest.json`
- `articles/<entry_id>.html`

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
- `OPENAI_MODEL`（例: `gpt-4o-mini`）

### 3) 送信を有効化して実行

```powershell
notifyhub-digest run --out-dir .\out --send-email
```

### メールHTMLのローカルプレビュー

送信前に HTML だけ生成して見た目を確認できます。

```powershell
notifyhub-digest email-preview --out-dir .\out
```

出力先: `out/digest/YYYY-MM-DD/email_preview.html`

### 実機確認ポイント

- ログに `ACS Email send result: status=Succeeded ...` が出ること
- ACS 側の送信ログでも `Succeeded` になること
- 受信メールのレイアウトが崩れていないこと（Outlook / モバイルなど）

## SWA + GitHub Actions（日次生成とデプロイ）

GitHub Actions のスケジュール実行で日次生成し、`site/` 配下に生成物を蓄積して Azure Static Web Apps にデプロイします。

- ワークフロー: [.github/workflows/digest-generate.yml](.github/workflows/digest-generate.yml)

### 前提

- デフォルトブランチが `main`
- Azure Static Web Apps リソースを作成済み
- SWA デプロイトークンを GitHub Secrets に登録済み

### GitHub Secrets

必須

- `AZURE_STATIC_WEB_APPS_API_TOKEN` : SWAのデプロイトークン（workflow内で参照しているシークレット名）

任意（メール送信時）

- `ACS_EMAIL_CONNECTION_STRING`
- `ACS_EMAIL_SENDER`
- `ACS_EMAIL_TO`
- `ACS_EMAIL_SUBJECT_PREFIX`

任意（AI分析時）

- `OPENAI_API_KEY`
- `OPENAI_MODEL`

### Actionsログ出力（vars / secrets）

- `vars` は `name=value` 形式でログ出力
- `secrets` は値を出力せず、参照しているシークレット名のみログ出力

### 公開先

- 生成物は `site/` 配下に出力され、SWA にアップロードされます
- URL 例: `https://notifyhub.site/digest/YYYY-MM-DD/`

## セキュリティ

- `summary_html` は許可タグのみ・属性禁止でサーバー側サニタイズを実施
- API キーなどの秘密情報はリポジトリにコミットしないでください（`.env` はローカル専用）
