[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $LocalPython) {
    $Python = $LocalPython
} else {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MCP_PORT = $Port.ToString()

$script = @'
import asyncio
import json
from fastmcp import Client


async def main() -> None:
    async with Client(f"http://127.0.0.1:{__PORT__}/mcp") as client:
        tools = await client.list_tools()
        names = sorted(tool.name for tool in tools)
        scope = await client.call_tool("project_scope", {})
        files = await client.call_tool(
            "list_project_files", {"path": "src/predict_bot", "recursive": False, "max_items": 5}
        )
        git_head = await client.call_tool("read_project_file", {"path": ".git/HEAD"})
        print(json.dumps({"tools": names, "scope": str(scope), "files": str(files), "git_head": str(git_head)}, ensure_ascii=False))
        expected_tools = {
            "copy_project_path",
            "delete_project_path",
            "file_metadata",
            "lab_status",
            "list_project_files",
            "make_project_directory",
            "move_project_path",
            "project_scope",
            "read_file_chunk",
            "read_project_file",
            "run_project_process",
            "run_project_shell",
            "search_project_files",
            "write_project_file",
        }
        if not expected_tools.issubset(set(names)):
            raise AssertionError(f"missing full-access tools: {sorted(expected_tools - set(names))}")

        try:
            await client.call_tool("run_project_process", {"program": "python", "args": ["-c", "print('mcp_process_ok')"]})
        except Exception:
            process_confirmation_rejected = True
        else:
            raise AssertionError("process confirmation was not required")
        process_result = await client.call_tool(
            "run_project_process",
            {"program": "python", "args": ["-c", "print('mcp_process_ok')"], "confirm": True},
        )
        if "mcp_process_ok" not in str(process_result):
            raise AssertionError(f"process execution failed: {process_result}")
        try:
            await client.call_tool("run_project_process", {"program": "python", "cwd": "..", "confirm": True})
        except Exception:
            process_cwd_rejected = True
        else:
            raise AssertionError("process cwd escaped the project")
        shell_result = await client.call_tool(
            "run_project_shell",
            {"command": "echo mcp_shell_ok", "confirm": True},
        )
        if "mcp_shell_ok" not in str(shell_result):
            raise AssertionError(f"shell execution failed: {shell_result}")
        try:
            await client.call_tool("read_project_file", {"path": "..\\pyproject.toml"})
        except Exception:
            traversal_rejected = True
        else:
            raise AssertionError("path traversal was not rejected")

        test_path = ".mcp\\verify-write-delete.txt"
        await client.call_tool("write_project_file", {"path": test_path, "content": "mcp verification"})
        written = await client.call_tool("read_project_file", {"path": test_path})
        await client.call_tool("delete_project_path", {"path": test_path, "confirm": True})

        large_path = ".mcp\\verify-large-search.txt"
        large_content = ("x" * (1024 * 1024 + 2048)) + "\\nneedle_after_one_mb\\n"
        await client.call_tool("write_project_file", {"path": large_path, "content": large_content})
        large_search = await client.call_tool(
            "search_project_files",
            {"query": "needle_after_one_mb", "path": ".mcp", "max_results": 0, "max_bytes_per_file": 0},
        )
        await client.call_tool("delete_project_path", {"path": large_path, "confirm": True})

        chunk_path = ".mcp\\verify-large-chunk.txt"
        chunk_content = ("0123456789abcdef" * 100000) + "\\nchunk_end_marker\\n"
        await client.call_tool("write_project_file", {"path": chunk_path, "content": chunk_content})
        info = await client.call_tool("file_metadata", {"path": chunk_path})
        info_text = str(info)
        chunk_one = await client.call_tool(
            "read_file_chunk",
            {"file_path": chunk_path, "chunk_size": 65536, "offset": 0},
        )
        chunk_two = await client.call_tool(
            "read_file_chunk",
            {"file_path": chunk_path, "chunk_size": 65536, "offset": 65536},
        )
        chunk_two_text = str(chunk_two)
        if "next_offset" not in chunk_two_text or "chunk_end_marker" not in str(await client.call_tool(
            "search_project_files",
            {"query": "chunk_end_marker", "path": ".mcp", "max_results": 1},
        )):
            raise AssertionError("chunk read verification did not reach the file tail")
        await client.call_tool("delete_project_path", {"path": chunk_path, "confirm": True})
        print(json.dumps({"traversal_rejected": traversal_rejected, "process_confirmation_rejected": process_confirmation_rejected, "process_cwd_rejected": process_cwd_rejected, "process_result": str(process_result), "shell_result": str(shell_result), "write_read_delete": str(written), "unlimited_search_over_1mb": str(large_search), "chunk_metadata": info_text, "chunk_one": str(chunk_one)[:500], "chunk_two": chunk_two_text[:500]}, ensure_ascii=False))


asyncio.run(main())
'@.Replace('__PORT__', $Port.ToString())

$script | & $Python -
if ($LASTEXITCODE -ne 0) {
    throw "MCP verification failed with exit code $LASTEXITCODE"
}
