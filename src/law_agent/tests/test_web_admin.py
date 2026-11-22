"""관리자 화면: 결과 파일 → HTML (판단 과정 5단계가 들어가는지)."""
import shutil
from pathlib import Path

from law_agent.web import admin

FIX = Path(__file__).parent / "fixtures" / "result_dry.json"


def test_pages(tmp_path):
    out = tmp_path / "outbox"
    out.mkdir()
    shutil.copy(FIX, out / "X_v0_result.json")
    admin.OUTBOX, admin.INBOX = out, tmp_path / "inbox"
    idx = admin.view_index().decode()
    assert "X_v0_result.json" in idx
    case = admin.view_case("X_v0_result.json").decode()
    for s in ("모델에게 준 사례 사실", "근거 법령 원문", "검증 후 결론", "법률 판단"):
        assert s in case
    assert "<script>alert" not in admin.view_case("../../etc/passwd").decode()
    assert "실시간 로그" in admin.view_log().decode()


def test_partners_by_role_separate_rankings():
    from law_agent.core.proposal.route_proposal import partners_by_role
    p = {"partner_candidates": [
        {"role": "transporter", "rank": 2, "name": "운송B"}, {"role": "preprocessor", "rank": 1, "name": "가공A"},
        {"role": "transporter", "rank": 1, "name": "운송A"}, {"role": "preprocessor", "rank": 2, "name": "가공B"}]}
    g = partners_by_role(p)
    assert [r for r, _k, _c in g] == ["preprocessor", "transporter"]          # 가공업체 먼저, 운송업체 따로
    assert [c["name"] for c in g[1][2]] == ["운송A", "운송B"]
    html = __import__("law_agent.web.admin", fromlist=["x"]).view_routes({"route_proposals": [
        {"title": "중간 가공", "checks": {"regulatory": "conditional"}, **p}]})
    assert "운송업체 (수집·운반업) 순위" in html and "중간 가공업체 (중간 재활용업) 순위" in html


def test_part1_section():
    import json
    from law_agent.core import part1_v2 as P
    inp = json.loads((Path(__file__).parent / "fixtures" / "part1_phase3_export.json").read_text(encoding="utf-8"))
    h = admin.view_part1(inp, P.convert(inp)["cases"][0])
    for s in ("① 입력서에 적힌 값", "성분표", "월별 발생량", "③ 분류번호 기록", "근거 있음 51-03-01", "⑤ 파트 2로 넘긴 값", "종이 라벨 조각 2%"):
        assert s in h
    assert "010-0000-0000" not in h                         # 담당자 연락처는 보여주지 않음
    assert "step49 이전" in admin.view_part1(None, None)


def test_user_form_parsing_and_home():
    from law_agent.web import user
    body = (b"--X\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\ndeclaration\r\n"
            b"--X\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a b.docx\"\r\n"
            b"Content-Type: application/octet-stream\r\n\r\nDATA\r\n--X--\r\n")
    f = user.parse_form({"Content-Type": "multipart/form-data; boundary=X"}, body)
    assert f["kind"] == "declaration" and f["file"]["filename"] == "a b.docx" and f["file"]["data"] == b"DATA"
    assert user.parse_form({"Content-Type": "application/x-www-form-urlencoded"}, "code=51-03-01".encode())["code"] == "51-03-01"
    assert user.safe_name("../../x y.docx") == "x_y.docx"
    assert "입력서 양식 받기" in user.view_home().decode()
    assert user.sid_time("20261118_161904") > 0
