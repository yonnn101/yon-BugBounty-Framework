"""Infrastructure: async subprocess execution for external recon tools (spec §2, §4).

Subclasses inherit :class:`AsyncBaseTool`, implement :meth:`parse_output`, and call
:meth:`run_subprocess` with CLI arguments. Raw blobs go to ``storage/raw_outputs/``
until MinIO/S3 upload is wired.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from models.enums import ToolDatumKind

# Project root: .../recon_methdology
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_OUTPUT_DIR = _PROJECT_ROOT / "storage" / "raw_outputs"
DEFAULT_TIMEOUT_SECONDS = 600.0  # 10 minutes (spec: kill hung tools)


@dataclass
class ToolResult:
    """Normalized stdout/stderr/exit status from an external tool."""

    exit_code: int
    stdout: str
    stderr: str
    command: list[str]
    timed_out: bool = False


class AsyncBaseTool(ABC):
    """Abstract async wrapper around ``asyncio.create_subprocess_exec``.

    Subclasses set :attr:`tool_name`, pass ``binary_path`` to ``__init__``, and
    implement :meth:`parse_output`. Override ``output_directory`` or ``timeout_seconds``
    on the instance if needed.

    Workflow integration: declare :attr:`INPUT_TYPES` / :attr:`OUTPUT_TYPES` as the
    primary datum kinds this tool consumes and produces when used in a chain (see
    ``services.workflow_service``). Empty sets mean "unspecified / stub".
    """

    tool_name: str = "async_base_tool"
    INPUT_TYPES: ClassVar[frozenset[ToolDatumKind]] = frozenset()
    OUTPUT_TYPES: ClassVar[frozenset[ToolDatumKind]] = frozenset()

    def __init__(
        self,
        binary_path: str | Path,
        *,
        tool_name: str | None = None,
        output_directory: Path | str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.binary_path = Path(binary_path)
        if tool_name is not None:
            self.tool_name = tool_name
        self.output_directory = Path(output_directory or DEFAULT_RAW_OUTPUT_DIR)
        self.timeout_seconds = timeout_seconds

    def _command_list(self, args: Sequence[str]) -> list[str]:
        return [str(self.binary_path), *[str(a) for a in args]]

    async def run_subprocess(
        self,
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ToolResult:
        """Run ``binary_path`` with ``args``; capture stdout/stderr separately.

        On timeout, sends ``kill()`` to the process and returns
        :class:`ToolResult` with ``timed_out=True`` and ``exit_code=-1``.

        Non-zero exit codes are logged; they do not raise by default.
        """
        cmd = self._command_list(args)
        timeout_s = self.timeout_seconds if timeout is None else timeout
        cwd_path = Path(cwd) if cwd is not None else None

        logger.info(
            "[{}] Executing: {}",
            self.tool_name,
            " ".join(self._shell_quote(c) for c in cmd),
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_path,
                env=env,
            )
        except OSError as exc:
            logger.exception(
                "[{}] Failed to start process cmd={!r}: {}",
                self.tool_name,
                cmd,
                exc,
            )
            return ToolResult(
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                command=cmd,
            )

        stdout_b: bytes = b""
        stderr_b: bytes = b""
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_s,
            )
        except TimeoutError:
            logger.error(
                "[{}] Process exceeded timeout ({:.1f}s); killing: {}",
                self.tool_name,
                timeout_s,
                cmd,
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                logger.warning("[{}] Process did not exit after kill; terminating", self.tool_name)
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except (ProcessLookupError, TimeoutError):
                    pass
            return ToolResult(
                exit_code=-1,
                stdout=self._decode(stdout_b),
                stderr=self._decode(stderr_b) + "\n[async_base_tool] killed after timeout",
                command=cmd,
                timed_out=True,
            )

        exit_code = proc.returncode if proc.returncode is not None else 0
        out_s = self._decode(stdout_b)
        err_s = self._decode(stderr_b)

        if exit_code != 0:
            logger.error(
                "[{}] Non-zero exit code={} cmd={!r} stderr_preview={!r}",
                self.tool_name,
                exit_code,
                cmd,
                (err_s or "")[:2000],
            )
        elif err_s:
            logger.warning(
                "[{}] stderr (exit 0): {}",
                self.tool_name,
                err_s[:2000],
            )

        return ToolResult(
            exit_code=exit_code,
            stdout=out_s,
            stderr=err_s,
            command=cmd,
            timed_out=False,
        )

    @staticmethod
    def _decode(data: bytes) -> str:
        if not data:
            return ""
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _shell_quote(part: str) -> str:
        if not part or any(c in part for c in ' \t\n"\'\\'):
            return repr(part)
        return part

    def save_raw_output(self, data: str | bytes, filename: str) -> Path:
        """Write full tool output under :attr:`output_directory` (local disk; S3 later)."""
        safe = Path(filename).name
        if not safe or safe in {".", ".."}:
            msg = f"Invalid filename: {filename!r}"
            logger.error("[{}] save_raw_output: {}", self.tool_name, msg)
            raise ValueError(msg)

        dest = (self.output_directory / safe).resolve()
        try:
            dest.relative_to(self.output_directory.resolve())
        except ValueError:
            msg = "Path escapes output_directory"
            logger.error("[{}] save_raw_output: {}", self.tool_name, msg)
            raise ValueError(msg) from None

        self.output_directory.mkdir(parents=True, exist_ok=True)
        payload = data if isinstance(data, bytes) else data.encode("utf-8")
        dest.write_bytes(payload)
        logger.info("[{}] Saved raw output -> {}", self.tool_name, dest)
        return dest

    @abstractmethod
    def parse_output(self, output_string: str) -> list[dict[str, Any]]:
        """Turn tool stdout (text or serialized JSON) into structured rows.

        Each implementation defines how to interpret that tool's format.
        """

    async def run_and_parse(
        self,
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        save_raw_filename: str | None = None,
    ) -> tuple[ToolResult, list[dict[str, Any]]]:
        """Run subprocess, optionally save stdout to disk, then parse (if exit ok and not timed out)."""
        result = await self.run_subprocess(args, cwd=cwd, env=env, timeout=timeout)
        if save_raw_filename:
            try:
                self.save_raw_output(result.stdout, save_raw_filename)
            except ValueError:
                raise
            except OSError as exc:
                logger.exception("[{}] save_raw_output failed: {}", self.tool_name, exc)
                raise

        if result.timed_out or result.exit_code != 0:
            return result, []

        try:
            parsed = self.parse_output(result.stdout)
        except Exception:
            logger.exception(
                "[{}] parse_output failed stdout_preview={!r}",
                self.tool_name,
                (result.stdout or "")[:500],
            )
            raise

        return result, parsed


# Spec / README wording ("BaseTool")
BaseTool = AsyncBaseTool
