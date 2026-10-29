"""Binary Ninja Settings: global defaults plus isolated provider-instance schema."""

from __future__ import annotations

import functools
import json

from mcrit_similarity.builtin import is_builtin
from mcrit_similarity.config import (
    DEFAULT_BAND_MATCHES,
    DEFAULT_MAX_RESULTS,
    DEFAULT_MINHASH_THRESHOLD,
    DEFAULT_PICHASH_SIZE,
    DEFAULT_SERVER,
    DEFAULT_TIMEOUT,
    GROUP,
    NUMBER_RANGES,
    PROVIDER_SETTINGS_ID,
    ProviderConfig,
    config_from_values,
)
from mcrit_similarity.log import log_error, log_warn
from mcrit_similarity.mcrit.client import is_loopback

SECRET_KEY = "api_token"
KEYCHAIN_PROVIDER = "SystemSecretsProvider"

# The disassembler plugin (mcrit-plugin) registers its own settings under the "mcrit" group,
# so this plugin's keys live under "mcrit_similarity" and both stay apart in the Settings UI.
_GROUP_TITLE = "MCRIT Binary Similarity"

_PROVIDER_SCHEMA = (
    (
        "server",
        "string",
        DEFAULT_SERVER,
        "MCRIT server URL for this session, or builtin to run MCRIT inside Binary Ninja.",
    ),
    ("minhash_threshold", "number", DEFAULT_MINHASH_THRESHOLD, "Minimum MinHash score (0-100)."),
    ("pichash_size", "number", DEFAULT_PICHASH_SIZE, "Minimum function size for PicHash matches."),
    (
        "band_matches_required",
        "number",
        DEFAULT_BAND_MATCHES,
        "LSH bands that must hit before a MinHash candidate is scored.",
    ),
    ("max_results", "number", DEFAULT_MAX_RESULTS, "Maximum MCRIT candidates kept per function."),
    ("include_library", "boolean", True, "Include matches flagged as library functions."),
    (
        "persist_samples",
        "boolean",
        True,
        "Upload missing samples so MatcherVs can run; an upload sends the binary's disassembly to the server. On by default for a local server, always on for the built-in one, off for a remote one. Required unless both SHA256s are already on the server.",
    ),
    (
        "apply_corpus_labels",
        "boolean",
        False,
        "When applying a match, rename a function that still has an auto-generated name to the matched function's MCRIT label.",
    ),
    (
        "query_per_node",
        "boolean",
        False,
        "When a function set has two or more incoming nodes, match it once against the whole MCRIT corpus and split the result per edge. Faster for many incoming nodes on a small corpus; slower on a large corpus.",
    ),
    ("family", "string", "", "Optional family name stored on uploaded samples."),
    ("version", "string", "", "Optional version string stored on uploaded samples."),
)


def register_global_settings() -> None:
    from binaryninja import SecretsProvider, Settings

    settings = Settings()
    settings.register_group(GROUP, _GROUP_TITLE)
    declarations = {
        "server": {
            "title": "Server URL",
            "type": "string",
            "default": DEFAULT_SERVER,
            "description": "builtin runs MCRIT inside Binary Ninja; otherwise the MCRIT server or mcritweb URL.",
            "ignore": ["SettingsProjectScope", "SettingsResourceScope"],
        },
        "username": {
            "title": "Username",
            "type": "string",
            "default": "",
            "description": "Username header sent to mcritweb.",
            "ignore": ["SettingsProjectScope", "SettingsResourceScope"],
        },
        SECRET_KEY: {
            "title": "API token",
            "type": "string",
            "default": "",
            "description": "mcritweb API token. Stored in the system keychain; the field is cleared once moved there.",
            "hidden": True,
            "ignore": ["SettingsProjectScope", "SettingsResourceScope"],
        },
        "timeout": {
            "title": "Request timeout",
            "type": "number",
            "default": DEFAULT_TIMEOUT,
            "minValue": NUMBER_RANGES["timeout"][0],
            "maxValue": NUMBER_RANGES["timeout"][1],
            "description": "HTTP timeout in seconds.",
            "ignore": ["SettingsProjectScope", "SettingsResourceScope"],
        },
    }
    for key, properties in declarations.items():
        if not settings.register_setting(f"{GROUP}.{key}", json.dumps(properties)):
            log_error(f"Failed to register {GROUP}.{key}")
    _migrate_token(settings, SecretsProvider)


@functools.cache
def _warn_plaintext_token() -> None:
    log_warn(
        "No system keychain is available; the MCRIT API token stays in Binary Ninja's settings"
    )


def _migrate_token(settings, secrets_provider_cls) -> str:
    # An empty field is indistinguishable from the default Binary Ninja reports after the token
    # has been moved to the keychain, so it must never be read as a request to delete the token.
    full_key = f"{GROUP}.{SECRET_KEY}"
    provider = secrets_provider_cls.get(KEYCHAIN_PROVIDER)
    typed = settings.get_string(full_key).strip()
    if typed:
        if provider is not None and provider.store_data(full_key, typed):
            settings.reset(full_key)
        else:
            _warn_plaintext_token()
        return typed
    if provider is not None and provider.has_data(full_key):
        return provider.get_data(full_key)
    return ""


def global_defaults() -> ProviderConfig:
    from binaryninja import SecretsProvider, Settings

    settings = Settings()
    token = _migrate_token(settings, SecretsProvider)
    values = {
        "server": settings.get_string(f"{GROUP}.server")
        if settings.contains(f"{GROUP}.server")
        else DEFAULT_SERVER,
        "username": (
            settings.get_string(f"{GROUP}.username")
            if settings.contains(f"{GROUP}.username")
            else ""
        ),
        "api_token": token,
        "timeout": (
            settings.get_integer(f"{GROUP}.timeout")
            if settings.contains(f"{GROUP}.timeout")
            else DEFAULT_TIMEOUT
        ),
    }
    return config_from_values(values)


def default_provider_settings():
    from binaryninja import Settings

    settings = Settings(PROVIDER_SETTINGS_ID)
    settings.register_group(GROUP, _GROUP_TITLE)
    try:
        current_server = global_defaults().server
    except Exception:
        current_server = DEFAULT_SERVER

    dynamic_defaults = {
        "server": current_server,
        "persist_samples": is_builtin(current_server) or is_loopback(current_server),
    }
    for key, type_name, default, description in _PROVIDER_SCHEMA:
        eff_default = dynamic_defaults.get(key, default)
        properties = {
            "title": key.replace("_", " ").title(),
            "type": type_name,
            "default": eff_default,
            "description": description,
            "ignore": ["SettingsResourceScope"],
        }
        if key in NUMBER_RANGES:
            properties["minValue"], properties["maxValue"] = NUMBER_RANGES[key]
        settings.register_setting(f"{GROUP}.{key}", json.dumps(properties))
    return settings


def _read_setting(settings, key: str, type_name: str, default):
    full = f"{GROUP}.{key}"
    if not settings.contains(full):
        return default
    if type_name == "boolean":
        return settings.get_bool(full)
    if type_name == "number":
        return settings.get_integer(full)
    return settings.get_string(full)


def config_from_settings(settings, defaults: ProviderConfig | None = None) -> ProviderConfig:
    base = defaults or global_defaults()
    values = {
        key: _read_setting(settings, key, type_name, getattr(base, key))
        for key, type_name, *_ in _PROVIDER_SCHEMA
    }
    return config_from_values(values, base)
