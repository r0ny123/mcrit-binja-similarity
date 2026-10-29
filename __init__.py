"""Binary Ninja entry point for the MCRIT Similarity provider."""

from mcrit_similarity import __version__

__all__ = ["__version__"]


def _register() -> None:
    from binaryninja import BinaryViewType, PluginCommand, core_product

    from mcrit_similarity import commands
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
    # Global: these need no open file.
    PluginCommand.register_global(
        commands.COMMAND_NAME,
        "Check the MCRIT server URL and API token",
        commands.run_connection_check,
    )
    PluginCommand.register_global(
        commands.IMPORT_COMMAND,
        "Load an MCRIT export file into the built-in server",
        commands.run_corpus_import,
    )
    PluginCommand.register_global(
        commands.EXPORT_COMMAND,
        "Save the built-in server's corpus as an MCRIT export file",
        commands.run_corpus_export,
    )
    PluginCommand.register(
        commands.ADD_COMMAND,
        "Upload this binary to the MCRIT corpus with a family and version",
        commands.run_add_binary,
    )
    PluginCommand.register_for_function(
        commands.MATCHES_COMMAND,
        "Match this function against the whole MCRIT corpus",
        commands.run_corpus_matches,
    )
    log_info("Registered MCRIT Binary Similarity provider")


def _preload_in_background(bv) -> None:
    import threading

    from mcrit_similarity.export import preload_report

    # BinaryViewEvent callbacks run one after another; hashing must not hold up the others.
    # The thread must hold ``bv`` strongly: Binary Ninja builds a fresh wrapper for each event
    # and drops it when the callback returns, so a weak reference is already dead.
    threading.Thread(target=preload_report, args=(bv,), name="mcrit-preload", daemon=True).start()


try:
    import binaryninja
except ImportError:
    binaryninja = None

if binaryninja is not None:
    _register()
