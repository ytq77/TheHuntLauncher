"""Offline Linux recipe/helper wiring and credential transport regressions."""
import configparser
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load("recipe_account_helper_test", ROOT / "recipe/account_helper.py")
account = load("recipe_account_test", ROOT / "recipe/create-account.py")
# Read the current caller without modifying the parallel worker's client code.
remote = load("remote_account_helper_test", ROOT.parent / "launcher/server_remote.py")


class AccountHelperWiringTests(unittest.TestCase):
    def test_standalone_recipe_uses_exact_windows_helper_source(self):
        self.assertEqual((ROOT / "recipe/fixes/stdin_account.cpp").read_bytes(),
                         (ROOT / "windows/stdin_account.cpp").read_bytes())

    def test_pinned_sample_staging_is_repeatable_and_refuses_drift(self):
        cache = Path(os.environ.get("SERVER_SECURITY_CACHE", ROOT / "windows/downloads"))
        manifest = json.loads((ROOT / "recipe/sources.json").read_text())
        item = next(x for x in manifest["archives"] if x["name"] == "shadnet.tar.gz")
        path = cache / item["name"]
        if not path.is_file():
            self.skipTest("Pinned offline shadNet cache missing")
        data = path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                prefix = archive.getnames()[0].split("/")[0] + "/"
                for name in ("CMakeLists.txt", "clientsample/CMakeLists.txt"):
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(prefix + name).read())
            helper.stage(root)
            snapshot = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            helper.stage(root)
            self.assertEqual(snapshot, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})
            # Docker and the Linux ZIP carry only recipe/, with no windows/ sibling.
            standalone = root / "isolated/recipe"
            for name in ("account_helper.py", "fixes/stdin_account.cpp", "tests/admin_fake_connection.cpp",
                         "tests/check_stdin_account.py"):
                target = standalone / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "recipe" / name, target)
            isolated = load("standalone_account_helper_test", standalone / "account_helper.py")
            isolated.stage(root)
            self.assertEqual((root / "clientsample/stdin_account.cpp").read_bytes(),
                             (ROOT / "windows/stdin_account.cpp").read_bytes())
            cmake = root / "clientsample/CMakeLists.txt"
            cmake.write_text(cmake.read_text(encoding="utf-8") + "\n# synthetic source drift\n", encoding="utf-8")
            snapshot = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(ValueError):
                helper.stage(root)
            self.assertEqual(snapshot, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_nonwindows_cmake_branch_does_not_link_winsock_or_define_windows(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("CMake is unavailable")
        # Run the actual CMake control flow; stand-ins collect target operations.
        # This checks Linux wiring without pretending to compile Linux binaries.
        script = '''
set(WIN32 FALSE)
set(BUILD_TESTING FALSE)
function(find_package)
endfunction()
function(add_executable name)
    if(NOT name STREQUAL "shadnet-admin")
        message(FATAL_ERROR "Unexpected helper target")
    endif()
endfunction()
function(target_include_directories)
endfunction()
function(target_link_libraries)
    if("ws2_32" IN_LIST ARGN)
        message(FATAL_ERROR "Windows library on Linux")
    endif()
    if(NOT "Qt6::Core" IN_LIST ARGN OR NOT "Threads::Threads" IN_LIST ARGN)
        message(FATAL_ERROR "Missing portable helper dependency")
    endif()
endfunction()
function(target_compile_definitions)
    message(FATAL_ERROR "Windows definitions on Linux")
endfunction()
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "linux-branch.cmake"
            path.write_text("cmake_minimum_required(VERSION 3.16)\n" + script + helper.ADDITION)
            result = subprocess.run([cmake, "-P", str(path)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_docker_builds_and_installs_helper_from_its_own_recipe_context(self):
        docker = (ROOT / "recipe/Dockerfile").read_text()
        self.assertIn("python3 /security/account_helper.py --shadnet /src", docker)
        command = next(line for line in docker.splitlines() if "cmake --build" in line)
        self.assertIn("shadnet-admin", command)
        self.assertIn("test_stdin_account", command)
        self.assertIn("COPY --from=build /src/build/clientsample/shadnet-admin /usr/local/bin/shadnet-admin", docker)
        self.assertNotIn("../windows", docker)
        ignored = (ROOT / "recipe/.dockerignore").read_text().splitlines()
        # The recipe uses deny-by-default context filtering. Every COPY input
        # introduced for security/helper generation must actually be admitted.
        for entry in ("!security.py", "!account_helper.py", "!tests/", "!tests/*.cpp", "!tests/*.py"):
            self.assertIn(entry, ignored)


class AccountCallerTests(unittest.TestCase):
    def test_remote_and_recipe_caller_send_all_credentials_via_stdin(self):
        config = configparser.ConfigParser()
        config["General"] = {"Host": "100.64.0.10", "UnsecuredPort": "31413"}
        root = Path("synthetic-deployment")
        for action, values, keys in (("register", ("hunter01", "test-password-秘密", "x@example.invalid", "test-key-秘密"),
                                      ("name", "password", "email", "key")),
                                     ("login", ("hunter01", "test-password-秘密"), ("name", "password"))):
            for caller in ("remote", "recipe"):
                with self.subTest(action=action, caller=caller), \
                     patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                         [], 0, '[status]\n{"ok":true,"error":"NoError"}\n', "")) as run:
                    if caller == "remote":
                        remote.sample(root, config, action, *values)
                    else:
                        account.invoke("100.64.0.10", "31413", action, *values)
                    command = run.call_args.args[0]
                    self.assertEqual(command, ["docker", "compose", "exec", "-T", "shadnet", "shadnet-admin",
                                               "100.64.0.10", "31413", action])
                    self.assertEqual(json.loads(run.call_args.kwargs["input"]), dict(zip(keys, values)))
                    self.assertNotIn(values[1], " ".join(command))
                    if action == "register":
                        self.assertNotIn(values[3], " ".join(command))

    def test_local_failure_and_timeout_do_not_echo_input_or_subprocess_arguments(self):
        secret = "synthetic-password-must-not-be-echoed"
        responses = [subprocess.CompletedProcess([], 1, secret, secret),
                     subprocess.CompletedProcess([], 0, '[create] Account created successfully.\n', secret),
                     subprocess.CompletedProcess([], 0, '{"ok":false,"error":"' + secret + '"}', secret),
                     subprocess.TimeoutExpired(["synthetic", secret], 35, output=secret, stderr=secret)]
        for response in responses:
            with self.subTest(response=type(response).__name__):
                kwargs = {"side_effect": response} if isinstance(response, BaseException) else {"return_value": response}
                with patch.object(account.subprocess, "run", **kwargs), self.assertRaises(ValueError) as caught:
                    account.invoke("100.64.0.10", "31413", "login", "hunter01", secret)
                self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
