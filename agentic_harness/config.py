import os
import re
from pathlib import Path

# The project's own folder, not the working directory - so the same file is
# found however the service is started.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load_env_file(path=None) -> int:
    """Load settings from a .env file into os.environ, so they don't each
    have to be exported before starting. Returns how many were set.

    One KEY=VALUE per line; blank lines and # comments ignored; an
    optional leading "export " (so the file can also be sourced by a
    shell); a value may be wrapped in single or double quotes, and an
    unquoted one may end in a " # comment". A variable that's already set
    in the real environment wins over the file, so a one-off override on
    the command line still works.

    Path: AGENTIC_ENV_FILE if set, else .env in the project folder. A missing
    file is fine - everything then comes from the environment as before.
    """
    path = Path(path or os.environ.get("AGENTIC_ENV_FILE") or ENV_FILE)
    if not path.is_file():
        return 0
    loaded = 0
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not _NAME.fullmatch(key):
            print(f"[config] {path}:{number}: ignoring line that isn't KEY=VALUE")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    print(f"[config] loaded {loaded} setting(s) from {path}")
    return loaded
