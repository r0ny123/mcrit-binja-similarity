"""Provider configuration. Parsing is Binary Ninja-free so tests can cover it."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SERVER = "http://127.0.0.1:8000/"
DEFAULT_TIMEOUT = 30
DEFAULT_MINHASH_THRESHOLD = 50
DEFAULT_PICHASH_SIZE = 10
DEFAULT_BAND_MATCHES = 2
DEFAULT_MAX_RESULTS = 5

GROUP = "mcrit"
PROVIDER_SETTINGS_ID = "mcrit-similarity"


@dataclass(frozen=True)
class ProviderConfig:
    server: str = DEFAULT_SERVER
    username: str = ""
    api_token: str = ""
    timeout: int = DEFAULT_TIMEOUT
    minhash_threshold: int = DEFAULT_MINHASH_THRESHOLD
    pichash_size: int = DEFAULT_PICHASH_SIZE
    band_matches_required: int = DEFAULT_BAND_MATCHES
    max_results: int = DEFAULT_MAX_RESULTS
    include_library: bool = True
    persist_samples: bool = True
    apply_corpus_labels: bool = False
    query_per_node: bool = False
    family: str = ""
    version: str = ""


def _as_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def config_from_values(
    values: dict[str, object], defaults: ProviderConfig | None = None
) -> ProviderConfig:
    base = defaults or ProviderConfig()
    server = _as_str(values.get("server"), base.server).strip() or base.server
    if server and not server.endswith("/"):
        server += "/"
    return ProviderConfig(
        server=server,
        username=_as_str(values.get("username"), base.username).strip(),
        api_token=_as_str(values.get("api_token"), base.api_token),
        timeout=_as_int(values.get("timeout"), base.timeout, 1, 3600),
        minhash_threshold=_as_int(values.get("minhash_threshold"), base.minhash_threshold, 0, 100),
        pichash_size=_as_int(values.get("pichash_size"), base.pichash_size, 0, 1000),
        band_matches_required=_as_int(
            values.get("band_matches_required"), base.band_matches_required, 1, 64
        ),
        max_results=_as_int(values.get("max_results"), base.max_results, 1, 50),
        include_library=_as_bool(values.get("include_library"), base.include_library),
        persist_samples=_as_bool(values.get("persist_samples"), base.persist_samples),
        apply_corpus_labels=_as_bool(values.get("apply_corpus_labels"), base.apply_corpus_labels),
        query_per_node=_as_bool(values.get("query_per_node"), base.query_per_node),
        family=_as_str(values.get("family"), base.family).strip(),
        version=_as_str(values.get("version"), base.version).strip(),
    )
