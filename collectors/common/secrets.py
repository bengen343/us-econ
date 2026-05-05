from google.api_core import exceptions as gax_exceptions
from google.cloud import secretmanager


def get_secret(project_id: str, name: str, *, version: str = "latest") -> str | None:
    client = secretmanager.SecretManagerServiceClient()
    secret_path = client.secret_version_path(project_id, name, version)
    try:
        response = client.access_secret_version(name=secret_path)
    except gax_exceptions.NotFound:
        return None
    return response.payload.data.decode("utf-8")
