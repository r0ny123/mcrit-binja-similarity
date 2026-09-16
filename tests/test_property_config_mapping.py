"""Properties of configuration parsing, entity mapping, arch mapping and token migration."""

from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mcrit_similarity.architectures import SMDA_ARCHITECTURES, smda_architecture
from mcrit_similarity.config import DEFAULT_SERVER, ProviderConfig, config_from_values
from mcrit_similarity.errors import UnsupportedArchitectureError
from mcrit_similarity.mapping import Entity, MatchPair, index_entities, lookup_entity, pair_matches
from mcrit_similarity.mcrit.parse import FunctionMatch
from mcrit_similarity.settings import KEYCHAIN_PROVIDER, _migrate_token

PROPERTY = settings(deadline=None, max_examples=150)

values = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**12), max_value=10**12),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=20),
    st.lists(st.integers(), max_size=2),
)
RANGES = {
    "timeout": (1, 3600),
    "minhash_threshold": (0, 100),
    "pichash_size": (0, 1000),
    "band_matches_required": (1, 64),
    "max_results": (1, 50),
}


@given(st.dictionaries(st.sampled_from(sorted(ProviderConfig().__dict__)), values, max_size=14))
@PROPERTY
def test_every_field_keeps_its_declared_type_and_range(raw):
    config = config_from_values(raw)
    for field, (low, high) in RANGES.items():
        number = getattr(config, field)
        assert isinstance(number, int) and not isinstance(number, bool)
        assert low <= number <= high
    for field in ("server", "username", "api_token", "family", "version"):
        assert isinstance(getattr(config, field), str)
    for field in ("include_library", "persist_samples", "apply_corpus_labels", "query_per_node"):
        assert isinstance(getattr(config, field), bool)
    assert config.server.endswith("/")


@given(st.dictionaries(st.sampled_from(sorted(ProviderConfig().__dict__)), values, max_size=14))
@PROPERTY
def test_parsing_a_parsed_config_changes_nothing(raw):
    once = config_from_values(raw)
    assert config_from_values(once.__dict__) == once


@pytest.mark.parametrize(
    ("given_server", "expected"),
    [
        ("", DEFAULT_SERVER),
        ("   ", DEFAULT_SERVER),
        ("https://h:8000", "https://h:8000/"),
        ("https://h:8000/", "https://h:8000/"),
        ("  https://h/api  ", "https://h/api/"),
    ],
)
def test_server_urls_are_normalised(given_server, expected):
    assert config_from_values({"server": given_server}).server == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("YES", True), (" 0 ", False), ("no", False), (1, True), (0, False)],
)
def test_boolean_strings_from_settings_are_understood(raw, expected):
    assert config_from_values({"query_per_node": raw}).query_per_node is expected


def test_unknown_boolean_text_keeps_the_default():
    assert config_from_values({"query_per_node": "maybe"}).query_per_node is False
    base = ProviderConfig(query_per_node=True)
    assert config_from_values({"query_per_node": "maybe"}, base).query_per_node is True


@given(st.integers(min_value=0, max_value=2**64 - 1))
@PROPERTY
def test_thumb_addresses_resolve_through_their_even_alias(address):
    entity = Entity(entity_id=1, address=address, name="f")
    index = index_entities([entity])
    assert lookup_entity(index, address) is entity
    assert lookup_entity(index, address | 1) is entity


def test_the_first_entity_wins_an_alias_collision():
    even = Entity(1, 0x1000, "even")
    odd = Entity(2, 0x1001, "odd")
    index = index_entities([even, odd])
    assert lookup_entity(index, 0x1000) is even
    assert lookup_entity(index, 0x1001) is odd
    assert index_entities([odd, even])[0x1000] is odd


def match(source_offset=0x1000, target_offset=None, **kwargs):
    return replace(
        FunctionMatch(
            source_offset=source_offset,
            target_offset=target_offset,
            source_function_id=1,
            target_function_id=42,
            target_sample_id=1,
            target_family_id=0,
            score=90.0,
            flags=1,
            num_bytes=128.0,
        ),
        **kwargs,
    )


@given(
    st.lists(st.integers(min_value=0, max_value=4), max_size=8),
    st.sets(st.integers(min_value=0, max_value=2), max_size=3),
)
@PROPERTY
def test_pairing_only_emits_resolved_and_scheduled_matches(indexes, scheduled):
    sources = [Entity(index, 0x1000 + 0x10 * index, f"s{index}") for index in range(5)]
    targets = [Entity(index, 0x2000 + 0x10 * index, f"t{index}") for index in range(5)]
    source_index = index_entities(sources)
    target_index = index_entities(targets)
    matches = [
        match(source_offset=sources[i].address, target_offset=targets[i].address) for i in indexes
    ]
    paired, unresolved = pair_matches(matches, source_index, target_index, scheduled)
    assert unresolved == []
    assert all(isinstance(pair, MatchPair) for pair in paired)
    assert {pair.source_entity_id for pair in paired} <= scheduled
    for pair in paired:
        assert 0 <= pair.similarity <= 255
        assert 0 <= pair.confidence <= 255


def test_unresolvable_matches_are_reported_separately():
    sources = index_entities([Entity(1, 0x1000, "s")])
    targets = index_entities([Entity(2, 0x2000, "t")])
    no_offset = match()
    unknown_target = match(target_offset=0x9999)
    unknown_source = match(source_offset=0x8888, target_offset=0x2000)
    good = match(target_offset=0x2000)
    paired, unresolved = pair_matches(
        [no_offset, unknown_target, unknown_source, good], sources, targets, {1}
    )
    assert unresolved == [no_offset, unknown_target, unknown_source]
    assert [pair.target_entity_id for pair in paired] == [2]


def test_unscheduled_sources_are_dropped_silently():
    sources = index_entities([Entity(1, 0x1000, "s")])
    targets = index_entities([Entity(2, 0x2000, "t")])
    paired, unresolved = pair_matches([match(target_offset=0x2000)], sources, targets, set())
    assert paired == [] and unresolved == []


def test_a_pichash_pair_is_always_fully_similar():
    sources = index_entities([Entity(1, 0x1000, "s")])
    targets = index_entities([Entity(2, 0x2000, "t")])
    (pair,), _ = pair_matches(
        [match(target_offset=0x2000, flags=2, score=1.0, pichash_candidates=1)],
        sources,
        targets,
        {1},
    )
    assert pair.similarity == 255
    assert pair.confidence == 255


@given(st.text(max_size=12))
@PROPERTY
def test_only_the_exporter_architectures_are_accepted(name):
    if name in SMDA_ARCHITECTURES:
        assert smda_architecture(name) in {"intel", "aarch64"}
    else:
        with pytest.raises(UnsupportedArchitectureError, match="Unsupported architecture"):
            smda_architecture(name)


class FakeSettings:
    def __init__(self, typed=""):
        self.value = typed
        self.resets = 0

    def contains(self, key):
        return True

    def get_string(self, key):
        return self.value

    def reset(self, key):
        self.resets += 1
        self.value = ""


class FakeKeychain:
    def __init__(self, data=None, store_ok=True):
        self.data = dict(data or {})
        self.store_ok = store_ok
        self.deletes = 0

    def store_data(self, key, value):
        if not self.store_ok:
            return False
        self.data[key] = value
        return True

    def has_data(self, key):
        return key in self.data

    def get_data(self, key):
        return self.data[key]

    def delete_data(self, key):
        self.deletes += 1
        return self.data.pop(key, None) is not None


def secrets(keychain):
    return type("Secrets", (), {"get": staticmethod(lambda name: keychain)})


@given(st.text(max_size=16), st.booleans(), st.booleans())
@PROPERTY
def test_migration_never_deletes_a_stored_token(typed, has_stored, store_ok):
    keychain = FakeKeychain({"mcrit.api_token": "stored"} if has_stored else {}, store_ok)
    token = _migrate_token(FakeSettings(typed), secrets(keychain))
    assert keychain.deletes == 0
    if typed:
        assert token == typed
    elif has_stored:
        assert token == "stored"
        assert keychain.data["mcrit.api_token"] == "stored"
    else:
        assert token == ""


def test_a_keychain_that_refuses_to_store_keeps_the_typed_value():
    settings_obj = FakeSettings("tok")
    keychain = FakeKeychain(store_ok=False)
    assert _migrate_token(settings_obj, secrets(keychain)) == "tok"
    assert settings_obj.value == "tok"
    assert settings_obj.resets == 0


def test_an_empty_field_never_clears_the_keychain():
    keychain = FakeKeychain({"mcrit.api_token": "stored"})
    for _ in range(3):
        assert _migrate_token(FakeSettings(""), secrets(keychain)) == "stored"
    assert keychain.data == {"mcrit.api_token": "stored"}


def test_the_keychain_provider_name_is_the_system_one():
    assert KEYCHAIN_PROVIDER == "SystemSecretsProvider"
