"""Auto-update version comparison."""
from irtracker import updater


def test_is_newer():
    assert updater.is_newer("v0.1.12", "v0.1.2")     # numeric, not lexical
    assert updater.is_newer("v0.2.0", "v0.1.99")
    assert updater.is_newer("1.0.0", "v0.9.9")
    assert not updater.is_newer("v0.1.2", "v0.1.12")
    assert not updater.is_newer("v0.1.0", "v0.1.0")
    assert not updater.is_newer("garbage", "v0.1.0")  # unparseable -> not newer
    assert not updater.is_newer("v0.2.0", "dev")


def test_current_version_is_a_string():
    v = updater.current_version()
    assert isinstance(v, str) and v


def test_dir_writable(tmp_path):
    assert updater._dir_writable(tmp_path) is True


def test_needs_admin_false_from_source():
    # running tests is never "frozen", so elevation is never required
    assert updater.needs_admin() is False
