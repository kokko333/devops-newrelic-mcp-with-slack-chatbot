# 要件定義書 — Slack × New Relic MCP AI チャットボット (PoC)

作成日: 2026-05-30  

---

## 1. 概要

Slack から自然言語でメッセージを送ると、New Relic MCP サーバー経由でアカウントの監視データを取得し、Claude AI が回答を生成して返す AI チャットボット。  
AWS ECS Fargate 上で動作し、CDK でインフラを IaC 管理する。チームメンバーが同一 Slack ワークスペースから利用できる PoC として構成する。

---

## 2. 目的・背景

| 項目 | 内容 |
|------|------|
| 主目的 | New Relic の監視データを自然言語で問い合わせられる UI を Slack 上に実現する |
| 副目的 | MCP（Model Context Protocol）と Claude API の連携パターンを PoC として示す |
| 対象利用者 | 同一 Slack ワークスペースに属するチームメンバー（共有 New Relic アカウント） |
| 成功基準 | 「直近1時間のエラーレートは？」「CPU使用率が高いホストは？」等の質問に New Relic データを参照した回答が返ること |

---

## 3. 決定事項

| 項目 | 決定内容 | 決定根拠 |
|------|---------|---------|
| デプロイ先 | AWS ECS Fargate（CDK で IaC 管理） | Socket Mode は長期 WebSocket 接続を維持するため、タイムアウトのある Lambda は使えない |
| Slack 接続方式 | Socket Mode（公開エンドポイント不要） | インバウンド HTTP エンドポイント不要でシンプル。ALB・固定 IP・ngrok が不要 |
| New Relic MCP | 公式 MCP サーバー（`https://mcp.newrelic.com/mcp`） | New Relic が公式提供するリモートエンドポイント。サブプロセス管理不要 |
| New Relic 認証 | 共有アカウント（全ユーザーが同一 NR アカウントを参照） | PoC スコープで認証の複雑さを避けるため |
| Slack 受付範囲 | メンション + ダイレクトメッセージ | チャンネルでの共有利用と個別利用の両方に対応 |
| 会話履歴 | スレッド単位でインメモリ保持 | PoC スコープ。永続化は本番化時の課題 |
| プログラミング言語 | Python 3.12（Bot） + Python CDK（インフラ） | Anthropic SDK・Slack Bolt・MCP ライブラリのエコシステムが充実 |
| Claude アクセス方式 | Amazon Bedrock 経由 | AWS 統合請求・IAM ロール認証（Anthropic API キー不要）・コスト管理一元化 |
| ECS サブネット配置 | Public Subnet + Auto-assign Public IP | NAT Gateway は常時 ~$32/月の固定費が発生するため PoC では採用しない。SG のインバウンドルールをゼロにすることで外部接続を遮断しつつコストゼロでアウトバウンドを実現。本番化時は Private Subnet + NAT Gateway へ移行 |

---

## 4. システム構成

### 4.1 全体アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│  Slack Workspace                                            │
│  ユーザーが @bot メンションまたは DM でメッセージ送信           │
└───────────────────────┬─────────────────────────────────────┘
                        │ WebSocket (Socket Mode / アウトバウンド)
                        ▼
┌──────────────── AWS ────────────────────────────────────────┐
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  ECS Fargate Task (Bot Server - Python)             │   │
│  │                                                     │   │
│  │  ┌──────────────────┐  ┌─────────────────────────┐  │   │
│  │  │  Slack Handler   │  │  Conversation Manager   │  │   │
│  │  │  (Bolt SDK)      │  │  (スレッド別 in-memory)  │  │   │
│  │  └────────┬─────────┘  └─────────────────────────┘  │   │
│  │           │                                          │   │
│  │           ▼                                          │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │  Claude Client (Amazon Bedrock SDK)          │   │   │
│  │  │  - NR MCPツール定義を注入してツール使用を実行  │   │   │
│  │  │  - ツール呼び出しが発生したら NR MCP を叩く   │   │   │
│  │  └───────────┬──────────────────┬───────────────┘   │   │
│  │              │                  │ AWS API            │   │
│  │  ┌───────────▼──────────────┐   │ (IAM ロール認証)   │   │
│  │  │  NR MCP HTTP Client      │   │                   │   │
│  │  │  POST .../mcp            │   │                   │   │
│  │  │  Header: Api-Key: ...    │   │                   │   │
│  │  └──────────────────────────┘   │                   │   │
│  └─────────────────────────────────┼───────────────────┘   │
│                                    │                        │
│  ┌─────────────────────────────────▼────────────────────┐   │
│  │  Amazon Bedrock                                      │   │
│  │  モデル: anthropic.claude-sonnet-4-6-...              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────┐   │
│  │  Secrets     │  │  ECR             │  │  CloudWatch │   │
│  │  Manager     │  │  (コンテナ image) │  │  Logs       │   │
│  └──────────────┘  └──────────────────┘  └─────────────┘   │
└─────────────────────────────────────────┬───────────────────┘
                                          │ HTTPS
                         ┌────────────────▼───────────────────┐
                         │  New Relic MCP Server (Public Preview)│
                         │  https://mcp.newrelic.com/mcp      │
                         │  (35+ ツール: NRQL, APM, etc.)      │
                         └────────────────┬───────────────────┘
                                          │
                         ┌────────────────▼───────────────────┐
                         │  New Relic                         │
                         │  (NerdGraph API / NRQL Insights)   │
                         └────────────────────────────────────┘

※ 外部通信: Slack WebSocket・New Relic MCP (HTTPS)
※ Bedrock へのアクセスは AWS 内部 API（IAM ロール認証、API キー不要）
※ インバウンドのエンドポイントは不要（Socket Mode を使用するため）
```

### 4.2 MCP 通信の仕組み

**なぜ Anthropic SDK の MCP コネクタを使わないか**

Anthropic API の `mcp_servers` パラメータは認証に `Authorization: Bearer <token>` 形式のみ対応。一方 New Relic MCP は `Api-Key: <key>` ヘッダーを要求するため、認証方式が不一致。そのため Bot が MCP HTTP クライアントとして NR MCP を直接呼び出す方式を採用する。

**Bot の処理フロー（1リクエストあたり）**

```
1. [起動時] NR MCP に tools/list リクエスト → ツール定義をキャッシュ
2. [メッセージ受信] Slack から事象を受け取り即時 ACK
3. [Claude 呼び出し] 会話履歴 + NR ツール定義を渡して Messages API を呼び出す
4. [ツール実行ループ]
   while Claude のレスポンスに tool_use ブロックがある:
     a. 各ツール呼び出しを NR MCP HTTP に転送（Api-Key ヘッダー付き）
     b. 結果を tool_result として Claude に返す
5. [回答生成] Claude が最終テキスト回答を生成
6. [Slack 返信] スレッドに回答を投稿（処理中メッセージを更新）
```

---

## 5. 機能要件

### 5.1 コア機能

| ID | 機能 | 詳細 |
|----|------|------|
| F-01 | Slack メッセージ受信 | `@bot` メンション および ダイレクトメッセージを受信する |
| F-02 | New Relic データ参照 | NR MCP の 35+ ツール（NRQL, APM, Logs, Alerts, Infrastructure 等）を利用 |
| F-03 | AI 回答生成 | 取得データをコンテキストとして Amazon Bedrock 経由の Claude claude-sonnet-4-6 が自然言語で回答を生成する |
| F-04 | Slack スレッド返信 | メンションが行われたスレッドに返信する（DM の場合はスレッド内） |
| F-05 | 会話履歴保持 | 同一スレッド内の直前 10 件のやりとりをコンテキストとして保持する |
| F-06 | 処理中表示 | 応答待ち中に「調査中...」メッセージを送信し、完了後に編集で更新する |

### 5.2 インタラクション例

```
ユーザー: @bot 直近1時間でエラーレートが最も高いアプリは？
ボット:   🔍 調査中...
ボット:   直近1時間のデータを確認しました。

          エラーレートが最も高いアプリは **payment-service** です。
          - エラーレート: 3.2%（全体平均 0.4%）
          - 主なエラー: `NullPointerException` 127件

ユーザー: そのアプリのスループットも教えて
ボット:   payment-service のスループット（直近1時間）:
          - 平均: 342 rpm
          - ピーク: 891 rpm（14:32 JST）
```

### 5.3 New Relic MCP が提供するツール（主要なもの）

New Relic MCP v1 (Public Preview) は以下のカテゴリのツールを提供する：

| カテゴリ | ツール例 |
|---------|---------|
| NRQL | 任意の NRQL クエリ実行 |
| APM | アプリケーションパフォーマンス、エラーレート、スループット |
| Infrastructure | ホスト CPU/メモリ、コンテナ、Kubernetes |
| Logs | ログ検索・分析 |
| Alerts | アラートポリシー、インシデント状況 |
| Dashboards | ダッシュボード情報取得 |

> **前提**: New Relic アカウントで MCP Public Preview を有効化が必要  
> 有効化方法: NR UI 右下のユーザー名 → Administration → Previews & Trials → New Relic AI MCP server

---

## 6. 非機能要件

| 項目 | 要件 |
|------|------|
| Slack 応答時間 | 受信から 3 秒以内に ACK（Slack タイムアウト対応）。最終回答は 60 秒以内 |
| 同時処理 | 複数ユーザーからの同時リクエストを非同期で処理（async/await） |
| セキュリティ | Slack・NR の API キーは Secrets Manager で管理。Bedrock は IAM ロール認証（API キー不要）。コードへのハードコード禁止 |
| ログ | CloudWatch Logs にリクエスト/レスポンスのサマリーを出力 |
| コスト効率 | ECS Fargate 最小スペック（0.25 vCPU / 0.5 GB）で起動 |
| 可搬性 | CDK デプロイコマンド数本で環境再現可能なこと |

---

## 7. AWS インフラ構成（CDK）

### 7.1 使用 AWS サービス

| サービス | 用途 | 備考 |
|---------|------|------|
| ECS Fargate | Bot サーバーの常時稼働 | Socket Mode は長期 WebSocket 接続が必要なため Lambda 不可 |
| ECR | コンテナイメージ保存 | CDK でリポジトリ作成 |
| Amazon Bedrock | Claude モデルの推論 | IAM ロール認証（API キー不要）。事前にモデルアクセスの有効化が必要 |
| Secrets Manager | API キー管理 | 4 シークレット（Slack × 2・NR × 2） |
| CloudWatch Logs | ログ収集 | 3 日保持 |
| IAM | タスクロール | Secrets Manager 読み取り + `bedrock:InvokeModel` の最小権限 |
| VPC | ネットワーク | 本システム専用 VPC を作成。ECS タスクは Public Subnet + Auto-assign Public IP で配置 |

> **インバウンドエンドポイント不要**: Socket Mode はアウトバウンド WebSocket のみのため、ALB や固定 IP は不要。

### 7.2 CDK スタック構成

```
infra/
├── app.py                      # CDK エントリーポイント
├── stacks/
│   └── bot_stack.py            # メインスタック
│       ├── VPC（専用）+ Public Subnet
│       ├── ECR Repository
│       ├── ECS Cluster
│       ├── Fargate Task Definition
│       │   ├── Container: bot (Python 3.12-slim)
│       │   └── IAM Task Role (Secrets Manager アクセス)
│       ├── ECS Service (desiredCount=1)
│       ├── Secrets Manager (4 シークレット)
│       └── CloudWatch Log Group
└── pyproject.toml
```

### 7.3 AWS Secrets Manager に格納する認証情報

| シークレット名 | 内容 | 取得元 |
|--------------|------|--------|
| `chatbot/slack-bot-token` | Bot User OAuth Token | Slack App 管理画面 |
| `chatbot/slack-app-token` | Socket Mode 用 App Token | Slack App 管理画面 |
| `chatbot/newrelic-api-key` | New Relic User API Key (`NRAK-...`) | NR UI → API Keys |
| `chatbot/newrelic-account-id` | New Relic アカウント ID | NR アカウント設定 |

> **Bedrock 認証は IAM ロールで行うため Anthropic API キーは不要。**  
> ECS タスクロールに `bedrock:InvokeModel` 権限を付与することで API キーなしでモデルを呼び出せる。

---

## 8. 技術スタック

### Bot サーバー（Python 3.12）

```
slack-bolt          # Slack Bolt SDK（Socket Mode 対応）
anthropic[bedrock]  # Claude API クライアント（Bedrock バックエンド対応版）
httpx               # NR MCP への非同期 HTTP クライアント
boto3               # AWS Secrets Manager・Bedrock Runtime クライアント
python-dotenv       # ローカル開発用環境変数管理
```

### インフラ（Python CDK v2）

```
aws-cdk-lib         # CDK コアライブラリ
constructs          # CDK コンストラクト
```

---

## 9. ディレクトリ構成

```
devops-newrelic-mcp-with-slack-chatbot/
├── _design/
│   └── requirements.md          # 本文書
├── bot/
│   ├── main.py                  # エントリーポイント・起動処理
│   ├── slack_handler.py         # Slack イベント受信・返信処理
│   ├── claude_client.py         # Claude API 呼び出し・ツール実行ループ
│   ├── nr_mcp_client.py         # New Relic MCP HTTP クライアント
│   ├── conversation_manager.py  # スレッド別会話履歴管理
│   ├── Dockerfile
│   └── pyproject.toml
├── infra/
│   ├── app.py                   # CDK アプリエントリーポイント
│   ├── stacks/
│   │   └── bot_stack.py         # ECS/ECR/Secrets Manager 等
│   └── pyproject.toml
├── .env.example                 # ローカル開発用サンプル
└── README.md                    # セットアップ手順（Slack App 作成含む）
```

---

## 10. 開発フェーズ（PoC）

### Phase 1: ローカル動作確認

コードは常に環境変数から認証情報を読む設計にするため、ECS とローカルで同一コードが動く。

| 依存先 | ECS 上 | ローカル |
|--------|--------|---------|
| Slack / NR 認証情報 | Secrets Manager 経由で環境変数として注入 | `.env` ファイルに記載 |
| Bedrock 認証 | ECS タスクロール（boto3 が自動検出） | `~/.aws/credentials`（`aws configure` 済みなら設定不要）|
| ログ | CloudWatch Logs | stdout |
| ECS / ECR | 必須 | 不要 |

- [ ] `aws configure` または `.env` に AWS 認証情報を設定
- [ ] `.env` ファイルで Slack・NR 認証情報を設定してローカル起動
- [ ] Slack Socket Mode でメッセージ受信確認
- [ ] NR MCP ツール一覧取得確認
- [ ] Claude (Bedrock) + NR MCP ツール使用の E2E 動作確認

### Phase 2: AWS デプロイ

- [ ] ECR にコンテナイメージをプッシュ
- [ ] CDK deploy でインフラ構築
- [ ] Secrets Manager に認証情報を登録
- [ ] ECS タスクの起動確認・ログ確認

### Out of Scope（PoC 後の課題）

- 永続的な会話履歴保存（DynamoDB 等）
- ユーザーごとの NR 認証・権限管理
- コスト管理・レート制限
- 本番向けスケーリング（desiredCount > 1 / マルチ AZ）
- 監視・アラート設定
- CI/CD パイプライン

---

## 11. セットアップ前提条件（PoC 開始前に必要なもの）

- [ ] New Relic アカウントで **MCP Public Preview を有効化**  
      NR UI → ユーザー名 → Administration → Previews & Trials → New Relic AI MCP server
- [ ] Slack ワークスペースへの管理者権限（App インストール用）
- [ ] AWS アカウント + CDK ブートストラップ済み環境
- [ ] Amazon Bedrock で **Claude claude-sonnet-4-6 のモデルアクセスを有効化**  
      AWS コンソール → Amazon Bedrock → Model access → Anthropic → claude-sonnet-4-6

---

## 12. 参考リソース

- [New Relic MCP ドキュメント](https://docs.newrelic.com/docs/agentic-ai/mcp/overview/)
- [New Relic MCP セットアップ](https://docs.newrelic.com/docs/agentic-ai/mcp/setup/)
- [New Relic MCP Public Preview 発表](https://docs.newrelic.com/whats-new/2025/11/whats-new-11-05-mcp-server/)
- [Slack Bolt SDK (Python)](https://slack.dev/bolt-python/)
- [Amazon Bedrock - Claude モデルの使用](https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html)
- [Anthropic SDK (Bedrock バックエンド)](https://docs.anthropic.com/en/api/claude-on-amazon-bedrock)
- [AWS CDK Python リファレンス](https://docs.aws.amazon.com/cdk/api/v2/python/)
