"""SMDA architecture names Binary Ninja can actually export.

IdaExporter only initializes Capstone for ``intel`` and ``aarch64``. Mapping other
BN arches would produce empty or corrupt reports, so they are rejected.
"""

from mcrit_similarity.errors import UnsupportedArchitectureError

SMDA_ARCHITECTURES = {
    "x86": "intel",
    "x86_64": "intel",
    "aarch64": "aarch64",
}


def smda_architecture(arch_name: str) -> str:
    mapped = SMDA_ARCHITECTURES.get(arch_name)
    if mapped is None:
        supported = ", ".join(sorted(SMDA_ARCHITECTURES))
        raise UnsupportedArchitectureError(
            f"Unsupported architecture {arch_name!r}. MCRIT Similarity exports {supported}."
        )
    return mapped
