"""Turn a provider configuration into a client for the server it names."""

from __future__ import annotations

from dataclasses import replace

from mcrit_similarity import builtin
from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.mcrit.client import McritClient


def resolve(config: ProviderConfig) -> ProviderConfig:
    """The configuration as used: the built-in server becomes its URL, starting it if needed."""
    if not builtin.is_builtin(config.server):
        return config
    endpoint = builtin.endpoint()
    # The built-in server holds nothing until samples are uploaded, and it runs every job inside
    # the request that submits it, so a request lasts as long as indexing or matching does.
    return replace(
        config,
        server=endpoint.url,
        api_token=endpoint.token,
        username="",
        persist_samples=True,
        timeout=0,
    )


def client_for(config: ProviderConfig) -> McritClient:
    return McritClient(
        config.server,
        apitoken=config.api_token or None,
        username=config.username or None,
        timeout=config.timeout,
    )
