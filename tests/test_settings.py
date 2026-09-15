from ipodmanager.settings import Settings


def test_settings_defaults_to_aac_320_off():
    s = Settings()
    assert s.convert_to_aac_320 is False


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    s = Settings(convert_to_aac_320=True)
    s.save()

    loaded = Settings.load()
    assert loaded.convert_to_aac_320 is True


def test_settings_load_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    loaded = Settings.load()
    assert loaded == Settings()
