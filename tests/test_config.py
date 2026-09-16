from mcrit_similarity.config import config_from_values


def test_defaults_and_clamping():
    config = config_from_values({})
    assert config.server.endswith("/")
    assert config.minhash_threshold == 50
    assert config.persist_samples is True
    assert config.apply_corpus_labels is False
    tight = config_from_values(
        {
            "server": "http://example:8000",
            "minhash_threshold": 200,
            "band_matches_required": 0,
            "include_library": "false",
            "persist_samples": "0",
            "apply_corpus_labels": "yes",
        }
    )
    assert tight.server == "http://example:8000/"
    assert tight.minhash_threshold == 100
    assert tight.band_matches_required == 1
    assert tight.include_library is False
    assert tight.persist_samples is False
    assert tight.apply_corpus_labels is True
