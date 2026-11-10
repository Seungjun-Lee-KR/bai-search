import io
import os
import time
import unicodedata
import zipfile
from datetime import date

from bai_search.collect import (expand_zip, is_ready, load_manifest, meta_from_filename,
                                override_meta, parse_date, resolve_meta)


def test_parse_date_variants():
    for s in ["2026-05-12", "2026.5.12", "2026. 5. 12.", "20260512", "2026년 5월 12일"]:
        assert parse_date(s) == date(2026, 5, 12), s
    assert parse_date("2026-13-40") is None and parse_date("없음") is None


def test_meta_from_filename():
    m = meta_from_filename("2026-05-12_법원 회계 관리실태_처분요구서.pdf")
    assert m.published_on == date(2026, 5, 12)
    assert m.audit_name == "법원 회계 관리실태 처분요구서"
    assert meta_from_filename("20250103 심사결정.hwp").source == "review_decision"
    assert meta_from_filename("3971a2cb-_________-7_1.pdf").audit_name is None


def test_manifest_utf8_and_cp949_and_nfd(tmp_path):
    (tmp_path / "a.csv").write_text("파일명,공개일,감사명,주소\n가.pdf,2026.05.12,회계 감사,https://x\n",
                                    encoding="utf-8-sig")
    (tmp_path / "b.csv").write_bytes("파일,게시일,제목,구분\n나,20250103,심사 건,심사결정\n".encode("cp949"))
    m = load_manifest(tmp_path)
    assert m["가.pdf"].published_on == date(2026, 5, 12) and m["가.pdf"].source_url == "https://x"
    assert m["나"].source == "review_decision"
    nfd = tmp_path / unicodedata.normalize("NFD", "가.pdf")   # macOS에서 내려받은 한글 파일명
    nfd.write_text("x")
    assert resolve_meta(nfd, m).audit_name == "회계 감사"


def test_manifest_overrides_filename_but_keeps_its_date(tmp_path):
    (tmp_path / "list.csv").write_text("파일명,감사명\n2026-01-02_x.pdf,감사A\n", encoding="utf-8")
    meta = resolve_meta(tmp_path / "2026-01-02_x.pdf", load_manifest(tmp_path))
    assert (meta.audit_name, meta.published_on) == ("감사A", date(2026, 1, 2))


def test_expand_zip_cp949_names_and_safety(tmp_path):
    # 한국어 윈도우 압축: 파일명이 UTF-8 표시 없이 cp949 바이트로 저장됨.
    # 파이썬은 한글 이름을 UTF-8로 쓰므로, 같은 길이의 ASCII 이름으로 만든 뒤 바이트를 바꿔 넣음.
    cp949 = "../../처분요구서.pdf".encode("cp949")
    placeholder = b"../../" + b"A" * (len(cp949) - 10) + b".pdf"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(placeholder.decode(), "pdf")
        zf.writestr("readme.exe", "x")
        zf.writestr("sub/결정.hwp", "hwp")
    z = tmp_path / "묶음.zip"
    z.write_bytes(buf.getvalue().replace(placeholder, cp949))
    out = expand_zip(z, tmp_path)
    assert sorted(p.name for p in out) == ["묶음__결정.hwp", "묶음__처분요구서.pdf"]
    assert all(p.parent == tmp_path for p in out)


def test_is_ready_skips_downloads_in_progress(tmp_path):
    part = tmp_path / "a.pdf.crdownload"
    part.write_text("x")
    fresh = tmp_path / "b.pdf"
    fresh.write_text("x")
    old = tmp_path / "c.pdf"
    old.write_text("x")
    t = time.time() - 60
    os.utime(old, (t, t))
    assert not is_ready(part) and not is_ready(fresh) and is_ready(old)


def test_override_meta():
    base = meta_from_filename("2026-01-02_감사명입니다.pdf")
    m = override_meta(base, date(2025, 1, 1), None, "u")
    assert (m.published_on, m.audit_name, m.source_url) == (date(2025, 1, 1), "감사명입니다", "u")
