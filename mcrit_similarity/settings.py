"""Binary Ninja Settings: global defaults plus isolated provider-instance schema."""

from __future__ import annotations

import json

from mcrit_similarity.config import (
    DEFAULT_BAND_MATCHES,
    DEFAULT_MAX_RESULTS,
    DEFAULT_MINHASH_THRESHOLD,
    DEFAULT_PICHASH_SIZE,
    DEFAULT_SERVER,
    DEFAULT_TIMEOUT,
    GROUP,
    PROVIDER_SETTINGS_ID,
    ProviderConfig,
    config_from_values,
)
from mcrit_similarity.log import log_error

SECRET_KEY = "api_token"
KEYCHAIN_PROVIDER = "SystemSecretsProvider"

_PROVIDER_SCHEMA = (
    ("server", "string", DEFAULT_SERVER, "MCRIT server URL for this session."),
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
        "Upload missing samples so MatcherVs can run. Required unless both SHA256s are already on the server.",
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
    settings.register_group(GROUP, "MCRIT")
    declarations = {
        "server": {
            "title": "Server URL",
            "type": "string",
            "default": DEFAULT_SERVER,
            "description": "Default MCRIT server URL.",
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
            "minValue": 1,
            "maxValue": 3600,
            "description": "HTTP timeout in seconds.",
            "ignore": ["SettingsProjectScope", "SettingsResourceScope"],
        },
    }
    for key, properties in declarations.items():
        if not settings.register_setting(f"{GROUP}.{key}", json.dumps(properties)):
            log_error(f"Failed to register {GROUP}.{key}")
    _migrate_token(settings, SecretsProvider)


def _migrate_token(settings, secrets_provider_cls) -> str:
    # An empty field is indistinguishable from the default Binary Ninja reports after the token
    # has been moved to the keychain, so it must never be read as a request to delete the token.
    full_key = f"{GROUP}.{SECRET_KEY}"
    provider = secrets_provider_cls.get(KEYCHAIN_PROVIDER)
    typed = settings.get_string(full_key)
    if typed:
        if provider is not None and provider.store_data(full_key, typed):
            settings.reset(full_key)
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
    settings.register_group(GROUP, "MCRIT")
    number_ranges = {
        "minhash_threshold": (0, 100),
        "pichash_size": (0, 1000),
        "band_matches_required": (1, 64),
        "max_results": (1, 50),
    }
    try:
        current_server = global_defaults().server
    except Exception:
        current_server = DEFAULT_SERVER

    for key, type_name, default, description in _PROVIDER_SCHEMA:
        eff_default = current_server if key == "server" else default
        properties = {
            "title": key.replace("_", " ").title(),
            "type": type_name,
            "default": eff_default,
            "description": description,
            "ignore": ["SettingsResourceScope"],
        }
        if key in number_ranges:
            properties["minValue"], properties["maxValue"] = number_ranges[key]
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
        "server": _read_setting(settings, "server", "string", "") or base.server,
        "username": base.username,
        "api_token": base.api_token,
        "timeout": base.timeout,
        "minhash_threshold": _read_setting(
            settings, "minhash_threshold", "number", base.minhash_threshold
        ),
        "pichash_size": _read_setting(settings, "pichash_size", "number", base.pichash_size),
        "band_matches_required": _read_setting(
            settings, "band_matches_required", "number", base.band_matches_required
        ),
        "max_results": _read_setting(settings, "max_results", "number", base.max_results),
        "include_library": _read_setting(
            settings, "include_library", "boolean", base.include_library
        ),
        "persist_samples": _read_setting(
            settings, "persist_samples", "boolean", base.persist_samples
        ),
        "apply_corpus_labels": _read_setting(
            settings, "apply_corpus_labels", "boolean", base.apply_corpus_labels
        ),
        "query_per_node": _read_setting(settings, "query_per_node", "boolean", base.query_per_node),
        "family": _read_setting(settings, "family", "string", base.family),
        "version": _read_setting(settings, "version", "string", base.version),
    }
    return config_from_values(values, base)
