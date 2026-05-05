# State lives in a GCS bucket created out-of-band. See README.md for bootstrap.
# The bucket name is supplied at init time:
#   terraform init -backend-config="bucket=<your-bucket>"
terraform {
  backend "gcs" {
    prefix = "terraform/state"
  }
}
