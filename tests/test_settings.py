from mcrit_similarity.settings import _migrate_token

KEY = "mcrit.api_token"


class FakeSettings:
    """contains() mirrors Binary Ninja: it reports registration, not whether a value is set."""

    def __init__(self, typed=""):
        self.value = typed

    def contains(self, key):
        return True

    def get_string(self, key):
        return self.value

    def reset(self, key):
        self.value = ""


class FakeKeychain:
    def __init__(self):
        self.data = {}

    def store_data(self, key, value):
        self.data[key] = value
        return True

    def has_data(self, key):
        return key in self.data

    def get_data(self, key):
        return self.data[key]

    def delete_data(self, key):
        return self.data.pop(key, None) is not None


def secrets(keychain):
    return type("Secrets", (), {"get": staticmethod(lambda name: keychain)})


def test_typed_token_moves_to_keychain_and_survives_later_reads():
    keychain = FakeKeychain()
    settings = FakeSettings("tok-123")
    assert _migrate_token(settings, secrets(keychain)) == "tok-123"
    assert settings.value == ""
    for _ in range(3):
        assert _migrate_token(settings, secrets(keychain)) == "tok-123"
    assert keychain.data == {KEY: "tok-123"}


def test_new_token_replaces_stored_one():
    keychain = FakeKeychain()
    keychain.data[KEY] = "old"
    assert _migrate_token(FakeSettings("new"), secrets(keychain)) == "new"
    assert keychain.data[KEY] == "new"


def test_token_stays_in_settings_without_a_keychain():
    settings = FakeSettings("tok")
    assert _migrate_token(settings, secrets(None)) == "tok"
    assert settings.value == "tok"
