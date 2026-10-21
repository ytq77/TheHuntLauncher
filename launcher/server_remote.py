"""One-shot SSH helper for the pinned Compose recipe; no listening admin port."""
import configparser
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid
from urllib.request import ProxyHandler, build_opener


def command(args, root):
    try:
        result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("服务器命令未完成，请检查 Docker 权限和服务状态。") from None
    if result.returncode:
        raise ValueError("服务器命令失败，请检查 Compose 目录、Docker 权限和服务状态。")
    return result.stdout


def root_path(value):
    if not isinstance(value, str) or not re.fullmatch(r"/opt/[A-Za-z0-9_./-]+", value) or ".." in value.split("/"):
        raise ValueError("请填写 /opt 下的实际 BBSC Compose 目录。")
    root = Path(value).resolve(strict=True)
    if not root.is_relative_to("/opt") or root == Path("/opt") or not (root / "compose.yaml").is_file():
        raise ValueError("未找到指定的 BBSC Compose 部署。")
    return root


def database(root):
    path = root / "shadnet-state/db/shadnet.db"
    if not path.is_file():
        raise ValueError("未找到本配方的 shadNet 数据库，请核对部署目录。")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    return connection


def accounts(root):
    with closing(database(root)) as connection:
        rows = connection.execute("SELECT user_id,username,admin,banned FROM account ORDER BY user_id LIMIT 1000").fetchall()
    return [{"id": row[0], "name": row[1], "admin": bool(row[2]), "banned": bool(row[3])} for row in rows]


def status(root):
    raw = command(["docker", "compose", "ps", "--all", "--format", "json"], root).strip()
    if not raw:
        rows = []
    elif raw.startswith("["):
        rows = json.loads(raw)
    else:
        rows = [json.loads(line) for line in raw.splitlines()]
    services = [{key: row.get(key, "") for key in ("Service", "State", "Health", "Status")} for row in rows]
    return {"services": services, "accounts": accounts(root),
            "server_hostname": os.uname().nodename,
            "checked_utc": datetime.now(timezone.utc).isoformat(),
            "notice": "容器状态与账号列表不等于 UDP 可用或实际召唤成功。"}


def credentials(request):
    name, password = request.get("name", ""), request.get("password", "")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,16}", name) or name == "DeletedUser":
        raise ValueError("账号名需为 3–16 位 ASCII 字母、数字、下划线或连字符。")
    if not isinstance(password, str) or not 1 <= len(password) <= 256 or any(ord(c) < 32 for c in password):
        raise ValueError("请填写 1–256 个字符的密码，不含控制字符。")
    return name, password


def sample(root, config, action, *values):
    general = config["General"]
    host, port = general["Host"], general["UnsecuredPort"]
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host) or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("shadNet 监听配置无效。")
    output = command(["docker", "compose", "exec", "-T", "shadnet", "shadnet-sample",
                      host, port, action, *values], root)
    marker = "[create] Account created successfully." if action == "register" else "[login] OK userId="
    if marker not in output:
        reason = re.search(r"\[(?:create|login)\] FAILED: (\w+)", output)
        raise ValueError("账号操作未得到成功确认：" + (reason.group(1) if reason else "请检查服务日志，勿重复新建。"))


def backup_accounts(root):
    folder = root / "backups"
    folder.mkdir(mode=0o700, exist_ok=True)
    target = folder / ("before-account-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".sqlite")
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(database(root)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("账号数据库备份校验失败，未进行注册。")
    return target.name


def handle(request):
    if not isinstance(request, dict) or request.get("action") not in {"status", "create", "verify"}:
        raise ValueError("不支持的管理操作。")
    root = root_path(request.get("directory"))
    if request["action"] == "status":
        return status(root)
    name, password = credentials(request)
    if request["action"] == "create" and request.get("confirmed") is not True:
        raise ValueError("新建账号必须先确认。")
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / "shadnet-state/shadnet.cfg", encoding="utf-8")
    if "General" not in config:
        raise ValueError("无法读取 shadNet 配置。")
    backup = None
    if request["action"] == "create":
        if any(row["name"].casefold() == name.casefold() for row in accounts(root)):
            raise ValueError("该账号已存在，未进行任何更改。")
        email = request.get("email", "")
        if not isinstance(email, str) or not re.fullmatch(r"[^\s@]{1,100}@[^\s@]{1,100}\.[^\s@]{1,40}", email):
            raise ValueError("请填写不重复的有效邮箱标识。")
        secret = config["General"].get("RegistrationSecretKey", "")
        if not secret or "__" in secret:
            raise ValueError("服务器没有可用的注册密钥，未创建账号。")
        backup = backup_accounts(root)
        sample(root, config, "register", name, password, email, secret)
        try:
            sample(root, config, "login", name, password)
        except ValueError:
            raise ValueError("账号已创建，但登录验证未通过；请刷新列表后排查，不要重复注册。") from None
    else:
        sample(root, config, "login", name, password)
    return {"message": "账号已创建并通过登录验证。" if backup else "账号密码验证通过。",
            "backup": backup, "accounts": accounts(root)}


def main():
    os.umask(0o077)
    try:
        data = sys.stdin.read(16385)
        if len(data) > 16384:
            raise ValueError("管理请求过大。")
        result = {"ok": True, **handle(json.loads(data))}
    except ValueError as error:
        # Only messages we create are suitable for the client; parser details can
        # contain pieces of the original credential-bearing input.
        message = "管理请求格式不正确。" if isinstance(error, json.JSONDecodeError) else str(error)
        result = {"ok": False, "error": message}
    except (OSError, sqlite3.Error, configparser.Error, KeyError, TypeError):
        result = {"ok": False, "error": "服务器操作未完成，请核对部署目录、管理员权限与服务状态。"}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
