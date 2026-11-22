from law_agent import scope


def test_in_scope_rcodes(monkeypatch):
    monkeypatch.setattr(scope, "scope", lambda: {"r_codes": ["R-10"]})
    assert scope.in_scope_rcodes(["R-1", "R-3-2", "R-10", "R-10-1"]) == ["R-10", "R-10-1"]
    assert scope.in_scope_rcodes(["R-1", "R-3-2"]) == ["R-1", "R-3-2"]      # 범위 안 것이 없으면 막지 않음
    monkeypatch.setattr(scope, "scope", lambda: {})
    assert scope.in_scope_rcodes(["R-1"]) == ["R-1"]
