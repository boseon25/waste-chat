

def test_purge_dry(tmp_path):
    from law_agent.store.cache import purge_dry
    d = tmp_path / "cache" / "ab"; d.mkdir(parents=True)
    (d / "a.json").write_text('{"value": {"reason": "dry run"}}', encoding="utf-8")
    (d / "b.json").write_text('{"value": {"reason": "real"}}', encoding="utf-8")
    assert purge_dry(tmp_path) == 1 and (d / "b.json").exists()
