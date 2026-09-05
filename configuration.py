"""Load and validate taskboard settings using Python 3.11+ tomllib."""

import os
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    "identity": {"display_name": "User"},
    "server": {"host": "127.0.0.1", "port": 7778},
    "storage": {"database": "data/taskboard.db"},
    "models": {"low": "", "medium": "", "high": "", "very_high": ""},
}


def load_config(path=None, overrides=None, environ=None):
    path = Path(path) if path is not None else ROOT / "taskboard.toml"
    env = os.environ if environ is None else environ
    config = {section: values.copy() for section, values in DEFAULTS.items()}
    try:
        with path.open("rb") as source:
            supplied = tomllib.load(source)
    except FileNotFoundError:
        supplied = {}
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Cannot read configuration {path}: {error}") from error
    for section, values in supplied.items():
        if section not in config or not isinstance(values, dict):
            raise ValueError(f"Invalid configuration section: {section}")
        for key, value in values.items():
            if key not in config[section]:
                raise ValueError(f"Unknown configuration setting: {section}.{key}")
            config[section][key] = value
    # Validate the file itself, including settings later overridden.
    validate(config)
    for variable, section, key in (
        ("TASKBOARD_HOST", "server", "host"),
        ("TASKBOARD_PORT", "server", "port"),
        ("TASKBOARD_DB", "storage", "database"),
        ("TASKBOARD_DISPLAY_NAME", "identity", "display_name"),
    ):
        if variable in env:
            value = env[variable]
            if key == "port":
                try:
                    value = int(value)
                except ValueError as error:
                    raise ValueError("TASKBOARD_PORT must be an integer") from error
            config[section][key] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            section, setting = {
                "host": ("server", "host"), "port": ("server", "port"),
                "db": ("storage", "database"),
                "display_name": ("identity", "display_name"),
            }[key]
            config[section][setting] = str(value) if isinstance(value, Path) else value
    validate(config)
    for key, value in config["storage"].items():
        location = Path(value).expanduser()
        config["storage"][key] = (ROOT / location).resolve()
    return config


def validate(config):
    for section, values in config.items():
        for key, value in values.items():
            if section == "server" and key == "port":
                if type(value) is not int or not 1 <= value <= 65535:
                    raise ValueError("server.port must be an integer from 1 to 65535")
            elif not isinstance(value, str) or (section != "models" and not value.strip()):
                raise ValueError(f"{section}.{key} must be a non-empty string")
    name = config["identity"]["display_name"]
    if len(name.strip()) > 100:
        raise ValueError("identity.display_name must be at most 100 characters")
    config["identity"]["display_name"] = name.strip()
