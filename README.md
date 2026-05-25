# us-econ

Periodic collectors that land U.S. economic data into BigQuery. Each collector runs as a Cloud Run Job on its own schedule, sharing a single container image.

## Architecture

```
                                  ┌───────────────────────┐
   Cloud Scheduler ──invoke──►    │  Cloud Run Job        │
   (per source)                   │  python -m collectors.<source>
                                  └────┬──────────────────┘
                                       │ 1. fetch from source (API/file/scrape/PDF)
                                       │ 2. write JSON-NL to GCS (raw archive)
                                       ▼
                              gs://<project>-raw/<source>/dt=YYYY-MM-DD/run_*.jsonl
                                       │
                                       │ 3. load_table_from_uri (free batch load)
                                       ▼
                              BigQuery: <project>.<dataset>.<table>  (append-only)
```

- **One container, many Jobs.** Every source ships in the same image; each Cloud Run Job overrides args to pick its entrypoint (e.g. `python -m collectors.bls_employment`).
- **Append-only tables.** Revisions become new rows tagged with `ingested_at` / `ingestion_run_id`. BLS vintage history is preserved for free.
- **GCS doubles as the load source.** We never use streaming inserts; the raw archive *is* the BigQuery load file. Both BQ batch loads and GCS lifecycle-tiered storage are essentially free.

## Repo layout

```
collectors/
  common/                shared runner, logging, http, secrets, gcs/bq helpers
  bls_employment/        first collector: monthly Employment Situation (BLS API v2)
terraform/
  *.tf                   project-wide infra (datasets, buckets, IAM, registry)
  modules/cloud_run_job  per-collector module (Run Job + Scheduler + IAM)
  collectors.tf          one module instantiation per collector
Dockerfile               single image, Python 3.12-slim
pyproject.toml           dependencies + tooling
```

## Bootstrap (one-time, manual)

These steps create the things Terraform itself depends on. Done once per environment.

1. **Create a GCP project and link billing.**
   ```sh
   gcloud projects create <project-id>
   gcloud billing projects link <project-id> --billing-account=<billing-account-id>
   gcloud config set project <project-id>
   ```

2. **Create a state bucket for Terraform.** Pick any globally-unique name; suggest `<project-id>-tfstate`.
   ```sh
   gcloud storage buckets create gs://<project-id>-tfstate \
     --location=US \
     --uniform-bucket-level-access \
     --public-access-prevention
   gcloud storage buckets update gs://<project-id>-tfstate --versioning
   ```

3. **Push this repo to GitHub.** The Cloud Build trigger needs an existing remote. Make a note of `<owner>/<repo>` for [terraform.tfvars](terraform/terraform.tfvars).

4. **Connect the GitHub repo to Cloud Build.**
   - Install the Google Cloud Build GitHub App on the repo: <https://github.com/marketplace/google-cloud-build>
   - In GCP Console: **Cloud Build → Triggers → Manage repositories → Connect repository → "GitHub (Cloud Build GitHub App)"** → select the repo. This creates the connection that Terraform's `github` block resolves against.

5. **Authenticate locally.**
   ```sh
   gcloud auth login
   gcloud auth application-default login
   ```

## Apply infrastructure

```sh
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: set project_id, github_owner, github_repo

terraform init -backend-config="bucket=<project-id>-tfstate"
terraform apply
```

The first apply will fail at the Cloud Run Job step because no container image exists yet — but everything else (APIs, IAM, dataset, bucket, registry, **Cloud Build trigger**) will have applied. That's expected. Seed the first image (next section) and re-apply.

## Building and pushing the image

### Continuous: Cloud Build on push to main

The Terraform stack provisions a Cloud Build trigger that watches the GitHub repo. Any push to `main` that touches `collectors/`, `Dockerfile`, `pyproject.toml`, `uv.lock`, or `cloudbuild.yaml` runs [cloudbuild.yaml](cloudbuild.yaml), which builds the container and pushes two tags to Artifact Registry: `:<SHORT_SHA>` and `:latest`. The Cloud Run Job pulls `:latest`, so the next execution after a successful build picks up the new code automatically — no `terraform apply` required.

Watch a build:
```sh
gcloud builds list --ongoing
gcloud builds log <build-id>
```

### One-time: seed the first image manually

Before the very first `terraform apply` succeeds, the registry is empty. Either push a commit to trigger Cloud Build, or build once locally:

```sh
# From the repo root.
gcloud auth configure-docker <region>-docker.pkg.dev

IMAGE=$(terraform -chdir=terraform output -raw image_repo)/collector:latest
docker build -t "$IMAGE" .
docker push "$IMAGE"

terraform -chdir=terraform apply
```

> The Cloud Run Job's `image` field is in `lifecycle.ignore_changes`, so re-tagging `:latest` from CI never causes Terraform drift.

## (Optional) Register a BLS API key

Without a key, the BLS API allows 25 queries/day per IP, which is plenty for a single daily job. If you want headroom or hit the limit:

1. Register at <https://data.bls.gov/registrationEngine/>.
2. Store the key as a Secret Manager version:
   ```sh
   echo -n "<your-bls-key>" | gcloud secrets versions add bls-api-key --data-file=-
   ```

The collector picks it up automatically next run.

## Adding a new collector

1. Create `collectors/<name>/` with `__init__.py`, `collect.py` (exporting `collect(settings) -> LoadSpec`), and `__main__.py` that calls `run_collector("<name>", collect)`.
2. If it needs a new dataset, add a `google_bigquery_dataset` and a corresponding `google_bigquery_dataset_iam_member` for `collector_runtime` in [terraform/iam.tf](terraform/iam.tf).
3. Add a `module "<name>"` block in [terraform/collectors.tf](terraform/collectors.tf) with the appropriate schedule.
4. Rebuild the image, `terraform apply`.

## Off-platform collectors

A handful of sources reject requests from Google Cloud's egress IP ranges, so those collectors cannot run on Cloud Run alongside the others. Their code still lives in this repo, and their BigQuery datasets are still provisioned by [terraform/bigquery.tf](terraform/bigquery.tf), but there is intentionally **no** `module` in [terraform/collectors.tf](terraform/collectors.tf) and **no** runner-SA dataset IAM binding in [terraform/iam.tf](terraform/iam.tf) for these.

Instead, each runs from a residential / non-cloud network (e.g. a MacBook, a MacStadium-hosted virtual Mac) invoked by `cron`, mirroring the cadence its Cloud Run Job would have used. The cron entry runs `uv run python -m collectors.<name>` with `GCP_PROJECT`, `RAW_BUCKET`, and `BQ_LOCATION` set in the environment, plus application-default credentials (either a personal user authorized as a BigQuery dataEditor on the dataset and Storage objectAdmin on the raw bucket, or `--impersonate-service-account=runner@...`). The runner writes to the same GCS raw bucket and BigQuery datasets as everything else; only the *invocation host* differs.

If any of these sources ever stops blocking GCP, the path back to Cloud Run is just to restore its `module` block and dataEditor IAM binding and re-apply.

### `rcp_potus_approval`

[`realclearpolling.com`](https://www.realclearpolling.com) returns `403 Forbidden` to GCP egress. Code at [collectors/rcp_potus_approval/](collectors/rcp_potus_approval/), dataset `rcp_potus_approval`. Invoked once per day at 09:00 MT.

### `google_trends`

`trends.google.com` consistently returns `400` to GCP egress (Google blocks Trends scraping from its own cloud). Code at [collectors/google_trends/](collectors/google_trends/), dataset `google_trends`. Invoked weekly Thursdays at ~06:00 MT, just before the claims collector lands the new release — so the freshest Trends snapshot is available to the claims forecast Scheduled Query. The collector re-pulls a 5-yr weekly window on every run (Trends' relative renormalization rules that out being incremental); each run is vintage-stamped and append-only, so downstream consumers pick the latest vintage per week.

## Local development

[uv](https://docs.astral.sh/uv/) manages the Python interpreter, the venv, and dependencies. Install it once (`winget install astral-sh.uv` on Windows, or see uv's docs).

```sh
uv sync                # creates .venv, installs runtime + dev deps, generates uv.lock
```

uv reads [.python-version](.python-version) and downloads Python 3.12 if it isn't present. Activation isn't required — prefix commands with `uv run`:

```powershell
$env:GCP_PROJECT = "<project-id>"
$env:RAW_BUCKET  = "<project-id>-raw"
$env:BQ_LOCATION = "US"

uv run python -m collectors.bls_employment
```

This will write to the real GCS bucket and BigQuery dataset, so use a sandbox project or accept the writes.

Common uv operations:

| Task | Command |
| --- | --- |
| Add a runtime dep | `uv add <pkg>` |
| Add a dev dep | `uv add --dev <pkg>` |
| Update lockfile | `uv lock --upgrade` |
| Run tests | `uv run pytest` |
| Run linter | `uv run ruff check .` |

Commit `uv.lock` — it's the reproducibility contract used by both local dev and the Docker build.
