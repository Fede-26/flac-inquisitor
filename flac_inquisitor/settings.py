"""Settings: environment variable, else the value saved from the Settings page,
else the default. A setting fixed by the environment cannot be changed from the
page, so a container definition stays the single source of truth for it."""

import os
from dataclasses import dataclass

from flac_inquisitor import db


@dataclass(frozen=True)
class Setting:
    key: str
    env: str
    default: str
    label: str
    help: str
    secret: bool = False


SETTINGS = [
    Setting("library_path", "LIBRARY_PATH", "/music", "Library",
            "Folder scanned for FLAC files."),
    Setting("quarantine_path", "QUARANTINE_PATH", "/quarantine", "Quarantine",
            "Where quarantined files are moved. Put it on the same mount as the library for instant moves."),
    Setting("workers", "WORKERS", "2", "Workers", "Files analyzed in parallel."),
    Setting("exclude", "EXCLUDE", "", "Exclude",
            "Glob patterns relative to the library, one per line (e.g. */Podcasts/*)."),
    Setting("lidarr_url", "LIDARR_URL", "", "Lidarr URL", "Optional, e.g. http://lidarr:8686."),
    Setting("lidarr_api_key", "LIDARR_API_KEY", "", "Lidarr API key", "Settings > General in Lidarr.",
            secret=True),
    Setting("lidarr_path_prefix", "LIDARR_PATH_PREFIX", "", "Library path in Lidarr",
            "Only if Lidarr sees the library under a different path than this app."),
]
BY_KEY = {s.key: s for s in SETTINGS}


def get(key: str) -> str:
    s = BY_KEY[key]
    if s.env in os.environ:
        return os.environ[s.env]
    saved = db.get_setting(key)
    return saved if saved is not None else s.default


def describe() -> list[dict]:
    out = []
    for s in SETTINGS:
        locked = s.env in os.environ
        saved = db.get_setting(s.key)
        source = "env" if locked else ("db" if saved is not None else "default")
        out.append({"key": s.key, "env": s.env, "label": s.label, "help": s.help, "secret": s.secret,
                    "value": get(s.key), "source": source, "locked": locked})
    return out


def save(values: dict[str, str]) -> None:
    for key, value in values.items():
        s = BY_KEY[key]
        if s.env in os.environ:
            raise ValueError(f"{s.label} is set by the {s.env} environment variable.")
        db.set_setting(key, value)
