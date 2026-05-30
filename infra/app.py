#!/usr/bin/env python3
import os
import sys

import aws_cdk as cdk
from stacks.bot_stack import BotStack
from stacks.secrets_stack import SecretsStack


def _require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        sys.exit(f"Error: .env に {key!r} が設定されていません")
    return value


app = cdk.App()

region = _require_env("AWS_DEFAULT_REGION")
bedrock_model_id = _require_env("BEDROCK_MODEL_ID")

# アカウント ID は CDK CLI が認証情報から自動解決して CDK_DEFAULT_ACCOUNT にセットする
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=region)

SecretsStack(app, "ChatbotSecretsStack", env=env)
BotStack(app, "NewRelicBotStack", bedrock_model_id=bedrock_model_id, env=env)

app.synth()
