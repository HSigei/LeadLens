variable "project" { type = string }
variable "environment" { type = string }
variable "aws_region" { type = string }
variable "image" { type = string }
variable "certificate_arn" { type = string }
variable "vpc_id" { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "private_subnet_ids" { type = list(string) }
variable "public_base_url" { type = string }
variable "report_sender" { type = string }
variable "report_recipients" { type = string }
variable "alert_email" { type = string }
variable "sentry_dsn" {
	type = string
	default = ""
}
variable "sentry_environment" {
	type = string
	default = "production"
}
variable "sentry_traces_sample_rate" {
	type = string
	default = "0.1"
}
variable "dashboard_jwt_secret_arn" { type = string }
variable "groq_api_key_arn" { type = string }
variable "call_center_webhook_secret_arn" { type = string }
variable "tenant_policy_file" {
	type = string
	default = "/service/policies/tenants.json"
}
variable "tenant_policy_json" {
	type = string
	sensitive = true
}