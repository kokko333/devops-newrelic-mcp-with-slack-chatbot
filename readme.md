# NewRelic Slack Bot

Slack から自然言語で New Relic の監視データを問い合わせる AI チャットボット。  
Amazon Bedrock (Claude) + New Relic MCP + Slack Socket Mode の組み合わせで動作する。

---

## 前提条件

以下をすべて完了させてから進めてください。

### 1. New Relic MCP Public Preview の有効化

1. New Relic UI にログイン
2. 左下のユーザーアイコン → **Administration** → **Previews & Trials**
3. **New Relic AI MCP server** を有効化（Accept & enroll）

### 2. Amazon Bedrock モデルアクセスの有効化

1. AWS コンソール → **Amazon Bedrock** → **Model access**
2. Anthropic セクションで使用するモデル（claude-sonnet-4-6 等）を有効化
3. 有効化後、モデル ID を確認して `.env` の `BEDROCK_MODEL_ID` に設定

### 3. Slack App の作成

**アプリの作成（マニフェストから）:**

1. [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From an app manifest**
2. インストール先のワークスペースを選択
3. `slack-app_manifest.json` の内容を貼り付けて **Next** → **Create**

> マニフェストには Socket Mode・Bot スコープ・Event Subscriptions がすべて含まれている。

**App-Level Token の生成:**

1. **Settings** → **Basic Information** → **App-Level Tokens** → **Generate Token and Scopes**
2. Token Name を入力し、scope に `connections:write` を追加して **Generate**
3. 表示された `xapp-` トークンを `SLACK_APP_TOKEN` に使用

**DM の有効化:**

1. **Features** → **App Home** → **Show Tabs** セクションで以下を設定:
   - **Messages Tab** をオン
   - **「Allow users to send Slash commands and messages from the messages tab」** にチェック

> この設定はマニフェストに含まれないため手動で行う。インストール後に変更した場合は Slack の再起動が必要。

**アプリのインストール:**

1. **Features** → **OAuth & Permissions** ページ上部の **Install to Workspace** をクリック
2. 認可画面で許可 → Bot User OAuth Token（`xoxb-` で始まる）を取得 → `SLACK_BOT_TOKEN` に使用

---

## ローカル開発

```bash
# 1. 環境変数の設定
cp .env.example .env
# .env を編集して各値を設定

# 2. 依存ライブラリのインストールと起動
make run
```

Slack でボットをメンションするか DM を送信して動作確認してください。

---

## AWS デプロイ

### 事前準備

- Docker Desktop が起動していること（CDK deploy 時に自動でイメージビルドされる）
- `.env` に `AWS_DEFAULT_REGION` と `BEDROCK_MODEL_ID` が設定されていること

```bash
# AWS CDK のブートストラップ（アカウント × リージョン(.env指定) の初回のみ）
make bootstrap
```

### Step 1: Secrets Manager リソースのデプロイ（初回のみ）

```bash
make deploy-secrets
```

### Step 2: シークレット値の登録

作成されたシークレットは初期値が空です。以下のコマンドで実際の値を登録してください。

```bash
aws secretsmanager put-secret-value \
  --secret-id chatbot/slack-bot-token \
  --secret-string "xoxb-your-actual-token"

aws secretsmanager put-secret-value \
  --secret-id chatbot/slack-app-token \
  --secret-string "xapp-your-actual-token"

aws secretsmanager put-secret-value \
  --secret-id chatbot/newrelic-api-key \
  --secret-string "NRAK-your-actual-key"

aws secretsmanager put-secret-value \
  --secret-id chatbot/newrelic-account-id \
  --secret-string "your-account-id"

aws secretsmanager put-secret-value \
  --secret-id chatbot/newrelic-license-key \
  --secret-string "your-40-char-ingest-license-key"
```

### Step 3: ボット本体のデプロイ

```bash
make deploy
```

CDK が Docker イメージのビルド・ECR push・ECS/VPC 等のリソース作成を行います。  
ECS タスク起動時に Step 2 で登録した値が読み込まれます。

> **シークレット値を更新した場合**は、ECS タスクの再起動が必要です。
>
> ```bash
> aws ecs update-service \
>   --cluster <CLUSTER_NAME> \
>   --service <SERVICE_NAME> \
>   --force-new-deployment
> ```

---

## New Relic 監視リソースのデプロイ（Terraform）

ECS デプロイとは独立して、New Relic 側のリソース（Events to Metrics・Drop Filter・ダッシュボード）を Terraform で管理する。
`.env` の NR 設定値が Makefile 経由で Terraform 変数に自動マッピングされるため、`terraform.tfvars` の作成は不要。

### 事前準備

- Terraform CLI がインストールされていること（`terraform -version` で確認）
- `.env` に以下が設定されていること
  - `NEW_RELIC_API_KEY`（User API Key）
  - `NEW_RELIC_ACCOUNT_ID`

### Step 1: 変更内容の確認

```bash
make monitoring-plan
```

`terraform init` が自動実行されてプロバイダーがダウンロードされ、作成・変更されるリソースの一覧が表示される。

### Step 2: リソースのデプロイ

```bash
make monitoring-apply
```

確認プロンプトに `yes` を入力するとリソースが作成される。

> **リソースを削除する場合:**
>
> ```bash
> make monitoring-destroy
> ```

---

## ディレクトリ構成

```
├── _design/
│   ├── requirements.md             # 要件定義書
│   └── monitoring_requirements.md  # 監視導入 要件定義書
├── bot/
│   ├── main.py                     # エントリーポイント
│   ├── slack_handler.py            # Slack イベント処理
│   ├── claude_client.py            # Bedrock / Claude + ツール実行ループ
│   ├── nr_mcp_client.py            # New Relic MCP HTTP クライアント
│   ├── conversation_manager.py
│   ├── Dockerfile
│   └── pyproject.toml
├── infra/
│   ├── app.py                      # CDK エントリーポイント
│   ├── cdk.json                    # CDK 設定（uv run python app.py）
│   ├── stacks/
│   │   ├── secrets_stack.py        # Secrets Manager リソース
│   │   └── bot_stack.py            # ECS / VPC / IAM 等
│   └── pyproject.toml
├── monitoring/
│   ├── main.tf                     # Terraform プロバイダー設定
│   ├── variables.tf                # 変数定義
│   ├── events_to_metrics.tf        # トークン数メトリクス変換ルール
│   ├── drop_rules.tf               # PII マスキング（メールアドレス）
│   ├── dashboard.tf                # ダッシュボード
│   └── dashboard.json.tftpl        # ダッシュボード JSON テンプレート
├── slack-app_manifest.json         # Slack App 作成用マニフェスト
└── .env.example
```
