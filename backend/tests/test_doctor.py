import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("doctor", Path(__file__).resolve().parent.parent / "scripts" / "doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


def test_parse_ffmpeg_version_variants():
    assert doctor.parse_ffmpeg_version("ffmpeg version 6.1.1-3ubuntu5 Copyright") == (6, 1)
    assert doctor.parse_ffmpeg_version("ffmpeg version n5.1.4 built with gcc") == (5, 1)
    assert doctor.parse_ffmpeg_version("ffmpeg version 4.2.7") == (4, 2)
    assert doctor.parse_ffmpeg_version("") is None and doctor.parse_ffmpeg_version("garbage") is None


def test_parse_env_ignores_comments_and_quotes():
    env = doctor.parse_env("# c\nAPP_SECRET='abc'  # inline\nDEBUG=false\n\nBAD LINE\nX=1=2\n")
    assert env == {"APP_SECRET": "abc", "DEBUG": "false", "X": "1=2"}


def test_check_reports_levels_and_flags_missing_secret():
    res = doctor.check({}, net=False)
    assert res and all(l in (doctor.OK, doctor.WARN, doctor.FAIL) for l, _, _ in res)
    sec = [r for r in res if r[1] == "APP_SECRET"][0]
    assert sec[0] == doctor.WARN and "secrets.token_urlsafe" in sec[2]
    assert [r for r in doctor.check({"APP_SECRET": "x" * 40})[:0]] == []                   # không lỗi khi truyền secret dài
    assert [r for r in doctor.check({"APP_SECRET": "x" * 40}) if r[1] == "APP_SECRET"][0][0] == doctor.OK
