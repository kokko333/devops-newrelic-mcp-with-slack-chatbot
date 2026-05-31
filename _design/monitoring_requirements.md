# 監視導入 要件定義書 — Slack × New Relic MCP AI チャットボット

作成日: 2026-05-31

---

## 1. 概要

本書は、既存の Slack × New Relic MCP AI チャットボットに対して New Relic APM および AI Monitoring を導入する際の要件を定義する。

| 監視種別 | 目的 |
|---|---|
| **APM** | ボット処理全体（Slack イベント受信〜応答送信）のレイテンシ・エラー・外部呼び出し時間の可視化 |
| **AI Monitoring** | Bedrock モデル呼び出しの内容・トークン使用量・エラーの記録と長期集計 |

---

## 2. 監視アーキテクチャと追跡範囲

```
【Slack 側】                  【Python Bot / ECS Fargate】               【外部サービス】

ユーザーがメッセージ送信
      ↓
Slack が WebSocket で
イベント配信
      ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
      ↓  ← APM / AI Monitoring 監視範囲 ここから
      ↓
  SlackHandler._process()  [APM Background Task]
      ↓
  ClaudeClient.generate_response()
      │
      ├─ Bedrock messages.create() ──────────────→ Amazon Bedrock（Claude）
      │  （AI Monitoring: LlmChatCompletionSummary   ↓（応答）
      │              LlmChatCompletionMessage）   ←──┘
      │
      ├─ NRMCPClient.call_tool() ────────────────→ mcp.newrelic.com
      │  （APM: 外部サービス呼び出しとして記録）      ↓（応答）
      │                                          ←──┘
      └─ Slack API chat.update() ───────────────→ Slack API
         （APM: 外部サービス呼び出しとして記録）      ↓（応答）
                                                ←──┘
  処理完了
      ↓  ← APM / AI Monitoring 監視範囲 ここまで
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
      ↓
Slack がユーザーに表示
```

**APM では追跡できない範囲:**
- ユーザー送信 → Slack 内部処理 → WebSocket 配信（Slack インフラ内部）
- Slack API レスポンス受信 → ユーザー画面への表示（Slack クライアント側）

---

## 3. 実装変更の全体像

本導入にあたって変更・追加が必要なコンポーネントは以下のとおり。

| コンポーネント | 変更種別 | 内容 |
|---|---|---|
| `bot/pyproject.toml` | 変更 | `newrelic` パッケージ追加 |
| `bot/main.py` | 変更 | APM エージェント初期化コード追加 |
| `bot/slack_handler.py` | 変更 | Background Task ラッパー追加 |
| `infra/stacks/secrets_stack.py` | 変更 | NR ライセンスキー用シークレット追加 |
| `infra/stacks/bot_stack.py` | 変更 | NR 関連環境変数の ECS タスク定義への追加 |
| `monitoring/` | 新規作成 | New Relic リソース管理用 Terraform ディレクトリ |

---

## 4. APM 設定

### 4.1 Python エージェントのインストール

`bot/pyproject.toml` に `newrelic` パッケージを追加する。

```toml
dependencies = [
    ・・・
    "newrelic>=10.0",   # 追加
]
```

### 4.2 エージェント初期化

APM エージェントは環境変数（後述）から設定を読み込む。`bot/main.py` の冒頭（`import` の前）に以下を追加する。

```python
import newrelic.agent
newrelic.agent.initialize()   # load_dotenv() より前に呼ぶ
```

### 4.3 Background Task の設定（重要）

本ボットは HTTP サーバーではなく WebSocket で常時接続する構造のため、APM は HTTP トランザクションを自動検出できない。Slack イベントのハンドラー処理を「Background Task」として明示的にマークする必要がある。

`bot/slack_handler.py` の `_process()` メソッドを以下のように変更する。

```python
import newrelic.agent

async def _process(self, event: dict, say, client) -> None:
    app = newrelic.agent.application()
    with newrelic.agent.BackgroundTask(app, name="slack-message-handler", group="Slack"):
        channel: str = event["channel"]
        thread_ts: str = event.get("thread_ts") or event["ts"]
        user_text: str = _MENTION_RE.sub("", event.get("text", "")).strip()
        # ... 既存処理
```

### 4.4 環境変数（ECS タスク定義への追加）

| 変数名 | 値 | 取得方法 |
|---|---|---|
| `NEW_RELIC_LICENSE_KEY` | NR ライセンスキー | Secrets Manager（後述） |
| `NEW_RELIC_APP_NAME` | `newrelic-slack-bot` | 固定値 |
| `NEW_RELIC_AI_MONITORING_ENABLED` | `true` | 固定値 |
| `NEW_RELIC_DISTRIBUTED_TRACING_ENABLED` | `true` | 固定値（AI Monitoring の前提条件） |

> **注意**: `NEW_RELIC_DISTRIBUTED_TRACING_ENABLED=true` は AI Monitoring の必須要件。これが無効だと AI データは収集されない。

### 4.5 ライセンスキーの管理

New Relic ライセンスキー（「INGEST - LICENSE」タイプ）を Secrets Manager で管理する。

**`infra/stacks/secrets_stack.py` への追加:**

```python
secretsmanager.Secret(self, "NrLicenseKey", secret_name="chatbot/newrelic-license-key")
```

**`infra/stacks/bot_stack.py` への追加（シークレット参照・ECS 設定）:**

```python
nr_license_key = secretsmanager.Secret.from_secret_name_v2(
    self, "NrLicenseKey", "chatbot/newrelic-license-key"
)

# タスク定義の secrets に追加
secrets={
    ...
    "NEW_RELIC_LICENSE_KEY": ecs.Secret.from_secrets_manager(nr_license_key),
}
```

---

## 5. AI Monitoring 設定

### 5.1 有効化

APM エージェントが `NEW_RELIC_AI_MONITORING_ENABLED=true` で起動すると、`anthropic.AsyncAnthropicBedrock` への呼び出しが自動的にインストルメント化される（`claude_client.py` のコード変更は不要）。

### 5.2 収集されるイベントタイプ

| イベントタイプ | 主なフィールド | 保存期間（Original） |
|---|---|---|
| `LlmChatCompletionSummary` | `duration`, `response.model`, `token_count`, `error` | 30日 |
| `LlmChatCompletionMessage` | `content`, `role`, `token_count`, `sequence` | 30日 |
| `LlmErrorMessage` | `error.message`, `error.code`, `response.model` | 30日 |

---

## 6. Drop Filter（PII マスキング）

### 6.1 マスキング対象

Slack ユーザーが送信するメッセージ（プロンプト）および Claude の応答に含まれる可能性のある個人情報を、New Relic 側での保存前にマスキングする。

| 対象 | 正規表現 | 備考 |
|---|---|---|
| メールアドレス | `[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}` | 精度高・実用的 |
| 人名 | — | 日本語人名および英語人名の正規表現マスキングは**信頼性が低い**ため見送り。実装するならアプリ側でのマスキング（`bot/slack_handler.py` でメッセージ受信直後に処理）or 専用 PII 検出ライブラリの採用を検討する。 |

### 6.2 対象フィールド

`LlmChatCompletionMessage` の `content` フィールド（プロンプト・応答テキストが格納される）。

### 6.3 実装方針

| 手順 | 方法 | Terraform |
|---|---|---|
| メールアドレスマスキングルール作成 | `newrelic_pipeline_cloud_rule` リソース | ○ |

**Terraform リソース定義（概要）:**

```hcl
resource "newrelic_pipeline_cloud_rule" "mask_email" {
  name        = "AI Monitoring - Mask email addresses"
  description = "LlmChatCompletionMessage の content フィールドのメールアドレスをマスキング"
  action      = "OBFUSCATE"

  filter {
    nrql = "SELECT * FROM LlmChatCompletionMessage"
  }

  obfuscation_expression {
    name  = "email-pattern"
    regex = "[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}"
  }
}
```

> **注意**: `newrelic_pipeline_cloud_rule` は Terraform プロバイダー v3.68.0 以上で利用可能。旧 `newrelic_nrql_drop_rule` は 2026年6月30日 EOL のため使用しないこと。

---

## 7. Events to Metrics（長期集計）

### 7.1 目的

`LlmChatCompletionSummary` イベントのデフォルト保存期間は 30日（Original Data プラン）。モデルごとの月次トークン消費量を長期追跡するために、Events to Metrics 変換ルールを設定し、集計値を Dimensional Metrics（rollup 保存期間: **13ヶ月固定**）として保存する。

### 7.2 作成するルール

| 項目 | 値 |
|---|---|
| メトリクス名 | `llm.chat.completion.token_count` |
| 集計関数 | `summary(token_count)` |
| グループ化 | `response.model` |
| 元イベント | `LlmChatCompletionSummary` |

`summary()` 関数は sum / count / min / max を一度に生成するため、ダッシュボードで `sum()` を使って月次合計を算出できる。

### 7.3 Terraform 実装

```hcl
resource "newrelic_events_to_metrics_rule" "llm_token_count" {
  name        = "LLM Chat Completion Token Count by Model"
  description = "モデルごとのトークン使用量を Dimensional Metrics に変換（保存期間 13ヶ月）"
  nrql        = "SELECT summary(token_count) FROM LlmChatCompletionSummary FACET response.model"

  account_id  = var.newrelic_account_id
}
```

### 7.4 変換によって失われるもの

- 個別の LLM 呼び出しのトークン数詳細は 30日経過後に参照不可
- 変換後のメトリクスは**集計値のみ**（個別レコードの閲覧は不可）
- 変換はイベントを削除しない（元イベントはそのまま 30日保持される）

---

## 8. ダッシュボード

### 8.1 ウィジェット一覧

| # | タイトル | 内容 | データソース |
|---|---|---|---|
| 1 | モデルごとのトークン消費数 | 月次集計・モデル別棒グラフ | Events to Metrics から生成した `llm.chat.completion.token_count` メトリクス |
| 2 | 直近 3 件のエラー内容 | エラー発生日時・メッセージ・モデル名 | `LlmErrorMessage` イベント |
| 3 | トークン使用量が多い質問 TOP 3（直近 1ヶ月） | 質問テキスト・トークン数 | `LlmChatCompletionMessage` イベント |

### 8.2 ウィジェット詳細と NRQL

#### Widget 1: モデルごとのトークン消費数

```sql
FROM Metric
SELECT sum(`llm.chat.completion.token_count`)
FACET `response.model`
SINCE 12 MONTHS AGO
TIMESERIES 1 MONTH
```

- チャートタイプ: 積み上げ棒グラフ（Stacked Bar Chart）

#### Widget 2: 直近 3 件のエラー内容

```sql
FROM LlmErrorMessage
SELECT timestamp, error.message, error.code, response.model
SINCE 3 MONTHS AGO
ORDER BY timestamp DESC
LIMIT 3
```

- チャートタイプ: テーブル

#### Widget 3: トークン使用量が多い質問 TOP 3

```sql
FROM LlmChatCompletionMessage
SELECT content, token_count, timestamp
WHERE role = 'user'
SINCE 1 MONTH AGO
ORDER BY token_count DESC
LIMIT 3
```

- チャートタイプ: テーブル

### 8.3 【重要】ユーザー想定イベントタイプの修正

ウィジェット 3 について、要件では `LlmEmbedding` イベントを参照する想定が示されているが、**このイベントタイプは誤りである**。

| イベントタイプ | 実際の用途 |
|---|---|
| `LlmEmbedding` | テキストをベクトル変換する Embedding モデル（`text-embedding-3-small` 等）への呼び出し。本ボットは Embedding モデルを使用しておらず、このイベントは発生しない。 |
| `LlmChatCompletionMessage` | Chat Completion の各メッセージ（ユーザー質問・アシスタント応答）。`content` フィールドにテキスト、`token_count` フィールドにメッセージ単位のトークン数が格納される。**本ウィジェットで使用すべき正しいイベントタイプ。** |

上記の NRQL（`LlmChatCompletionMessage` / `role = 'user'`）を正式仕様として採用する。

### 8.4 Terraform 実装

ダッシュボードは `newrelic_one_dashboard_json` リソースで管理する。

```hcl
resource "newrelic_one_dashboard_json" "ai_monitoring" {
  json = file("${path.module}/dashboard.json")
}
```

ダッシュボード定義は `monitoring/dashboard.json` に JSON 形式で記述する。

---

## 9. ディレクトリ構成（追加分）

```
monitoring/                         # 新規作成 (New Relic Terraform)
├── main.tf                         # プロバイダー設定
├── variables.tf                    # 変数定義（NR アカウント ID 等）
├── events_to_metrics.tf            # Events to Metrics ルール
├── drop_rules.tf                   # Pipeline Cloud Rules（PII マスキング）
├── dashboard.tf                    # ダッシュボード
└── dashboard.json                  # ダッシュボード JSON 定義
```

**`monitoring/main.tf` 構成:**

```hcl
terraform {
  required_providers {
    newrelic = {
      source  = "newrelic/newrelic"
      version = "~> 3.68"
    }
  }
}

provider "newrelic" {
  account_id = var.newrelic_account_id
  api_key    = var.newrelic_api_key   # User API Key (NRAK-...)
  region     = "US"
}
```

**`monitoring/variables.tf`:**

```hcl
variable "newrelic_account_id" {
  description = "New Relic アカウント ID"
  type        = number
}

variable "newrelic_api_key" {
  description = "New Relic User API Key (NRAK-...)"
  type        = string
  sensitive   = true
}
```

> `var.newrelic_api_key` には既存の `chatbot/newrelic-api-key` シークレットの値（NR MCP 認証に使用している User API Key）をそのまま使用できる。

---

## 10. Terraform 実装可否まとめ

| 機能 | Terraform | リソース | 備考 |
|---|:---:|---|---|
| Events to Metrics ルール | ○ | `newrelic_events_to_metrics_rule` | |
| Drop Filter（PII マスキング） | ○ | `newrelic_pipeline_cloud_rule` | v3.68.0 以上必須 |
| ダッシュボード | ○ | `newrelic_one_dashboard_json` | |
| APM エージェントの有効化 | △ | なし（NR プロバイダー側に専用リソースなし） | ECS タスク定義の環境変数（CDK 側）で制御 |
| AI Monitoring の有効化 | △ | なし | `NEW_RELIC_AI_MONITORING_ENABLED=true` 環境変数で制御（CDK 側） |
| データ保持期間の変更 | × | なし | NR UI または NerdGraph API のみ対応 |

---

## 11. 手動対応事項

| # | 作業内容 | タイミング | 実施場所 |
|---|---|---|---|
| 1 | New Relic ライセンスキーの取得 | 初回のみ | NR UI → アカウント設定 → API Keys → 「INGEST - LICENSE」タイプのキーを作成 |
| 2 | `chatbot/newrelic-license-key` シークレットへの値登録 | `make deploy-secrets` 後 | `aws secretsmanager put-secret-value --secret-id chatbot/newrelic-license-key --secret-string "<key>"` |
| 3 | Terraform 変数ファイルの作成 | Terraform 初回適用前 | `monitoring/terraform.tfvars` に `newrelic_account_id` と `newrelic_api_key` を記載（`.gitignore` 対象） |
| 4 | データ保存期間の変更（任意） | 必要に応じて | NR UI → Administration → Data retention |

---

## 12. 実装タスク一覧

### Phase 1: CDK / コード変更（AWS 側）

- [ ] `infra/stacks/secrets_stack.py` に `chatbot/newrelic-license-key` シークレットを追加
- [ ] `infra/stacks/bot_stack.py` に NR ライセンスキー参照・ECS 環境変数を追加
- [ ] `bot/pyproject.toml` に `newrelic>=10.0` を追加
- [ ] `bot/main.py` に APM 初期化コードを追加
- [ ] `bot/slack_handler.py` に Background Task ラッパーを追加
- [ ] `make deploy-secrets` で新シークレットをデプロイ
- [ ] AWS CLI で `chatbot/newrelic-license-key` に値を登録
- [ ] `make deploy` でボット本体を再デプロイ

### Phase 2: Terraform（New Relic 側）

- [ ] `monitoring/` ディレクトリおよび `.tf` ファイルを作成
- [ ] `monitoring/terraform.tfvars` を作成（`.gitignore` に追加）
- [ ] `terraform init` → `terraform plan` → `terraform apply`
- [ ] ダッシュボードの表示を NR UI で確認

### Phase 3: 動作確認

- [ ] Slack でメッセージ送信 → NR APM にトランザクション（Background Task）が記録されること
- [ ] NR AI Monitoring → AI Entities にボットのサービスが表示されること
- [ ] `LlmChatCompletionSummary` イベントが NRQL で取得できること
- [ ] テストメッセージにメールアドレスを含め、NR 上で `[REDACTED]` にマスキングされていること
- [ ] ダッシュボードの各ウィジェットにデータが表示されること
