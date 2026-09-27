import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.services import ai_providers as ai
from app.services import filter_builder as fb
from app.services import fonts
from app.services.glossary import glossary_prompt, parse_glossary


def test_parse_glossary_formats_and_limits():
    txt = "# chú thích\n林动 = Lâm Động\n萧炎 -> Tiêu Viêm\nZhang San：Trương Tam\n林动 = trùng\nhỏng dòng này\n = thiếu\nA = \n" + "x" * 70 + " = dài"
    assert parse_glossary(txt) == [("林动", "Lâm Động"), ("萧炎", "Tiêu Viêm"), ("Zhang San", "Trương Tam")]
    assert parse_glossary(None) == [] and parse_glossary("") == []
    assert len(parse_glossary("\n".join(f"k{i} = v{i}" for i in range(500)))) == 100


def test_glossary_prompt_goes_into_llm_system_prompt():
    seen = {}

    def fake(ai_, system, user):
        seen["system"] = system
        return json.dumps([f"T:{t}" for t in json.loads(user)])

    orig = ai._complete_sync
    ai._complete_sync = fake
    try:
        tr = ai.LLMTranslator(ai.UserAI("openai", "m", "k"))
        out = asyncio.run(tr.translate_all(["林动来了"], "vi", "zh", glossary=parse_glossary("林动 = Lâm Động")))
    finally:
        ai._complete_sync = orig
    assert out == ["T:林动来了"]
    assert "林动 => Lâm Động" in seen["system"] and "MUST" in seen["system"]
    assert glossary_prompt([]) == ""


def _find_ttf():
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if Path(p).exists():
            return Path(p)
    hits = list(Path("/usr/share/fonts").rglob("*.ttf")) if Path("/usr/share/fonts").exists() else []
    return hits[0] if hits else None


def test_read_font_family_and_custom_listing():
    ttf = _find_ttf()
    if not ttf:
        return
    fam = fonts.read_font_family(ttf)
    assert fam and len(fam) < 60
    d = Path(tempfile.mkdtemp())
    shutil.copy(ttf, d / "x.ttf")
    (d / "not-a-font.ttf").write_bytes(b"garbage-not-a-font-file")
    assert fonts.custom_families(d) == [fam]                        # file hỏng bị bỏ qua, không lỗi
    assert fonts.read_font_family(d / "khong-co.ttf") is None
    listing = fonts.list_fonts(d)
    assert listing[0] == {"family": fam, "custom": True}


def test_ass_filter_gets_fontsdir_and_renders_with_real_ffmpeg():
    plan = fb.build_render_plan(fb.RenderSpec(640, 360, False, ass_path="/tmp/a.ass", fonts_dir="/tmp/my fonts"), "a.mp4", "b.mp4")
    assert "fontsdir='/tmp/my fonts'" in plan.filter_complex
    ttf = _find_ttf()
    if not (ttf and shutil.which("ffmpeg")):
        return
    d = Path(tempfile.mkdtemp()); (d / "fonts").mkdir(); shutil.copy(ttf, d / "fonts" / "f.ttf")
    from app.services.subtitle_service import Segment, to_ass_styled
    fam = fonts.read_font_family(ttf)
    (d / "s.ass").write_text(to_ass_styled([Segment(0, 0, 1000, "Xin chào Việt Nam")], 640, 360, font=fam, font_px=30), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1", str(d / "in.mp4")], check=True)
    spec = fb.RenderSpec(640, 360, False, ass_path=str(d / "s.ass"), fonts_dir=str(d / "fonts"))
    p = fb.build_render_plan(spec, str(d / "in.mp4"), str(d / "out.mp4"))
    r = subprocess.run(p.cmd, capture_output=True, text=True)
    assert r.returncode == 0 and (d / "out.mp4").exists(), r.stderr[-400:]
