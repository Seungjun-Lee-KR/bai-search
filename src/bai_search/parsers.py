"""PDF / HWP(5.x) / HWPX 문서에서 본문 텍스트를 뽑아냅니다."""
from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET


class ParseError(Exception):
    pass


def extract_text(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf_text(path)
    if suffix == ".hwp":
        return hwp_text(path.read_bytes())
    if suffix == ".hwpx":
        return hwpx_text(path.read_bytes())
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    raise ParseError(f"지원하지 않는 형식: {path.name}")


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- PDF
def _pdf_text(path: Path) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    text = "\n\n".join(pages)
    if not text.strip():
        raise ParseError(f"{path.name}: 텍스트 층이 없습니다(스캔본). OCR이 필요합니다.")
    return normalize(text)


# ---------------------------------------------------------------- HWP 5.x
HWPTAG_PARA_TEXT = 16 + 51  # HWPTAG_BEGIN + 51

# 1 WCHAR 크기의 문자 제어코드. 나머지 0~31 코드는 8 WCHAR(16바이트)를 차지합니다.
_CHAR_CONTROLS = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}


def hwp_text(data: bytes) -> str:
    import olefile
    import io

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except OSError as e:
        raise ParseError(f"HWP 파일을 열 수 없습니다: {e}") from e

    with ole:
        header = ole.openstream("FileHeader").read()
        if header[:17] != b"HWP Document File":
            raise ParseError("HWP 5.x 문서가 아닙니다.")
        flags = struct.unpack_from("<I", header, 36)[0]
        compressed = bool(flags & 0x01)
        if flags & 0x04:
            raise ParseError("배포용(암호화) 문서는 지원하지 않습니다. PDF로 받아 주세요.")

        sections = sorted(
            (s for s in ole.listdir() if len(s) == 2 and s[0] == "BodyText"),
            key=lambda s: int(s[1].replace("Section", "") or 0),
        )
        parts = []
        for s in sections:
            raw = ole.openstream("/".join(s)).read()
            if compressed:
                raw = zlib.decompress(raw, -15)
            parts.append(hwp_section_text(raw))
    return normalize("\n".join(parts))


def hwp_section_text(raw: bytes) -> str:
    """BodyText/SectionN 레코드 스트림에서 문단 텍스트만 꺼냅니다."""
    out: list[str] = []
    pos = 0
    n = len(raw)
    while pos + 4 <= n:
        (head,) = struct.unpack_from("<I", raw, pos)
        pos += 4
        tag = head & 0x3FF
        size = (head >> 20) & 0xFFF
        if size == 0xFFF:
            (size,) = struct.unpack_from("<I", raw, pos)
            pos += 4
        payload = raw[pos:pos + size]
        pos += size
        if tag == HWPTAG_PARA_TEXT:
            out.append(_decode_para_text(payload))
    return "\n".join(out)


def _decode_para_text(payload: bytes) -> str:
    chars: list[str] = []
    i = 0
    n = len(payload) - 1
    while i < n:
        code = payload[i] | (payload[i + 1] << 8)
        if code >= 32:
            chars.append(chr(code))
            i += 2
        elif code in _CHAR_CONTROLS:
            if code in (10, 13):
                chars.append("\n")
            elif code == 30 or code == 31:
                chars.append(" ")
            i += 2
        else:
            if code == 9:
                chars.append("\t")
            i += 16
    return "".join(chars).rstrip("\n")


# ---------------------------------------------------------------- HWPX
def hwpx_text(data: bytes) -> str:
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ParseError("HWPX(zip) 형식이 아닙니다.") from e

    def section_no(name: str) -> int:
        m = re.search(r"section(\d+)\.xml$", name)
        return int(m.group(1)) if m else 0

    names = sorted(
        (n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
        key=section_no,
    )
    paras: list[str] = []
    for name in names:
        root = ET.fromstring(zf.read(name))
        for p in root.iter():
            if _local(p.tag) != "p":
                continue
            # 표 안 문단은 중첩된 p로 따로 나오므로 직계 run의 t만 모읍니다.
            texts = []
            for run in p:
                if _local(run.tag) != "run":
                    continue
                for t in run:
                    if _local(t.tag) == "t":
                        texts.append("".join(t.itertext()))
            if texts:
                paras.append("".join(texts))
    return normalize("\n".join(paras))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
