#!/usr/bin/env python3
"""Qualify official/redis deterministic suites and a locked package consumer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


PACKAGE = Path(__file__).resolve().parents[1]


class QualificationError(RuntimeError):
    pass


def run(argv: list[str], *, cwd: Path,
        env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if result.returncode != 0:
        raise QualificationError(
            "command failed (%d): %s\nstdout:\n%s\nstderr:\n%s"
            % (result.returncode, " ".join(argv), result.stdout, result.stderr)
        )
    return result


def resolve_toolchain(env: dict[str, str]) -> tuple[Path, Path, Path, Path, Path]:
    root_is_set = "TOKA_ROOT" in env
    explicit_keys = ("TOKA", "TOKAC", "TOKA_LIB")
    explicit_set = [key for key in explicit_keys if key in env]
    if root_is_set and explicit_set:
        raise QualificationError(
            "set either TOKA_ROOT or TOKA/TOKAC/TOKA_LIB, not both"
        )
    if root_is_set:
        if not env["TOKA_ROOT"].strip():
            raise QualificationError("TOKA_ROOT must not be empty")
        root = Path(env["TOKA_ROOT"]).expanduser().resolve()
        toka = root / "build" / "bin" / "toka"
        tokac = root / "build" / "bin" / "tokac"
        library = root / "lib"
        runtime = library / "sys" / "toka_rt.o"
        build_driver = root / "tools" / "scripts" / "toka_build.py"
    else:
        if len(explicit_set) != len(explicit_keys):
            missing = ", ".join(key for key in explicit_keys if key not in env)
            raise QualificationError(
                "set TOKA_ROOT or all of TOKA/TOKAC/TOKA_LIB"
                + (" (missing: " + missing + ")" if missing else "")
            )
        empty = [key for key in explicit_keys if not env[key].strip()]
        if empty:
            raise QualificationError(
                "toolchain variables must not be empty: " + ", ".join(empty)
            )
        toka = Path(env["TOKA"]).expanduser().resolve()
        tokac = Path(env["TOKAC"]).expanduser().resolve()
        library = Path(env["TOKA_LIB"]).expanduser().resolve()
        runtime = library / "sys" / "toka_rt.o"
        build_driver = library / "toolchain" / "toka_build.py"

    required_files = {
        "toka": toka,
        "tokac": tokac,
        "toka_rt.o": runtime,
        "toka_build.py": build_driver,
    }
    missing_files = [name for name, path in required_files.items() if not path.is_file()]
    if not library.is_dir():
        missing_files.append("TOKA_LIB")
    if missing_files:
        raise QualificationError(
            "incomplete Toka toolchain (missing: %s)" % ", ".join(missing_files)
        )
    return toka, tokac, library, runtime, build_driver


def make_sdk(work: Path, source_library: Path, runtime: Path,
             build_driver: Path) -> Path:
    library = work / "sdk" / "lib"
    shutil.copytree(
        source_library,
        library,
        ignore=shutil.ignore_patterns("*.pyc", "__pycache__"),
    )
    runtime_dir = library / "sys"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runtime, runtime_dir / "toka_rt.o")
    toolchain = library / "toolchain"
    toolchain.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build_driver, toolchain / "toka_build.py")
    return library


def generate_tls_fixture(package: Path, openssl: str,
                         env: dict[str, str]) -> None:
    fixture = package / ".toka-test" / "tls"
    fixture.mkdir(parents=True, mode=0o700)
    fixture.chmod(0o700)
    ca_key = fixture / "ca.key"
    ca_cert = fixture / "ca.crt"
    server_key = fixture / "server.key"
    server_csr = fixture / "server.csr"
    server_cert = fixture / "server.crt"
    extensions = fixture / "server.ext"
    extensions.write_text(
        "[v3_req]\n"
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    run([
        openssl, "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-keyout", str(ca_key), "-out", str(ca_cert), "-days", "1",
        "-subj", "/CN=toka-redis-test-ca",
    ], cwd=package, env=env)
    run([
        openssl, "req", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-keyout", str(server_key), "-out", str(server_csr),
        "-subj", "/CN=localhost",
    ], cwd=package, env=env)
    run([
        openssl, "x509", "-req", "-in", str(server_csr),
        "-CA", str(ca_cert), "-CAkey", str(ca_key), "-CAcreateserial",
        "-out", str(server_cert), "-days", "1", "-sha256",
        "-extfile", str(extensions), "-extensions", "v3_req",
    ], cwd=package, env=env)
    ca_cert.chmod(0o644)
    server_cert.chmod(0o644)
    server_key.chmod(0o600)


def write_consumer(project: Path, dependency: Path) -> None:
    (project / "src").mkdir(parents=True)
    (project / "package.tk").write_text(
        "pub const PACKAGE = (\n"
        '    name = "redis_consumer",\n'
        '    version = "0.1.0",\n'
        "    dependencies = (\n"
        "        redis = %s,\n"
        "    )\n"
        ")\n" % json.dumps(str(dependency)),
        encoding="utf-8",
    )
    (project / "build.tk").write_text(
        "import build::{Executable, run_build}\n\n"
        "fn main() -> i32 {\n"
        '    auto app# = Executable::make(c"redis_consumer", c"src/main.tk")\n'
        "    return run_build(app)\n"
        "}\n",
        encoding="utf-8",
    )
    (project / "src" / "main.tk").write_text(
        "import official/redis::{RedisCommand, RedisDecode, decode_one}\n"
        "import std/vec::{Vec}\n\n"
        "fn main() -> i32 {\n"
        '    auto command = RedisCommand::new("PING")\n'
        "    if command.into_wire().is_err() { return 1 }\n"
        "    auto frame# = Vec<u8>::new()\n"
        "    frame#.push('+' as u8)\n"
        "    frame#.push('O' as u8)\n"
        "    frame#.push('K' as u8)\n"
        "    frame#.push('\\r' as u8)\n"
        "    frame#.push('\\n' as u8)\n"
        "    match decode_one(frame).unwrap() {\n"
        "        auto RedisDecode::Complete(_) => return 0\n"
        "        _ => return 2\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )


def main() -> int:
    host_env = dict(os.environ)
    toka, tokac, source_library, runtime, build_driver = resolve_toolchain(host_env)
    openssl = shutil.which("openssl", path=host_env.get("PATH"))
    if openssl is None:
        raise QualificationError("OpenSSL executable is required for the TLS fixture")

    with tempfile.TemporaryDirectory(prefix="toka-redis-package-") as temporary:
        work = Path(temporary)
        sdk = make_sdk(work, source_library, runtime, build_driver)
        base_env = dict(host_env)
        base_env.update({"TOKAC": str(tokac), "TOKA_LIB": str(sdk)})
        base_env.pop("TOKA_ROOT", None)
        base_env.pop("TOKA", None)
        base_env.pop("TOKA_OFFLINE", None)
        exec_env = dict(base_env)
        exec_env.pop("TOKA_LIB", None)
        dependency = work / "redis"
        shutil.copytree(
            PACKAGE,
            dependency,
            ignore=shutil.ignore_patterns(
                ".git", ".toka-test", "__pycache__", "*.pyc"
            ),
        )
        generate_tls_fixture(dependency, openssl, base_env)

        include = ["-I", str(sdk), "-I", str(dependency / "lib")]
        deterministic_suites = (
            "codec_v1",
            "client_v1",
            "clone_ownership_v1",
            "transport_v2",
            "pool_v1",
        )
        for suite in deterministic_suites:
            program = work / suite
            run([str(tokac), *include,
                 str(dependency / "tests" / (suite + ".tk")),
                 "-o", str(program)], cwd=dependency, env=base_env)
            run([str(program)], cwd=dependency, env=exec_env)
        shutil.rmtree(dependency / ".toka-test")
        if any(dependency.rglob("*.key")):
            raise QualificationError("TLS private key remained in the package tree")

        project = work / "consumer"
        write_consumer(project, dependency)

        run([str(toka), "fetch"], cwd=project, env=base_env)
        lock = project / "package.lock"
        locked = lock.read_bytes()
        if not locked.startswith(b"toka-lock-v1\n") or b"redis" not in locked:
            raise QualificationError("Redis consumer did not produce a v1 lock with redis")

        offline_env = dict(base_env)
        offline_env["TOKA_OFFLINE"] = "1"
        run([str(toka), "fetch"], cwd=project, env=offline_env)
        if lock.read_bytes() != locked:
            raise QualificationError("offline Redis fetch changed package.lock")
        run([str(toka), "build"], cwd=project, env=offline_env)
        program = project / "target" / "debug" / "redis_consumer"
        if not program.is_file():
            raise QualificationError("toka build did not produce Redis consumer")
        run([str(program)], cwd=project, env=offline_env)

    print(json.dumps({
        "result": "pass",
        "schema": "toka.official-redis-package-v1",
        "stages": {
            "bounded_connection_pool": "pass",
            "clone_ownership": "pass",
            "locked_local_dependency": "pass",
            "offline_lock_replay": "pass",
            "public_import_build_run": "pass",
            "resp2_codec": "pass",
            "serial_client": "pass",
            "verified_tls_and_pipeline": "pass",
        },
        "version": 1,
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, QualificationError, subprocess.TimeoutExpired) as error:
        print("FAIL: " + str(error))
        raise SystemExit(1)
