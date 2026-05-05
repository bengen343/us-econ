variable "name" {
  description = "Short identifier for this collector. Used as the Cloud Run Job and Scheduler name."
  type        = string
}

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "image" {
  description = "Fully-qualified container image URI to run."
  type        = string
}

variable "args" {
  description = "Args passed to the container entrypoint, e.g. [\"collectors.bls_employment\"]."
  type        = list(string)
}

variable "env" {
  description = "Plain environment variables for the container."
  type        = map(string)
  default     = {}
}

variable "service_account_email" {
  description = "SA used both as the Job's runtime identity and as Cloud Scheduler's OAuth identity for invoking it."
  type        = string
}

variable "schedule" {
  description = "Cron expression for Cloud Scheduler."
  type        = string
}

variable "schedule_timezone" {
  type    = string
  default = "Etc/UTC"
}

variable "timeout" {
  description = "Per-execution timeout."
  type        = string
  default     = "600s"
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "512Mi"
}

variable "max_retries" {
  type    = number
  default = 1
}
