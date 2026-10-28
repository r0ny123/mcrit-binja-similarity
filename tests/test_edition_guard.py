"""The Ultimate check must gate registration, not the (always-succeeding) module import."""

import importlib

import binaryninja
import pytest

from mcrit_similarity.provider import McritProviderType

entry = importlib.import_module("__init__")


@pytest.fixture
def registrations(monkeypatch):
    calls = []
    monkeypatch.setattr(McritProviderType, "register", lambda self: calls.append(self))
    return calls


@pytest.mark.parametrize(
    "product, expected",
    [
        ("Binary Ninja Ultimate", 1),
        ("Binary Ninja Commercial", 0),
        ("Binary Ninja Free", 0),
        (None, 0),
    ],
)
def test_registers_only_on_ultimate(monkeypatch, registrations, product, expected):
    monkeypatch.setattr(binaryninja, "core_product", lambda: product)
    entry._register()
    assert len(registrations) == expected


def test_preload_outlives_the_event_callbacks_view_wrapper(monkeypatch):
    import threading

    from mcrit_similarity import export

    class Wrapper:
        """Like the BinaryView Binary Ninja builds per event and drops after the callback."""

    seen = []
    done = threading.Event()
    monkeypatch.setattr(export, "preload_report", lambda bv: (seen.append(bv), done.set()))
    entry._preload_in_background(Wrapper())
    assert done.wait(5)
    assert isinstance(seen[0], Wrapper)
