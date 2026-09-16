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
