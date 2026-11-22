from pathlib import Path

from law_agent.web import user

FIX = Path(__file__).parent / "fixtures" / "intake_with_code.docx"


def test_docx_codes_reads_virtual_code_rows():
    assert user.docx_codes(FIX) == {1: "51-03-01", 2: "04-02-00"}


def test_docx_codes_missing_or_bad_file(tmp_path):
    bad = tmp_path / "x.docx"
    bad.write_bytes(b"not a zip")
    assert user.docx_codes(bad) == {}


def test_record_docx_codes_calls_part1(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(user, "cases", lambda sid: [{"case_id": "A", "table_index": 1, "name": "PE"},
                                                    {"case_id": "B", "table_index": 3, "name": "기타"}])
    monkeypatch.setattr(user, "WORK", tmp_path)
    monkeypatch.setattr(user, "p1", lambda *a: calls.append(a) or (0, ""))
    assert user.record_docx_codes("S1", FIX) == ["PE 51-03-01"]
    assert calls[0][:2] == ("part1.py", "classification") and "51-03-01" in calls[0]
