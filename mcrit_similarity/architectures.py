"""SMDA architecture names Binary Ninja can actually export.

SMDA's Binary Ninja exporter only initializes Capstone for ``intel``, ``aarch64`` and
little-endian 32-bit ``arm``. Other BN arches are rejected up front with a plugin error instead of
SMDA's bare ``ValueError``.
"""

from mcrit_similarity.errors import UnsupportedArchitectureError

SMDA_ARCHITECTURES = {
    "x86": "intel",
    "x86_64": "intel",
    "aarch64": "aarch64",
    "armv7": "arm",
    "thumb2": "arm",
}


def smda_architecture(arch_name: str) -> str:
    mapped = SMDA_ARCHITECTURES.get(arch_name)
    if mapped is None:
        supported = ", ".join(sorted(SMDA_ARCHITECTURES))
        raise UnsupportedArchitectureError(
            f"Unsupported architecture {arch_name!r}. MCRIT Similarity exports {supported}."
        )
    return mapped
