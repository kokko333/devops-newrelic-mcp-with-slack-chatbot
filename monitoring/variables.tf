variable "newrelic_region" {
  description = "New Relic データセンターリージョン（US または EU）。one.newrelic.com でログインする場合は US、one.eu.newrelic.com の場合は EU"
  type        = string
  default     = "US"

  validation {
    condition     = contains(["US", "EU"], var.newrelic_region)
    error_message = "newrelic_region は \"US\" または \"EU\" のいずれかを指定してください。"
  }
}

variable "newrelic_app_name" {
  description = "New Relic APM に表示されるアプリケーション名"
  type        = string
  default     = "newrelic-slack-bot"
}

variable "newrelic_account_id" {
  description = "New Relic アカウント ID（NR UI > 右上アバター > API keys > Account ID）"
  type        = number
}

variable "newrelic_api_key" {
  description = "New Relic User API Key（type: User, NRAK-...）"
  type        = string
  sensitive   = true
}
