SHELL := /bin/bash
.DEFAULT_GOAL := help

# プロジェクトルートの .env を読み込み、全変数をシェル環境変数としてエクスポートする。
# make deploy 等でも .env の値が参照できるようになる。
ifneq (,$(wildcard .env))
include .env
export
endif

# .env の NR 設定を Terraform 変数にマッピングする
export TF_VAR_newrelic_account_id = $(NEW_RELIC_ACCOUNT_ID)
export TF_VAR_newrelic_api_key    = $(NEW_RELIC_API_KEY)
export TF_VAR_newrelic_app_name   = $(NEW_RELIC_APP_NAME)

.PHONY: run bootstrap deploy-secrets deploy destroy destroy-secrets \
        monitoring-plan monitoring-apply monitoring-destroy \
        clean help

# ---- Bot -------------------------------------------------------------------

run:  ## 依存ライブラリをインストールして bot を起動（プロジェクトルートの .env を読み込む）
	cd bot && uv sync && uv run python main.py

# ---- Infra (CDK) -----------------------------------------------------------

bootstrap:  ## CDK ブートストラップ（アカウント×リージョン(.env指定) の初回のみ）
	cd infra && uv sync && cdk bootstrap

deploy-secrets:  ## Secrets Manager リソースをデプロイ（初回のみ / 値の登録は別途必要）
	cd infra && uv sync && cdk deploy ChatbotSecretsStack

deploy:  ## ボット本体をデプロイ（deploy-secrets と値の登録が完了していること）
	cd infra && uv sync && cdk deploy NewRelicBotStack

destroy:  ## ボット本体（ECS/VPC 等）を削除
	cd infra && uv sync && cdk destroy NewRelicBotStack

destroy-secrets:  ## Secrets Manager リソースを削除（シークレット値も消えるため注意）
	cd infra && uv sync && cdk destroy ChatbotSecretsStack

# ---- Monitoring (Terraform / New Relic) ------------------------------------

monitoring-plan:  ## Terraform init + plan（monitoring/ - 変更内容を確認）
	cd monitoring && terraform init && terraform plan

monitoring-apply:  ## Terraform init + apply（monitoring/ - New Relic リソースを作成・更新）
	cd monitoring && terraform init && terraform apply

monitoring-destroy:  ## Terraform init + destroy（monitoring/ - New Relic リソースを削除）
	cd monitoring && terraform init && terraform destroy

# ---- Utility ---------------------------------------------------------------

clean:  ## bot/.venv と infra/.venv を削除
	rm -rf bot/.venv infra/.venv

help:  ## このヘルプを表示
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
