import aws_cdk as cdk
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class SecretsStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        secretsmanager.Secret(self, "SlackBotToken", secret_name="chatbot/slack-bot-token")
        secretsmanager.Secret(self, "SlackAppToken", secret_name="chatbot/slack-app-token")
        secretsmanager.Secret(self, "NrApiKey", secret_name="chatbot/newrelic-api-key")
        secretsmanager.Secret(self, "NrAccountId", secret_name="chatbot/newrelic-account-id")
        secretsmanager.Secret(self, "NrLicenseKey", secret_name="chatbot/newrelic-license-key")
