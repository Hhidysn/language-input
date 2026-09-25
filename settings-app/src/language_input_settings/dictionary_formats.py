"""Bounded readers for locally owned pinyin dictionaries.

The SCEL, SGPU and Microsoft UDL layouts follow the format descriptions in
https://github.com/nopdan/rose (GPL-3.0). No source words leave this machine.
"""

from __future__ import annotations

import struct
from pathlib import Path

from .dictionary_syllables import MSPY_SYLLABLES, SOGOU_SYLLABLES


class FormatError(ValueError):
    pass


def _number(data: bytes, offset: int, size: int = 4) -> int:
    if offset < 0 or offset + size > len(data):
        raise FormatError("词库文件已截断。")
    return int.from_bytes(data[offset : offset + size], "little")


def _text(data: bytes, offset: int, size: int) -> str:
    if size < 0 or size % 2 or offset < 0 or offset + size > len(data):
        raise FormatError("词库中的文本长度无效。")
    try:
        return data[offset : offset + size].decode("utf-16-le").rstrip("\x00")
    except UnicodeError as exc:
        raise FormatError("词库包含无效的 UTF-16 文本。") from exc


def read_scel(data: bytes) -> list[tuple[str, tuple[str, ...], int]]:
    """Read a Sogou SCEL cell dictionary, retaining its original readings."""
    if len(data) < 0x1544:
        raise FormatError("不是完整的搜狗 .scel 细胞词库。")
    entry_offset = _number(data, 0)
    groups = _number(data, 0x120)
    words = _number(data, 0x124)
    if not 0x1540 <= entry_offset < len(data) or groups > 1_000_000 or words > 2_000_000:
        raise FormatError("搜狗 .scel 文件头不受支持或已损坏。")
    pos = entry_offset
    count = _number(data, pos)
    pos += 4
    if count > 1024:
        raise FormatError("搜狗 .scel 拼音表过大。")
    syllables: dict[int, str] = {}
    for _ in range(count):
        index, size = _number(data, pos, 2), _number(data, pos + 2, 2)
        pos += 4
        syllables[index] = _text(data, pos, size).lower().replace("ü", "v")
        pos += size
    if not count:
        syllables = dict(enumerate(SOGOU_SYLLABLES))
    result: list[tuple[str, tuple[str, ...], int]] = []
    for _ in range(groups):
        repeat, py_bytes = _number(data, pos, 2), _number(data, pos + 2, 2)
        pos += 4
        if py_bytes % 2 or py_bytes > 128 or repeat > 100_000:
            raise FormatError("搜狗 .scel 词条结构无效。")
        indexes = [_number(data, pos + n, 2) for n in range(0, py_bytes, 2)]
        pos += py_bytes
        code = tuple(syllables.get(index, "") for index in indexes)
        for _ in range(repeat):
            size = _number(data, pos, 2)
            pos += 2
            word = _text(data, pos, size)
            pos += size
            ext_size = _number(data, pos, 2)
            pos += 2
            if ext_size < 4 or pos + ext_size > len(data):
                raise FormatError("搜狗 .scel 词频数据无效。")
            frequency = _number(data, pos)
            pos += ext_size
            result.append((word, code, frequency))
    if len(result) != words:
        raise FormatError("搜狗 .scel 词条数量与文件头不匹配。")
    # Some SCEL files carry a separate pinyin-phrase section. It has no
    # frequency, but those phrases should remain available after import.
    phrase_count = _number(data, 0x5C)
    phrase_pos = _number(data, 0x60)
    phrase_size = _number(data, 0x64)
    if phrase_count:
        if phrase_count > 1_000_000 or phrase_pos < pos or phrase_pos + phrase_size > len(data):
            raise FormatError("搜狗 .scel 短语区无效。")
        pos = phrase_pos
        for _ in range(phrase_count):
            if pos + 19 > len(data):
                raise FormatError("搜狗 .scel 短语区已截断。")
            pinyin_phrase = data[pos + 2] == 1
            pos += 17
            code_size = _number(data, pos, 2)
            pos += 2
            if code_size > 128 or code_size % 2:
                raise FormatError("搜狗 .scel 短语拼音无效。")
            indexes = [_number(data, pos + n, 2) for n in range(0, code_size, 2)] if pinyin_phrase else []
            pos += code_size
            size = _number(data, pos, 2)
            pos += 2
            word = _text(data, pos, size)
            pos += size
            if pinyin_phrase:
                result.append((word, tuple(syllables.get(index, "") for index in indexes), 0))
    return result


def read_sogou_user(data: bytes) -> list[tuple[str, tuple[str, ...], int]]:
    """Read the SGPU v3 personal dictionary used by current Sogou Pinyin."""
    if data[:4] != b"SGPU" or len(data) < 96:
        raise FormatError("仅支持 SGPU 格式的搜狗本机用户词库。")
    size = _number(data, 16)
    index_offset = _number(data, 56)
    index_size = _number(data, 60)
    count = _number(data, 64)
    dictionary_offset = _number(data, 68)
    dictionary_size = _number(data, 72)
    if (size != len(data) or count > 1_000_000 or count * 4 > index_size
            or index_offset + index_size != dictionary_offset
            or dictionary_offset + dictionary_size > len(data)):
        raise FormatError("搜狗用户词库索引已损坏或格式不同。")
    result: list[tuple[str, tuple[str, ...], int]] = []
    for i in range(count):
        pos = dictionary_offset + _number(data, index_offset + 4 * i)
        if pos + 13 > len(data):
            raise FormatError("搜狗用户词库词条越界。")
        frequency = struct.unpack_from("<h", data, pos)[0]
        py_bytes = _number(data, pos + 9, 2)
        if py_bytes > 128 or py_bytes % 2:
            raise FormatError("搜狗用户词库拼音长度无效。")
        py_pos = pos + 11
        indexes = [_number(data, py_pos + n, 2) for n in range(0, py_bytes, 2)]
        word_size = _number(data, py_pos + py_bytes + 2, 2)
        word = _text(data, py_pos + py_bytes + 4, word_size)
        result.append((word, tuple(SOGOU_SYLLABLES[index] if index < len(SOGOU_SYLLABLES) else "" for index in indexes), max(frequency, 0)))
    return result


def read_microsoft_udl(data: bytes) -> list[tuple[str, tuple[str, ...], int]]:
    """Read the fixed-record ChsPinyinUDL.dat user dictionary."""
    if data[:4] != b"\x55\xaa\x88\x81" or len(data) < 0x2400:
        raise FormatError("不是受支持的微软拼音用户词库。")
    count = _number(data, 12)
    if count > 1_000_000 or 0x2400 + count * 60 > len(data):
        raise FormatError("微软拼音用户词库词条数量无效。")
    result: list[tuple[str, tuple[str, ...], int]] = []
    for i in range(count):
        pos = 0x2400 + i * 60
        length = data[pos + 10]
        if not 0 < length <= 12:
            continue
        word = _text(data, pos + 12, length * 2)
        code_pos = pos + 12 + length * 2
        indexes = [_number(data, code_pos + 2 * j, 2) for j in range(length)]
        code = tuple(MSPY_SYLLABLES[index] if index < len(MSPY_SYLLABLES) else "" for index in indexes)
        result.append((word, code, 1))
    return result


def read_binary(path: Path) -> tuple[str, list[tuple[str, tuple[str, ...], int]]]:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in {".scel", ".qcel"}:
        return "SCEL", read_scel(data)
    if suffix == ".bin":
        return "搜狗 SGPU", read_sogou_user(data)
    if suffix == ".dat":
        return "微软拼音 UDL", read_microsoft_udl(data)
    raise FormatError("不支持的二进制词库格式。")
