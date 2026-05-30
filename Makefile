SHELL := /bin/bash
.DEFAULT_GOAL := help

# プロジェクトルートの .env を読み込み、全変数をシェル環境変数としてエクスポートする。
# make deploy 等でも .env の値が参照できるようになる。
ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: install run infra-install bootstrap deploy-secrets deploy destroy destroy-secrets clean help

# ---- Bot -------------------------------------------------------------------

install:  ## bot の依存ライブラリを bot/.venv にインストール
	cd bot && uv sync

run:  ## bot をローカルで起動（プロジェクトルートの .env を読み込む）
	cd bot && uv run python main.py

# ---- Infra (CDK) -----------------------------------------------------------

infra-install:  ## CDK の依存ライブラリを infra/.venv にインストール
	cd infra && uv sync

bootstrap: infra-install  ## CDK ブートストラップ（アカウント×リージョン(.env指定) の初回のみ）
	cd infra && cdk bootstrap

deploy-secrets: infra-install  ## Secrets Manager リソースをデプロイ（初回のみ / 値の登録は別途必要）
	cd infra && cdk deploy ChatbotSecretsStack

deploy: infra-install  ## ボット本体をデプロイ（deploy-secrets と値の登録が完了していること）
	cd infra && cdk deploy NewRelicBotStack

destroy: infra-install  ## ボット本体（ECS/VPC 等）を削除
	cd infra && cdk destroy NewRelicBotStack

destroy-secrets: infra-install  ## Secrets Manager リソースを削除（シークレット値も消えるため注意）
	cd infra && cdk destroy ChatbotSecretsStack

# ---- Utility ---------------------------------------------------------------

clean:  ## bot/.venv と infra/.venv を削除
	rm -rf bot/.venv infra/.venv

help:  ## このヘルプを表示
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
