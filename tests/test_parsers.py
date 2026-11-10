import io
import struct
import zipfile

from bai_search.parsers import HWPTAG_PARA_TEXT, hwp_section_text, hwpx_text, normalize


def _record(tag: int, payload: bytes) -> bytes:
    return struct.pack("<I", tag | (len(payload) << 20)) + payload


def test_hwp_para_text_with_controls():
    # 일반 글자 + 탭(8 WCHAR 인라인 제어) + 문단 끝(13)
    tab = struct.pack("<H", 9) + b"\x00" * 14
    payload = "수의계약".encode("utf-16-le") + tab + "분할".encode("utf-16-le") + struct.pack("<H", 13)
    raw = _record(66, b"\x00" * 8) + _record(HWPTAG_PARA_TEXT, payload)
    assert hwp_section_text(raw) == "수의계약\t분할"


def test_hwp_extended_size_record():
    text = "가" * 3000
    payload = text.encode("utf-16-le")
    head = struct.pack("<I", HWPTAG_PARA_TEXT | (0xFFF << 20)) + struct.pack("<I", len(payload))
    assert hwp_section_text(head + payload) == text


def test_hwpx_text():
    xml = (
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        "<hp:p><hp:run><hp:t>제 목 업무추진비 </hp:t></hp:run><hp:run><hp:t>사적 사용</hp:t></hp:run></hp:p>"
        "<hp:p><hp:run><hp:t>둘째 문단</hp:t></hp:run></hp:p></hs:sec>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Contents/section0.xml", xml)
    assert hwpx_text(buf.getvalue()) == "제 목 업무추진비 사적 사용\n둘째 문단"


def test_normalize():
    assert normalize("a  b\r\n\n\n\nc") == "a b\n\nc"
