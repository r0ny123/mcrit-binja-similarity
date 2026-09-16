"""Compare two binaries with the MCRIT similarity provider (Ultimate only).

Mirrors the official Binary Ninja headless similarity example:
https://docs.binary.ninja/guide/similarity.html#python-and-headless-usage
"""

from __future__ import annotations

import argparse
import gc
import time

from binaryninja import SimilarityProviderType, SimilaritySession, SimilaritySessionNode, load
from binaryninja.log import LogLevel, log_to_stdout


def wait_for_completion(completion, timeout: float = 600) -> bool:
    deadline = time.monotonic() + timeout
    while not completion.is_finished and time.monotonic() < deadline:
        time.sleep(0.1)
    return completion.is_finished


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("primary", help="reference binary (A)")
    parser.add_argument("secondary", help="binary to match (B)")
    parser.add_argument(
        "--timeout", type=float, default=600, help="seconds to wait for the session"
    )
    args = parser.parse_args()

    log_to_stdout(LogLevel.InfoLog)
    provider_type = SimilarityProviderType["MCRIT"]
    settings = provider_type.get_default_settings()
    provider = provider_type.create(settings)
    if provider is None:
        raise SystemExit("MCRIT provider could not be created (Ultimate required, check settings)")

    session = SimilaritySession()
    session.add_provider(provider)
    with load(args.primary) as primary, load(args.secondary) as secondary:
        old_node = SimilaritySessionNode(primary)
        new_node = SimilaritySessionNode(secondary)
        session.graph.add_node(old_node)
        session.graph.add_node(new_node)
        session.graph.add_edge(old_node, new_node)

        completion = session.run()
        if not wait_for_completion(completion, args.timeout):
            completion.request_stop()
            wait_for_completion(completion, timeout=10)
            raise SystemExit("MCRIT session did not finish before the timeout")

        for entity_id in new_node.entities:
            entity = new_node.get_entity(entity_id)
            if entity is None:
                continue
            for result_id in new_node.get_results(entity_id):
                result = new_node.get_result(result_id)
                if result is None:
                    continue
                matched_name = provider.get_name(new_node, entity_id, result_id) or "unknown"
                print(f"{entity.name} -> {matched_name}: {result.similarity / 255:.1%}")

        # Drop session objects while the views are still alive. If they survive until
        # interpreter shutdown, Binary Ninja logs thousands of ignored destructor errors.
        del old_node, new_node, session, provider
        gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
