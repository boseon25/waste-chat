"""윈도우·리눅스 공통 실행기 (launch.py) — 설정 읽기·가상환경 경로·환경변수."""
import os
from pathlib import Path

from law_agent import launch
from law_agent.bridge import watch


def test_read_env_file_forms(tmp_path):
    f = tmp_path / "env.sh"
    f.write_text(
        "# 주석\n"
        "export WASTE_LAW_DIR=\"${ROOT}/data/law/chunk_v2\"\n"
        "WASTE_DATA_DIR='$ROOT/data/tables/active'\n"
        "LAW_AGENT_DATA_DIR=~/x  # 줄끝 주석\n"
        "export NOW=$(date)\n"
        "if [ -f x ]; then echo; fi\n", encoding="utf-8")
    env = {"ROOT": "/r", "HOME": "/h"}
    names = launch.read_env_file(f, env)
    assert names == ["WASTE_LAW_DIR", "WASTE_DATA_DIR", "LAW_AGENT_DATA_DIR"]
    assert env["WASTE_LAW_DIR"] == "/r/data/law/chunk_v2"
    assert env["WASTE_DATA_DIR"] == "/r/data/tables/active"
    assert env["LAW_AGENT_DATA_DIR"] == str(Path.home()) + "/x"
    assert "NOW" not in env


def test_windows_path_value_kept(tmp_path):
    f = tmp_path / "waste.env"
    f.write_text("WASTE_VENV=C:\\Users\\me\\venv\nWASTE_LAW_DIR=${ROOT}\\data\\law\n", encoding="utf-8")
    env = {"ROOT": "C:\\w"}
    launch.read_env_file(f, env)
    assert env["WASTE_VENV"] == "C:\\Users\\me\\venv"
    assert env["WASTE_LAW_DIR"] == "C:\\w\\data\\law"


def test_venv_python_per_os(monkeypatch):
    monkeypatch.setattr(launch, "IS_WIN", True)
    assert launch.venv_python(Path("v")).as_posix() == "v/Scripts/python.exe"
    monkeypatch.setattr(launch, "IS_WIN", False)
    assert launch.venv_python(Path("v")).as_posix() == "v/bin/python"


def test_build_env_sets_utf8_and_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    monkeypatch.setenv("PYTHONPATH", "old")
    monkeypatch.delenv("PART1_DIR", raising=False)
    (tmp_path / "waste.env").write_text("WASTE_LAW_DIR=${ROOT}/law\n", encoding="utf-8")
    env, loaded = launch.build_env()
    assert loaded == ["waste.env"]
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONPATH"] == str(tmp_path / "src") + os.pathsep + "old"
    assert env["PART1_DIR"] == str(tmp_path / "src" / "part1")
    assert env["WASTE_LAW_DIR"] == f"{tmp_path}/law"


def test_key_file_overrides_and_not_printed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    k = tmp_path / launch.KEY_FILE
    k.parent.mkdir(parents=True)
    k.write_text("TIMELY_API_KEY=secret-value-123\n", encoding="utf-8")
    env, _ = launch.build_env()
    assert env["TIMELY_API_KEY"] == "secret-value-123"
    assert "secret-value-123" not in capsys.readouterr().out


def test_bat_files_ascii_crlf():
    for name in ("waste.bat", "waste-admin.bat"):
        data = (Path(launch.HERE) / "bin" / name).read_bytes()
        assert all(b < 128 for b in data), name
        assert b"\r\n" in data and b"launch.py" in data


def test_move_overwrites_existing(tmp_path):
    src = tmp_path / "a.json"; src.write_text("new")
    d = tmp_path / "done"; d.mkdir(); (d / "a.json").write_text("old")
    watch._move(src, d, "a.json")
    assert (d / "a.json").read_text() == "new" and not src.exists()
