"""Carry bounded admin requests over existing OpenSSH key authentication."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent


def ssh_command(connection):
    target = connection.get("target", "")
    directory = connection.get("directory", "")
    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,200}", target):
        raise ValueError("请填写 SSH 主机别名或 用户@主机，不含空格和命令。")
    if not isinstance(directory, str) or not re.fullmatch(r"/opt/[A-Za-z0-9_./-]+", directory) or ".." in directory.split("/"):
        raise ValueError("请填写 /opt 下的 BBSC Compose 目录。")
    port = connection.get("port", 22)
    if type(port) is not int or not 1 <= port <= 65535 or type(connection.get("sudo", False)) is not bool:
        raise ValueError("SSH 端口或 sudo 设置无效。")
    executable = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/OpenSSH/ssh.exe"
    command = [str(executable), "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2", "-p", str(port)]
    identity = connection.get("identity", "")
    if identity:
        if not isinstance(identity, str) or not Path(identity).is_file():
            raise ValueError("SSH 私钥文件不存在。")
        command += ["-i", identity, "-o", "IdentitiesOnly=yes"]
    known_hosts = connection.get("known_hosts", "")
    if known_hosts:
        if not isinstance(known_hosts, str) or not Path(known_hosts).is_file():
            raise ValueError("指定的 SSH 主机指纹文件不存在。")
        command += ["-o", 'UserKnownHostsFile="' + str(Path(known_hosts).resolve()).replace("\\", "/") + '"']
    code = base64.b64encode((ROOT / "server_remote.py").read_bytes()).decode("ascii")
    remote = ("sudo -n " if connection.get("sudo", False) else "") + "python3 -c 'import base64;exec(base64.b64decode(\"" + code + "\"))'"
    return command + [target, remote]


def invoke(connection, action, fields=None):
    if action not in {"status", "create", "verify"}:
        raise ValueError("不支持的服务器操作。")
    request = {"action": action, "directory": connection["directory"]}
    if fields:
        request.update({key: value for key, value in fields.items() if key in {"name", "password", "email", "confirmed"}})
    command = ssh_command(connection)
    try:
        result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True,
                                timeout=100, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        raise ValueError("SSH 管理连接未完成。请检查 OpenSSH、密钥、主机指纹与管理员权限。") from None
    if result.returncode:
        raise ValueError("SSH 管理连接失败。请先在终端用相同身份连接并核对主机指纹；sudo 必须可非交互执行。")
    if len(result.stdout) > 1_000_000:
        raise ValueError("服务器返回内容过大，已停止显示。")
    try:
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise ValueError
    except ValueError:
        raise ValueError("服务器没有返回有效管理结果；请检查 Python 3 和 Compose 环境。") from None
    return response


if __name__ == "__main__":
    # The UI sends secrets through stdin, never arguments, files or history.
    import sys
    try:
        request = json.loads(sys.stdin.read(16385))
        response = invoke(request["connection"], request["action"], request.get("fields"))
    except (OSError, ValueError, KeyError, TypeError):
        response = {"ok": False, "error": "管理请求未完成。请核对 SSH 设置、密钥权限、服务状态和输入内容。"}
    print(json.dumps(response, ensure_ascii=True))
