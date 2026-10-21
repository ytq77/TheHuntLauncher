"""Validated gameplay preferences; only explicitly catalogued switches reach BBSC."""
import json
from pathlib import Path


def catalog(root):
    try:
        document = json.loads((Path(root) / "feature-catalog.json").read_text(encoding="utf-8-sig"))
        if document["schema"] != 1:
            raise ValueError
        return document["settings"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("功能说明文件缺失或损坏，请重新解压完整安装包。") from None


def validate(root, values):
    if not isinstance(values, dict):
        raise ValueError("功能配置格式不正确。")
    entries = {item["id"]: item for item in catalog(root) if item["type"] in {"bool", "int"}}
    if values.keys() - entries.keys():
        raise ValueError("功能配置包含当前版本不支持的项目，请在功能页重新保存。")
    resolved = {}
    for key, item in entries.items():
        value = values.get(key, item["default"])
        if item["type"] == "bool" and type(value) is not bool:
            raise ValueError(item["title"] + "必须为开启或关闭。")
        if item["type"] == "int" and (type(value) is not int or not item["min"] <= value <= item["max"]):
            raise ValueError(item["title"] + f"必须是 {item['min']}–{item['max']} 的整数。")
        if not item["editable"] and value != item["default"]:
            raise ValueError(item["title"] + "是本包的必要基础功能。")
        resolved[key] = value
    return resolved


def load(root):
    path = Path(root) / "features.json"
    if not path.exists():
        return validate(root, {})
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(document, dict) or document.get("schema") != 1:
            raise ValueError
        values = document["values"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("无法读取功能配置，请在功能页重新保存。") from None
    return validate(root, values)


def environment(root):
    values = load(root)
    entries = catalog(root)
    env = {item["env"]: str(int(values[item["id"]])) for item in entries
           if item["type"] in {"bool", "int"}}
    return env, values
