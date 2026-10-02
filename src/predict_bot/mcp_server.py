"""Project-scoped FastMCP filesystem server.

The server exposes complete file access inside this checkout while keeping a
strict filesystem boundary for file tools. Paths are always project-relative,
symlinks are rejected, and process execution is explicit, bounded, and
reported as best-effort rather than an OS sandbox.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Iterator

from fastmcp import FastMCP


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_LABEL = PROJECT_ROOT.name
DEFAULT_PORT = 8000
MIN_CHUNK_SIZE = 64 * 1024
DEFAULT_CHUNK_SIZE = 256 * 1024
MAX_CHUNK_SIZE = 1024 * 1024
MAX_PROCESS_TIMEOUT_SECONDS: int | None = None
MAX_PROCESS_OUTPUT_BYTES = 10 * 1024 * 1024
SENSITIVE_PROCESS_ENV_NAMES = {
    "CONTROL_PLANE_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_TUNNEL_TOKEN",
    "CF_API_TOKEN",
    "OPENAI_API_KEY",
    "TUNNEL_TOKEN",
}


def _relative_path(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix() or "."


def _reject_symlink_components(requested: Path) -> None:
    current = PROJECT_ROOT
    for component in requested.parts:
        current /= component
        if current.is_symlink():
            raise PermissionError("symlinks are not allowed in project paths")


def _resolve_project_path(path: str, *, must_exist: bool = True) -> Path:
    if "\x00" in path:
        raise ValueError("path contains a NUL byte")

    requested = Path(path.strip() or ".")
    if requested.is_absolute():
        raise ValueError("absolute paths are not allowed; use a project-relative path")
    _reject_symlink_components(requested)

    resolved = (PROJECT_ROOT / requested).resolve(strict=False)
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("path escapes the project root") from exc

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"project path does not exist: {path}")
    return resolved


def _require_non_root(path: Path) -> None:
    if path == PROJECT_ROOT:
        raise PermissionError("the project root itself cannot be replaced or deleted")


def _validate_optional_limit(value: int, *, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} must be 0 or greater; 0 means unlimited")


def _validate_chunk_request(chunk_size: int, offset: int) -> None:
    if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must be between {MIN_CHUNK_SIZE} and {MAX_CHUNK_SIZE} bytes"
        )
    if offset < 0:
        raise ValueError("offset must be 0 or greater")


def _resolve_process_cwd(cwd: str) -> Path:
    workdir = _resolve_project_path(cwd)
    if not workdir.is_dir():
        raise NotADirectoryError(f"process cwd is not a directory: {cwd}")
    return workdir


def _validate_process_limits(timeout_seconds: int, max_output_bytes: int) -> None:
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be 0 or greater; 0 means unlimited")
    if not 1 <= max_output_bytes <= MAX_PROCESS_OUTPUT_BYTES:
        raise ValueError(
            f"max_output_bytes must be between 1 and {MAX_PROCESS_OUTPUT_BYTES}"
        )


def _process_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in SENSITIVE_PROCESS_ENV_NAMES
    }


def _read_process_output(stream: tempfile._TemporaryFileWrapper, limit: int) -> tuple[str, bool]:
    stream.seek(0)
    payload = stream.read(limit + 1)
    truncated = len(payload) > limit
    return payload[:limit].decode("utf-8", errors="replace"), truncated


def _run_process(
    command: list[str] | str,
    *,
    cwd: str,
    timeout_seconds: int,
    max_output_bytes: int,
    shell: bool,
    confirm: bool,
) -> dict[str, object]:
    if not confirm:
        raise PermissionError("set confirm=true to execute a process")
    if shell and not isinstance(command, str):
        raise TypeError("shell command must be a string")
    if not shell and (not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command)):
        raise TypeError("process command must be a non-empty list of strings")

    _validate_process_limits(timeout_seconds, max_output_bytes)
    workdir = _resolve_process_cwd(cwd)
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=str(workdir),
            shell=shell,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=_process_environment(),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        timed_out = False
        try:
            return_code = process.wait() if timeout_seconds == 0 else process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            return_code = process.wait(timeout=5)

        stdout, stdout_truncated = _read_process_output(stdout_file, max_output_bytes)
        stderr, stderr_truncated = _read_process_output(stderr_file, max_output_bytes)

    return {
        "ok": return_code == 0 and not timed_out,
        "cwd": _relative_path(workdir),
        "return_code": return_code,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "max_output_bytes": max_output_bytes,
        "boundary": "cwd starts inside project; child process is not an OS sandbox",
    }


def _iter_files(scope: Path, *, recursive: bool) -> Iterator[Path]:
    if scope.is_file():
        yield scope
        return

    if recursive:
        for current, directories, filenames in os.walk(
            scope, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            directories[:] = sorted(
                directory
                for directory in directories
                if not (current_path / directory).is_symlink()
            )
            for filename in sorted(filenames):
                candidate = current_path / filename
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(PROJECT_ROOT)
                if resolved.is_file():
                    yield resolved
    else:
        for candidate in sorted(scope.iterdir(), key=lambda item: item.name.lower()):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            yield candidate.resolve(strict=True)


def _metadata(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": _relative_path(path),
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "is_file": path.is_file(),
        "is_directory": path.is_dir(),
    }


mcp = FastMCP("BTC 5M Lab Project Files")


@mcp.tool
def project_scope() -> dict[str, object]:
    """Describe the complete project-scoped filesystem permission model."""

    return {
        "ok": True,
        "project": PROJECT_LABEL,
        "root": ".",
        "access": "read_write_create_move_copy_delete",
        "path_format": "project-relative only",
        "directory_exclusions": [],
        "git_metadata_exposed": True,
        "sensitive_files_hidden": False,
        "unlimited_file_read_and_text_search": True,
        "shell_or_process_execution": True,
        "process_execution": "enabled_with_confirm_and_project_cwd",
        "shell_execution": "enabled_with_confirm; not_an_os_sandbox",
        "process_timeout_default_seconds": 0,
        "process_timeout_zero_means": "unlimited_local_wait",
        "process_timeout_max_seconds": MAX_PROCESS_TIMEOUT_SECONDS,
        "process_output_max_bytes": MAX_PROCESS_OUTPUT_BYTES,
    }


@mcp.tool
def lab_status() -> dict[str, object]:
    """Return a server status response without exposing the OS path."""

    return {
        "ok": True,
        "service": "BTC 5M Lab MCP",
        "access": "complete_project_filesystem",
    }


@mcp.tool
def list_project_files(
    path: str = ".",
    recursive: bool = True,
    max_items: int = 500,
) -> dict[str, object]:
    """List files under a project-relative path; max_items=0 means unlimited."""

    _validate_optional_limit(max_items, name="max_items")
    scope = _resolve_project_path(path)
    if not scope.is_dir() and not scope.is_file():
        raise NotADirectoryError(f"not a file or directory: {path}")

    files: list[str] = []
    file_iterator = iter(_iter_files(scope, recursive=recursive))
    truncated = False
    for candidate in file_iterator:
        files.append(_relative_path(candidate))
        if max_items and len(files) >= max_items:
            truncated = next(file_iterator, None) is not None
            break

    return {
        "ok": True,
        "path": _relative_path(scope),
        "recursive": recursive,
        "files": files,
        "count": len(files),
        "truncated": truncated,
    }


@mcp.tool
def file_metadata(path: str) -> dict[str, object]:
    """Return metadata before reading, including the byte size for chunking."""

    candidate = _resolve_project_path(path)
    return {"ok": True, **_metadata(candidate)}


@mcp.tool
def read_project_file(path: str, max_bytes: int = 0) -> dict[str, object]:
    """Read a project file; use read_file_chunk for files larger than one MB."""

    _validate_optional_limit(max_bytes, name="max_bytes")
    candidate = _resolve_project_path(path)
    if not candidate.is_file():
        raise IsADirectoryError(f"not a file: {path}")
    if max_bytes and candidate.stat().st_size > max_bytes:
        raise ValueError(
            f"file is {candidate.stat().st_size} bytes; max_bytes is {max_bytes}"
        )

    payload = candidate.read_bytes()
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "ok": True,
            "path": _relative_path(candidate),
            "bytes": len(payload),
            "encoding": "base64",
            "content_base64": base64.b64encode(payload).decode("ascii"),
        }
    if b"\x00" in payload:
        return {
            "ok": True,
            "path": _relative_path(candidate),
            "bytes": len(payload),
            "encoding": "base64",
            "content_base64": base64.b64encode(payload).decode("ascii"),
        }
    return {
        "ok": True,
        "path": _relative_path(candidate),
        "bytes": len(payload),
        "encoding": "utf-8",
        "content": content,
    }


@mcp.tool
def read_file_chunk(
    file_path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    offset: int = 0,
    expected_size: int = -1,
    expected_modified_ns: int = 0,
) -> dict[str, object]:
    """Read one bounded byte chunk from a project file.

    Call file_metadata first, then repeat this tool with next_offset until eof
    is true. Each response is capped at 1 MiB of raw bytes so callers can read
    files much larger than the transport's single-response limit.
    """

    _validate_chunk_request(chunk_size, offset)
    if expected_size < -1:
        raise ValueError("expected_size must be -1 or greater")
    if expected_modified_ns < 0:
        raise ValueError("expected_modified_ns must be 0 or greater")

    candidate = _resolve_project_path(file_path)
    if not candidate.is_file():
        raise IsADirectoryError(f"not a file: {file_path}")

    stat = candidate.stat()
    if expected_size >= 0 and stat.st_size != expected_size:
        raise ValueError(
            f"file size changed from {expected_size} to {stat.st_size} bytes"
        )
    if expected_modified_ns and stat.st_mtime_ns != expected_modified_ns:
        raise ValueError("file modified since metadata was read")
    if offset > stat.st_size:
        raise ValueError(f"offset {offset} is beyond file size {stat.st_size}")

    with candidate.open("rb") as binary_stream:
        binary_stream.seek(offset)
        payload = binary_stream.read(chunk_size)

    next_offset = offset + len(payload)
    eof = next_offset >= stat.st_size
    result: dict[str, object] = {
        "ok": True,
        "path": _relative_path(candidate),
        "offset": offset,
        "next_offset": next_offset,
        "chunk_bytes": len(payload),
        "total_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "eof": eof,
        "requested_chunk_size": chunk_size,
    }

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    if text and b"\x00" not in payload:
        result.update({"encoding": "utf-8", "content": text})
    elif not payload:
        result.update({"encoding": "utf-8", "content": ""})
    else:
        result.update(
            {
                "encoding": "base64",
                "content_base64": base64.b64encode(payload).decode("ascii"),
            }
        )
    return result


@mcp.tool
def run_project_process(
    program: str,
    args: list[str] | None = None,
    cwd: str = ".",
    timeout_seconds: int = 0,
    max_output_bytes: int = 256 * 1024,
    confirm: bool = False,
) -> dict[str, object]:
    """Run one non-shell process with a project-relative working directory."""

    if not program.strip():
        raise ValueError("program must not be empty")
    command = [program, *(args or [])]
    return _run_process(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        shell=False,
        confirm=confirm,
    )


@mcp.tool
def run_project_shell(
    command: str,
    cwd: str = ".",
    timeout_seconds: int = 0,
    max_output_bytes: int = 256 * 1024,
    confirm: bool = False,
) -> dict[str, object]:
    """Run a shell command from a project-relative working directory.

    This is not an OS sandbox: the command can change directory or launch a
    child process outside the project. Keep this tool behind authenticated MCP
    transport and use confirm=true only for intentional commands.
    """

    if not command.strip():
        raise ValueError("command must not be empty")
    return _run_process(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        shell=True,
        confirm=confirm,
    )


@mcp.tool
def search_project_files(
    query: str,
    path: str = ".",
    max_results: int = 100,
    max_bytes_per_file: int = 0,
) -> dict[str, object]:
    """Stream-search UTF-8 text files; max_bytes_per_file=0 means unlimited."""

    if not query:
        raise ValueError("query must not be empty")
    _validate_optional_limit(max_results, name="max_results")
    _validate_optional_limit(max_bytes_per_file, name="max_bytes_per_file")
    scope = _resolve_project_path(path)
    results: list[dict[str, object]] = []
    needle = query.casefold()

    for candidate in _iter_files(scope, recursive=True):
        if max_bytes_per_file and candidate.stat().st_size > max_bytes_per_file:
            continue
        try:
            with candidate.open("rb") as binary_stream:
                if b"\x00" in binary_stream.read(8192):
                    continue
            with candidate.open("r", encoding="utf-8", errors="replace") as text_stream:
                for line_number, line in enumerate(text_stream, start=1):
                    if needle not in line.casefold():
                        continue
                    results.append(
                        {
                            "path": _relative_path(candidate),
                            "line": line_number,
                            "text": line.rstrip("\r\n")[:1_000],
                        }
                    )
                    if max_results and len(results) >= max_results:
                        return {
                            "ok": True,
                            "query": query,
                            "results": results,
                            "count": len(results),
                            "truncated": True,
                        }
        except (OSError, UnicodeError):
            continue

    return {
        "ok": True,
        "query": query,
        "results": results,
        "count": len(results),
        "truncated": False,
    }


@mcp.tool
def make_project_directory(path: str, parents: bool = True) -> dict[str, object]:
    """Create a directory inside the project boundary."""

    target = _resolve_project_path(path, must_exist=False)
    _require_non_root(target)
    target.mkdir(parents=parents, exist_ok=False)
    return {"ok": True, **_metadata(target)}


@mcp.tool
def write_project_file(
    path: str,
    content: str,
    overwrite: bool = True,
) -> dict[str, object]:
    """Write UTF-8 text atomically inside the project boundary."""

    target = _resolve_project_path(path, must_exist=False)
    _require_non_root(target)
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"not a file: {path}")
    if target.exists() and not overwrite:
        raise FileExistsError(f"file already exists: {path}")
    if not target.parent.exists():
        raise FileNotFoundError(f"parent directory does not exist: {target.parent}")

    temporary = target.with_name(f".{target.name}.mcp-tmp-{uuid.uuid4().hex}")
    temporary.write_text(content, encoding="utf-8", newline="")
    temporary.replace(target)
    return {"ok": True, **_metadata(target)}


@mcp.tool
def copy_project_path(
    source: str,
    destination: str,
    overwrite: bool = False,
) -> dict[str, object]:
    """Copy a project file or directory to another project-relative path."""

    source_path = _resolve_project_path(source)
    destination_path = _resolve_project_path(destination, must_exist=False)
    _require_non_root(source_path)
    _require_non_root(destination_path)
    if destination_path.exists() and not overwrite:
        raise FileExistsError(f"destination already exists: {destination}")
    if not destination_path.parent.exists():
        raise FileNotFoundError(f"parent directory does not exist: {destination_path.parent}")
    if source_path.is_dir():
        if destination_path.exists():
            shutil.rmtree(destination_path)
        shutil.copytree(source_path, destination_path)
    else:
        shutil.copy2(source_path, destination_path)
    return {"ok": True, **_metadata(destination_path)}


@mcp.tool
def move_project_path(
    source: str,
    destination: str,
    overwrite: bool = False,
) -> dict[str, object]:
    """Move a project file or directory within the project boundary."""

    source_path = _resolve_project_path(source)
    destination_path = _resolve_project_path(destination, must_exist=False)
    _require_non_root(source_path)
    _require_non_root(destination_path)
    if destination_path.exists() and not overwrite:
        raise FileExistsError(f"destination already exists: {destination}")
    if not destination_path.parent.exists():
        raise FileNotFoundError(f"parent directory does not exist: {destination_path.parent}")
    if destination_path.exists() and destination_path.is_dir():
        shutil.rmtree(destination_path)
    elif destination_path.exists():
        destination_path.unlink()
    shutil.move(str(source_path), str(destination_path))
    return {"ok": True, **_metadata(destination_path)}


@mcp.tool
def delete_project_path(
    path: str,
    recursive: bool = False,
    confirm: bool = False,
) -> dict[str, object]:
    """Delete a project path; confirmation is required and the root is protected."""

    if not confirm:
        raise PermissionError("set confirm=true to delete a project path")
    target = _resolve_project_path(path)
    _require_non_root(target)
    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()
    return {"ok": True, "deleted": path}


def _server_port() -> int:
    raw_port = os.environ.get("MCP_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("MCP_PORT must be an integer between 1024 and 65535") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("MCP_PORT must be an integer between 1024 and 65535")
    return port


if __name__ == "__main__":
    port = _server_port()
    mcp.run(
        transport="http",
        host="127.0.0.1",
        port=port,
        path="/mcp",
        host_origin_protection=True,
        allowed_hosts=["127.0.0.1", f"127.0.0.1:{port}", "localhost", f"localhost:{port}"],
        allowed_origins=[f"http://127.0.0.1:{port}", f"http://localhost:{port}"],
        show_banner=False,
    )
