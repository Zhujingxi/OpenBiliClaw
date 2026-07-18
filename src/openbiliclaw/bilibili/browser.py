"""Bilibili Browser automation via agent-browser.

Provides browser-based interaction with Bilibili for operations
that the API doesn't support or where visual context is needed.
Uses Vercel's agent-browser CLI: https://github.com/vercel-labs/agent-browser
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


class BrowserCommandError(RuntimeError):
    """Raised when agent-browser returns a failing status."""


class BilibiliBrowser:
    """Browser automation interface using agent-browser.

    This is the secondary access layer, used when:
    - The API doesn't cover a needed operation
    - Visual context (DOM, screenshots) is needed
    - Complex page interactions are required

    Requires agent-browser to be installed:
        npm install -g agent-browser
        agent-browser install
    """

    def __init__(
        self,
        executable: str = "",
        headed: bool = False,
        cookie: str = "",
    ) -> None:
        self._executable = executable or self._find_executable()
        self._headed = headed
        self._cookie = cookie
        self._session_name = f"openbiliclaw-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _find_executable() -> str:
        """Find the agent-browser executable."""
        path = shutil.which("agent-browser")
        if path:
            return path
        return "agent-browser"

    @staticmethod
    def get_install_hint() -> str:
        """Return the official agent-browser installation hint."""
        return (
            "未检测到 agent-browser。请先执行 "
            "`npm install -g agent-browser`，然后执行 "
            "`agent-browser install` 安装浏览器内核。"
        )

    @staticmethod
    def _has_executable(executable: str) -> bool:
        """Check whether the configured executable is available."""
        executable_path = Path(executable)
        if executable_path.is_absolute() or "/" in executable:
            if not (executable_path.exists() and executable_path.is_file()):
                return False
        elif shutil.which(executable) is None:
            return False

        try:
            result = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False

        return result.returncode == 0

    @property
    def is_available(self) -> bool:
        """Check if agent-browser is available."""
        return self._has_executable(self._executable)

    @property
    def executable(self) -> str:
        """Return the resolved executable name or path."""
        return self._executable

    async def _run_command(self, *args: str) -> dict[str, Any]:
        """Execute an agent-browser command and return the result.

        Args:
            *args: Command arguments.

        Returns:
            Parsed JSON output from agent-browser.
        """
        cmd = [self._executable, "--session", self._session_name, *args]
        if self._headed:
            cmd.append("--headed")

        logger.debug("Running agent-browser: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            logger.error("agent-browser error: %s", error_msg)
            command = " ".join(cmd)
            raise BrowserCommandError(f"agent-browser command failed: {command}: {error_msg}")

        try:
            return cast("dict[str, Any]", json.loads(stdout.decode()))
        except json.JSONDecodeError:
            return {"output": stdout.decode()}

    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigate to a URL.

        Args:
            url: Target URL.

        Returns:
            Page info.
        """
        try:
            return await self._run_command("open", url)
        except BrowserCommandError as exc:
            if "ERR_ABORTED" not in str(exc):
                raise
        return await self._run_command("open", url)

    async def get_page_content(self, url: str) -> str:
        """Get the text content of a page.

        Args:
            url: Target URL.

        Returns:
            Page text content.
        """
        await self.navigate(url)
        snapshot = await self._run_command("snapshot", "-i", "--json")
        return self._extract_snapshot_text(snapshot)

    @staticmethod
    def _extract_snapshot_text(result: dict[str, Any]) -> str:
        """Extract visible page text from a snapshot payload."""
        data = result.get("data")
        if isinstance(data, dict):
            snapshot = data.get("snapshot")
            if isinstance(snapshot, str) and snapshot.strip():
                return snapshot
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                return text

        output = result.get("output")
        if isinstance(output, str) and output.strip():
            return output

        raise BrowserCommandError("agent-browser returned no readable snapshot content")

    async def screenshot(self, url: str, output_path: str) -> str:
        """Take a screenshot of a page.

        Args:
            url: Target URL.
            output_path: Where to save the screenshot.

        Returns:
            Path to the saved screenshot.
        """
        result = await self._run_command("screenshot", url, "-o", output_path)
        return str(result.get("output", output_path))

    async def close(self) -> None:
        """Close any active browser sessions."""
        try:
            await self._run_command("close")
        except Exception:
            logger.debug("No active session to close.")
