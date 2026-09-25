"""Sync Sogou's active fuzzy-pinyin pairs to the Xiaohe Rime schema.

Rime derives spellings before Xiaohe's full-pinyin-to-double-pinyin xforms.
List insertion patches leave the installed schema and unrelated custom keys
untouched. Sogou's [Gray] suggestions are deliberately not treated as active.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

from . import paths, rime_settings, yaml_io


class FuzzyPinyinError(ValueError):
    pass


SCHEMA_ID = "language_input_flypy"

# Exact [Fuzzy] pair names used by Sogou; each pair is two-way. Keep each
# initial separate so unselected pairs do not become active accidentally.
PAIR_RULES: dict[tuple[str, str], tuple[str, str]] = {
    ("zh", "z"): ("derive/^zh/z/", "derive/^z([^h])/zh$1/"),
    ("ch", "c"): ("derive/^ch/c/", "derive/^c([^h])/ch$1/"),
    ("sh", "s"): ("derive/^sh/s/", "derive/^s([^h])/sh$1/"),
    ("n", "l"): ("derive/^n/l/", "derive/^l/n/"),
    ("ing", "in"): ("derive/ing$/in/", "derive/in$/ing/"),
    ("iang", "ian"): ("derive/iang$/ian/", "derive/ian$/iang/"),
    ("uang", "uan"): ("derive/uang$/uan/", "derive/uan$/uang/"),
    ("h", "f"): ("derive/^h/f/", "derive/^f/h/"),
    ("l", "r"): ("derive/^l/r/", "derive/^r/l/"),
    ("ang", "an"): ("derive/ang$/an/", "derive/an$/ang/"),
    ("eng", "en"): ("derive/eng$/en/", "derive/en$/eng/"),
}

_MANAGED_RULES = {rule for rules in PAIR_RULES.values() for rule in rules}
_INSERT_KEY = re.compile(r"^speller/algebra/@before \d+$")


def sogou_fuzzy_path(profile: Path | None = None) -> Path:
    home = Path(profile) if profile is not None else Path.home()
    return home / "AppData" / "LocalLow" / "SogouPY" / "Fuzzy.dat"


def read_sogou_pairs(source: str | Path | None = None) -> tuple[tuple[str, str], ...]:
    path = Path(source) if source is not None else sogou_fuzzy_path()
    if not path.is_file():
        raise FuzzyPinyinError("未找到搜狗本机模糊音设置 Fuzzy.dat。")
    if path.stat().st_size > 64 * 1024:
        raise FuzzyPinyinError("搜狗模糊音设置文件过大。")
    try:
        contents = path.read_text(encoding="utf-16")
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read_string(contents)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise FuzzyPinyinError(f"无法读取搜狗模糊音设置：{exc}") from exc
    if not parser.has_section("Fuzzy"):
        raise FuzzyPinyinError("搜狗设置中没有 [Fuzzy] 模糊音区。")
    selected: list[tuple[str, str]] = []
    unsupported: list[str] = []
    for key, value in parser.items("Fuzzy"):
        pair = (key.strip().lower(), value.strip().lower())
        if pair in PAIR_RULES:
            selected.append(pair)
        else:
            unsupported.append(f"{pair[0]}/{pair[1]}")
    if unsupported:
        raise FuzzyPinyinError("尚未支持的搜狗模糊音：" + "、".join(unsupported))
    return tuple(selected)


def _rules(pairs: tuple[tuple[str, str], ...]) -> list[str]:
    return [rule for pair in pairs for rule in PAIR_RULES[pair]]


def _custom_path(user_dir: Path) -> Path:
    return user_dir / f"{SCHEMA_ID}.custom.yaml"


def _managed_keys(patch: dict) -> list[str]:
    keys: list[str] = []
    nested = patch.get("speller")
    if isinstance(nested, dict) and "algebra" in nested:
        raise FuzzyPinyinError("小鹤方案已有其他拼写规则覆盖，请先检查自定义补丁。")
    for key, value in patch.items():
        if key == "speller/algebra" or (
            key.startswith("speller/algebra/") and not _INSERT_KEY.fullmatch(key)
        ):
            raise FuzzyPinyinError("小鹤方案已有其他拼写规则覆盖，请先检查自定义补丁。")
        if _INSERT_KEY.fullmatch(key):
            if value not in _MANAGED_RULES:
                raise FuzzyPinyinError(f"小鹤方案已有自定义拼写规则，未覆盖：{key}")
            keys.append(key)
    return keys


def preview_sogou_fuzzy(
    *, source: str | Path | None = None, user_dir: str | Path | None = None
) -> dict:
    pairs = read_sogou_pairs(source)
    user = Path(user_dir) if user_dir is not None else paths.rime_user_dir()
    compiled = rime_settings._load_yaml(user / "build" / f"{SCHEMA_ID}.schema.yaml") or {}
    algebra = (compiled.get("speller") or {}).get("algebra") or []
    rules = _rules(pairs)
    if rules:
        synced = algebra[:len(rules)] == rules
    else:
        custom = _custom_path(user)
        document = rime_settings._load_rt(custom) if custom.is_file() else {}
        patch = document.get("patch") or {}
        synced = not any(_INSERT_KEY.fullmatch(key) and value in _MANAGED_RULES for key, value in patch.items())
    return {"pairs": pairs, "rules": rules, "synced": synced}


def sync_sogou_fuzzy(
    *, source: str | Path | None = None,
    user_dir: str | Path | None = None,
    deploy: bool = True,
) -> dict:
    """Patch only managed fuzzy derivations and verify the compiled schema."""
    pairs = read_sogou_pairs(source)
    rules = _rules(pairs)
    user = Path(user_dir) if user_dir is not None else paths.rime_user_dir()
    custom = _custom_path(user)
    document = rime_settings._load_rt(custom) if custom.is_file() else rime_settings._require_map()()
    patch = document.get("patch")
    if patch is None:
        patch = rime_settings._require_map()()
        document["patch"] = patch
    if not isinstance(patch, dict):
        raise FuzzyPinyinError("小鹤方案补丁格式不正确。")
    previous = {key: patch[key] for key in _managed_keys(patch)}
    wanted = {}
    for index, rule in enumerate(rules):
        # Zero-padded paths sort in the same order as their numeric indices.
        # All derivations are inserted before the base erase/xform rules.
        wanted[f"speller/algebra/@before {index:02d}"] = rule
    changed = previous != wanted
    if changed:
        for key in previous:
            del patch[key]
        patch.update(wanted)
        user.mkdir(parents=True, exist_ok=True)
        yaml_io.backup_file(custom)
        rime_settings._dump_rt(custom, document)

    result = {
        "pairs": pairs,
        "rules": rules,
        "custom_path": str(custom),
        "changed": changed,
        "deploy": None,
        "clean": None,
    }
    if deploy:
        deployed = rime_settings.run_deploy()
        compiled = rime_settings._load_yaml(user / "build" / f"{SCHEMA_ID}.schema.yaml") or {}
        algebra = (compiled.get("speller") or {}).get("algebra") or []
        result["deploy"] = deployed
        result["clean"] = bool(deployed["clean"] and algebra[:len(rules)] == rules)
    return result
