"""Settings: environment variable, else the value saved from the Settings page,
else the default. A setting fixed by the environment cannot be changed from the
page, so a container definition stays the single source of truth for it."""

import os
from dataclasses import dataclass
from pathlib import Path

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


def _same_mount(a: Path, b: Path) -> bool:
    """True when a rename between the two folders is possible. Two bind
    mounts of one dataset have different st_dev in a container, so this is
    what decides whether moves are instant or copy-and-verify."""
    return a.stat().st_dev == b.stat().st_dev


def validate(values: dict[str, str]) -> dict:
    """Check proposed values without saving. Returns errors and warnings."""
    merged = {s.key: get(s.key) for s in SETTINGS} | values
    errors, warnings = [], []
    library = Path(merged["library_path"])
    quarantine = Path(merged["quarantine_path"])
    if not library.is_dir():
        errors.append(f"Library folder {library} does not exist inside the container.")
    elif not os.access(library, os.R_OK | os.X_OK):
        errors.append(f"Library folder {library} is not readable.")
    if quarantine.exists() and not quarantine.is_dir():
        errors.append(f"Quarantine path {quarantine} is a file.")
    elif not quarantine.exists():
        warnings.append(f"Quarantine folder {quarantine} does not exist yet; it will be created on first use.")
    if library.is_dir() and not os.access(library, os.W_OK):
        warnings.append("The library is read-only: scanning works, quarantine and restore do not.")
    if library.is_dir() and quarantine.is_dir():
        if not os.access(quarantine, os.W_OK):
            errors.append(f"Quarantine folder {quarantine} is not writable.")
        elif not _same_mount(library, quarantine):
            warnings.append("Library and quarantine are on different mounts: files will be copied and verified "
                            "instead of moved instantly. Mount one folder that contains both to avoid this.")
        if quarantine.resolve().is_relative_to(library.resolve()):
            errors.append("The quarantine folder must not be inside the library, or quarantined files get scanned.")
    try:
        if int(merged["workers"]) < 1:
            raise ValueError
    except ValueError:
        errors.append("Workers must be a whole number of at least 1.")
    url = merged["lidarr_url"].strip()
    if url and not url.startswith(("http://", "https://")):
        errors.append("Lidarr URL must start with http:// or https://.")
    return {"errors": errors, "warnings": warnings}
