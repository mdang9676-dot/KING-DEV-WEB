"""Test dựng video bằng FFmpeg THẬT: đủ 4 kiểu che, nâng nét, phụ đề mới, logo, trộn tiếng. Bỏ qua nếu máy không có ffmpeg."""
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.services import ffmpeg_service as ff
from app.services import filter_builder as fb
from app.services.subtitle_service import Segment, to_ass_styled

HAS_FFMPEG = bool(shutil.which("ffmpeg"))


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


def make_assets(d: Path):
    sh(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=2", "-f", "lavfi", "-i",
        "sine=frequency=220:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "src.mp4")])
    sh(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0xFF3366:s=200x100,format=rgba", "-frames:v", "1", str(d / "logo.png")])
    sh(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=660:duration=2", "-ar", "24000", "-ac", "1", str(d / "dub.wav")])


async def render(d: Path, spec: fb.RenderSpec):
    plan = fb.build_render_plan(spec, str(d / "src.mp4"), str(d / "out.mp4"))
    seen = []

    async def prog(f):
        seen.append(f)

    async def nocancel():
        return False

    await ff.run_with_progress(plan.cmd, 2.0, prog, nocancel)
    return plan, await ff.probe(d / "out.mp4"), seen


def test_rect_to_px_clamps_and_is_even():
    x, y, w, h = fb.rect_to_px(fb.Rect(0.99, 0.99, 0.5, 0.5), 640, 360)
    assert x + w <= 640 and y + h <= 360 and all(v % 2 == 0 for v in (x, y, w, h))
    assert fb.rect_to_px(fb.Rect(0.5, 0.5, 0.001, 0.001), 640, 360)[2:] == (16, 16)     # tối thiểu 16px


def test_output_size_only_upscales():
    assert fb.output_size(640, 360, fb.EnhanceSpec(target_height=720)) == (1280, 720)
    assert fb.output_size(1920, 1080, fb.EnhanceSpec(target_height=720)) == (1920, 1080)   # không thu nhỏ
    assert fb.output_size(641, 361, None) == (640, 360)                                    # luôn chẵn


def test_invalid_inputs_are_rejected():
    for bad in ({"mode": "solid", "color": "red;rm -rf"}, {"mode": "khong-co"}):
        spec = fb.RenderSpec(640, 360, True, covers=[(fb.Rect(.1, .1, .2, .2), fb.CoverSpec(**bad))])
        try:
            fb.build_render_plan(spec, "a.mp4", "b.mp4")
            assert False, bad
        except fb.RenderSpecError:
            pass


def test_no_filters_means_stream_copy():
    plan = fb.build_render_plan(fb.RenderSpec(640, 360, True), "a.mp4", "b.mp4")
    assert not plan.reencodes_video and "copy" in plan.cmd


def test_all_cover_modes_and_full_pipeline_render():
    if not HAS_FFMPEG:
        return
    d = Path(tempfile.mkdtemp())
    make_assets(d)
    for mode in fb.COVER_MODES:                                   # blur / mosaic / erase / solid, cả vùng sát mép khung
        for rect in (fb.Rect(.1, .7, .8, .2), fb.Rect(0, 0, .3, .2), fb.Rect(.9, .9, .1, .1)):
            spec = fb.RenderSpec(640, 360, True, covers=[(rect, fb.CoverSpec(mode, 70))])
            _, meta, _ = asyncio.run(render(d, spec))
            assert (meta.width, meta.height) == (640, 360), (mode, rect)

    (d / "new.ass").write_text(to_ass_styled([Segment(0, 0, 2000, "PHỤ ĐỀ MỚI")], 1280, 720, font_px=44, region=(120, 590, 1040, 90)), encoding="utf-8")
    spec = fb.RenderSpec(
        640, 360, True, covers=[(fb.Rect(.08, .78, .84, .16), fb.CoverSpec("blur", 80)), (fb.Rect(.8, .03, .17, .12), fb.CoverSpec("erase"))],
        enhance=fb.EnhanceSpec(720, 0.6, True), ass_path=str(d / "new.ass"),
        logo=fb.LogoSpec(str(d / "logo.png"), 200, 100, 0.03, 0.04, 0.16, 0.85),
        dub_audio_path=str(d / "dub.wav"), original_volume=0.2)
    plan, meta, seen = asyncio.run(render(d, spec))
    assert (meta.width, meta.height, meta.has_audio) == (1280, 720, True)
    assert seen and seen[-1] == 1.0
