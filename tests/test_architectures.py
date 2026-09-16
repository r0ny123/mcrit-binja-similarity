import pytest

from mcrit_similarity.architectures import smda_architecture
from mcrit_similarity.errors import UnsupportedArchitectureError


def test_supported_arches():
    assert smda_architecture("x86") == "intel"
    assert smda_architecture("x86_64") == "intel"
    assert smda_architecture("aarch64") == "aarch64"


def test_rejects_unexportable_arches():
    with pytest.raises(UnsupportedArchitectureError, match="armv7"):
        smda_architecture("armv7")
