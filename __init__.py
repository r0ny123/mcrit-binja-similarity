"""Binary Ninja entry point for the MCRIT Similarity provider."""

from mcrit_similarity import __version__

__all__ = ["__version__"]


def _register() -> None:
    from binaryninja import BinaryViewType, core_product

    from mcrit_similarity.log import log_info
    from mcrit_similarity.settings import register_global_settings

    register_global_settings()
    # binaryninja.similarity is pure Python and imports on every edition, so importing it
    # proves nothing. Only the license distinguishes Ultimate.
    if "ultimate" not in (core_product() or "").lower():
        log_info("MCRIT Similarity requires Binary Ninja Ultimate 6.0+")
        return
    from mcrit_similarity.provider import McritProviderType

    McritProviderType().register()
    BinaryViewType.add_binaryview_initial_analysis_completion_event(_preload_in_background)
    log_info("Registered MCRIT Binary Similarity provider")


def _preload_in_background(bv) -> None:
    import threading

    from mcrit_similarity.export import preload_report

    # BinaryViewEvent callbacks run one after another; hashing must not hold up the others.
    threading.Thread(target=preload_report, args=(bv,), name="mcrit-preload", daemon=True).start()


try:
    import binaryninja
except ImportError:
    binaryninja = None

if binaryninja is not None:
    _register()
