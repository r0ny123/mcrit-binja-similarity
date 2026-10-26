from mcrit_similarity.settings import _migrate_token

KEY = "mcrit_similarity.api_token"


class FakeSettings:
    """contains() mirrors Binary Ninja: it reports registration, not whether a value is set."""

    def __init__(self, **values):
        self.values = values

    def contains(self, key):
        return True

    def get_string(self, key):
        return str(self.values.get(key, ""))

    def reset(self, key):
        self.values[key] = ""


class FakeKeychain:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def store_data(self, key, value):
        self.data[key] = value
        return True

    def has_data(self, key):
        return key in self.data

    def get_data(self, key):
        return self.data[key]


def secrets(keychain):
    return type("Secrets", (), {"get": staticmethod(lambda name: keychain)})


def test_typed_token_moves_to_keychain_and_survives_later_reads():
    keychain = FakeKeychain()
    settings = FakeSettings(**{KEY: "tok-123"})
    assert _migrate_token(settings, secrets(keychain)) == "tok-123"
    assert settings.values[KEY] == ""
    for _ in range(3):
        assert _migrate_token(settings, secrets(keychain)) == "tok-123"
    assert keychain.data == {KEY: "tok-123"}


def test_new_token_replaces_stored_one():
    keychain = FakeKeychain({KEY: "old"})
    assert _migrate_token(FakeSettings(**{KEY: "new"}), secrets(keychain)) == "new"
    assert keychain.data == {KEY: "new"}


def test_token_stays_in_settings_without_a_keychain():
    settings = FakeSettings(**{KEY: "tok"})
    assert _migrate_token(settings, secrets(None)) == "tok"
    assert settings.values[KEY] == "tok"
