import os

import aws_cdk as cdk
from aws_cdk import (
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

# bot/ ディレクトリへの絶対パス（このファイルの 2 階層上 → プロジェクトルート → bot/）
_BOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "bot")


class BotStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, bedrock_model_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ------------------------------------------------------------------ #
        # VPC  (Public Subnet のみ / NAT Gateway なし)
        # 決定根拠: NAT Gateway は ~$32/月の固定費。SG でインバウンドを全拒否し
        # Public IP を付与することでコストゼロでアウトバウンドを実現する。
        # ------------------------------------------------------------------ #
        vpc = ec2.Vpc(
            self,
            "BotVpc",
            max_azs=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
            nat_gateways=0,
        )

        # ---- Security Group (インバウンド全拒否 / アウトバウンド全許可) ----
        sg = ec2.SecurityGroup(
            self,
            "BotSG",
            vpc=vpc,
            description="NewRelic Slack Bot - outbound only",
            allow_all_outbound=True,
        )

        # ------------------------------------------------------------------ #
        # Secrets Manager  (ChatbotSecretsStack で作成済みのシークレットを参照)
        # ------------------------------------------------------------------ #
        slack_bot_token = secretsmanager.Secret.from_secret_name_v2(
            self, "SlackBotToken", "chatbot/slack-bot-token"
        )
        slack_app_token = secretsmanager.Secret.from_secret_name_v2(
            self, "SlackAppToken", "chatbot/slack-app-token"
        )
        nr_api_key = secretsmanager.Secret.from_secret_name_v2(
            self, "NrApiKey", "chatbot/newrelic-api-key"
        )
        nr_account_id = secretsmanager.Secret.from_secret_name_v2(
            self, "NrAccountId", "chatbot/newrelic-account-id"
        )

        # ------------------------------------------------------------------ #
        # IAM Task Role
        # ------------------------------------------------------------------ #
        task_role = iam.Role(
            self,
            "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="ECS task role for NewRelic Slack Bot",
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    # クロスリージョン推論プロファイル（jp.* / global.*）はアカウント ID 付き ARN
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/jp.anthropic.*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/global.anthropic.*",
                    # 推論プロファイルが内部で呼び出すファンデーションモデル（アカウント ID なし）
                    "arn:aws:bedrock:*::foundation-model/anthropic.*",
                ],
            )
        )

        # ------------------------------------------------------------------ #
        # CloudWatch Log Group (3 日保持)
        # ------------------------------------------------------------------ #
        log_group = logs.LogGroup(
            self,
            "BotLogGroup",
            log_group_name="/ecs/newrelic-slack-bot",
            retention=logs.RetentionDays.THREE_DAYS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------------ #
        # ECS Cluster
        # ------------------------------------------------------------------ #
        cluster = ecs.Cluster(self, "BotCluster", vpc=vpc)

        # ------------------------------------------------------------------ #
        # Container Image (CDK deploy 時に自動ビルド & ECR push)
        # ------------------------------------------------------------------ #
        image_asset = ecr_assets.DockerImageAsset(
            self,
            "BotImage",
            directory=_BOT_DIR,
        )

        # ------------------------------------------------------------------ #
        # Fargate Task Definition (0.25 vCPU / 0.5 GB)
        # ------------------------------------------------------------------ #
        task_def = ecs.FargateTaskDefinition(
            self,
            "BotTaskDef",
            cpu=256,
            memory_limit_mib=512,
            task_role=task_role,
        )

        task_def.add_container(
            "BotContainer",
            image=ecs.ContainerImage.from_docker_image_asset(image_asset),
            environment={
                "AWS_DEFAULT_REGION": self.region,
                "BEDROCK_MODEL_ID": bedrock_model_id,
            },
            secrets={
                "SLACK_BOT_TOKEN": ecs.Secret.from_secrets_manager(slack_bot_token),
                "SLACK_APP_TOKEN": ecs.Secret.from_secrets_manager(slack_app_token),
                "NEW_RELIC_API_KEY": ecs.Secret.from_secrets_manager(nr_api_key),
                "NEW_RELIC_ACCOUNT_ID": ecs.Secret.from_secrets_manager(nr_account_id),
            },
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="bot",
                log_group=log_group,
            ),
        )

        # ------------------------------------------------------------------ #
        # ECS Fargate Service
        # Public Subnet + assign_public_ip=True でアウトバウンド通信を実現
        # ------------------------------------------------------------------ #
        ecs.FargateService(
            self,
            "BotService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[sg],
        )
