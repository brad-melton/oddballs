"""
Loads .env.local (gitignored) into os.environ if present, so scripts run
locally can pick up TURSO_DATABASE_URL / TURSO_AUTH_TOKEN without ever
passing them as command-line arguments or committing them to git. See
.env.example for the expected format.

Import this before anything that reads those env vars:
    import env_local  # noqa: F401

No-op if .env.local doesn't exist, and never overrides a real environment
variable that's already set (e.g. on a deploy host).
"""
import os

_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")


def load(path: str = _ENV_FILE) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


load()
