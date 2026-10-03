from app.core import updates


def test_newer():
    assert updates.is_newer("1.0.1", "1.0.0")
    assert not updates.is_newer("1.0.0", "1.0.0")
    assert not updates.is_newer("junk", "1.0.0")


def test_manifest_must_point_at_this_repository():
    good = {"version": "1.2.0", "installer": {
        "name": "PLCMonitor-Setup-1.2.0.exe",
        "url": f"{updates.DOWNLOAD_PREFIX}v1.2.0/PLCMonitor-Setup-1.2.0.exe",
        "size": 10, "sha256": "a" * 64}}
    assert updates.parse(good).version == "1.2.0"
    bad = dict(good, installer=dict(good["installer"], url="https://example.com/PLCMonitor-Setup-1.2.0.exe"))
    try:
        updates.parse(bad)
    except updates.UpdateError:
        pass
    else:
        raise AssertionError("accepted a foreign URL")
