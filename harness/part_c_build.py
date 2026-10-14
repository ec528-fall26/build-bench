"""Local build adapter for Part C integration tests.

Repacking and validation operate on a copy of the agent's worktree.
This module is research infrastructure, never part of the agent ZIP.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import re

from harness.run_case import assemble_case


_STAGE_ORDER = {
    "unknown": -1,
    "configure": 0,
    "build": 1,
    "test": 2,
    "install": 3,
    "packaging": 4,
}


class LocalBuildFeedback:
    """Build candidates using an existing local validator bundle.

    Inject diagnose and result_type from the full agent after loading it.
    This avoids accidentally importing the one-shot agent's src package.
    """

    def __init__(
        self,
        bundle: Path,
        builds_dir: Path,
        *,
        diagnose,
        result_type,
        timeout_seconds: float = 300,
        packaging_image: str = "buildbench-source-tools:part-c",
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")

        self.bundle = bundle.resolve()
        self.builds_dir = builds_dir.resolve()
        self.diagnose = diagnose
        self.result_type = result_type
        self.timeout_seconds = timeout_seconds
        self.packaging_image = packaging_image
        self.build_count = 0

        if not (self.bundle / "run.sh").is_file():
            raise ValueError("The bundle must contain run.sh.")
        if not (self.bundle / "case" / "manifest.json").is_file():
            raise ValueError("The bundle must contain case/manifest.json.")

        self.builds_dir.mkdir(parents=True, exist_ok=True)
        self.environment = os.environ.copy()

        # The organizer's wrapper requires GNU realpath, including on macOS.
        if shutil.which("brew"):
            prefix = subprocess.check_output(
                ["brew", "--prefix", "coreutils"],
                text=True,
                timeout=15,
            ).strip()
            gnu_bin = Path(prefix) / "libexec" / "gnubin"
            if not (gnu_bin / "realpath").is_file():
                raise RuntimeError("GNU realpath was not found.")
            self.environment["PATH"] = (
                str(gnu_bin) + os.pathsep + self.environment.get("PATH", "")
            )

    def _run(self, command, log: Path, deadline: float) -> int:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Local build deadline reached.")

        with log.open("a", encoding="utf-8") as output:
            try:
                completed = subprocess.run(
                    [str(argument) for argument in command],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=self.environment,
                    timeout=remaining,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"Local build command timed out; see {log}"
                ) from exc

        return completed.returncode

    def request_build(self, worktree: Path):
        worktree = worktree.resolve()
        if self.builds_dir.is_relative_to(worktree):
            raise ValueError("Build outputs must be outside the worktree.")

        self.build_count += 1
        build_dir = self.builds_dir / f"build-{self.build_count:03d}"
        build_dir.mkdir()  # Never overwrite an earlier build.
        command_log = build_dir / "commands.log"
        started = time.monotonic()
        deadline = started + self.timeout_seconds

        try:
            copied_work = build_dir / "work"
            shutil.copytree(worktree, copied_work, symlinks=True)

            repack_status = self._run(
                [
                    "docker", "run", "--rm", "--network=none",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{copied_work}:/work",
                    "-w", "/work/input",
                    self.packaging_image,
                    "dpkg-source", "--auto-commit", "-b", ".",
                ],
                command_log,
                deadline,
            )
            if repack_status != 0:
                raise RuntimeError(
                    f"Source repacking failed; see {command_log}"
                )

            candidate_case = build_dir / "case"
            assemble_case(
                self.bundle / "case",
                copied_work,
                candidate_case,
            )

            output_dir = build_dir / "result"
            validator_status = self._run(
                [
                    "bash", self.bundle / "run.sh",
                    "--input", candidate_case,
                    "--output", output_dir,
                ],
                command_log,
                deadline,
            )

            result = json.loads(
                (output_dir / "build-result.json").read_text(
                    encoding="utf-8"
                )
            )
            details = json.loads(
                (output_dir / "build-diagnostics.json").read_text(
                    encoding="utf-8"
                )
            )

            if not details.get("execution", {}).get("reached_real_build"):
                raise RuntimeError(
                    f"Validator did not reach compilation; see {output_dir}"
                )
            if result.get("timed_out"):
                raise RuntimeError(
                    f"Validator timed out; see {output_dir}"
                )

            succeeded = (
                result.get("status") == "succeeded"
                and result.get("artifact_validation_passed") is True
            )
            if succeeded and validator_status != 0:
                raise RuntimeError(
                    f"Validator wrapper failed after building; see {command_log}"
                )

            # Preserve the raw validator log. Part A reads a separate
            # normalized log, so its byte offsets refer to that file.
            normalized_log = output_dir / "build.normalized.log"
            with (
                (output_dir / "build.log").open("rb") as source,
                normalized_log.open("wb") as destination,
            ):
                for line in source:
                    destination.write(
                        re.sub(rb"^\[\s*\d+s\][ \t]?", b"", line)
                    )

            diagnostics = self.diagnose(normalized_log, worktree)

            stage = (
                "packaging"
                if succeeded
                else max(
                    (item.stage for item in diagnostics),
                    key=lambda value: _STAGE_ORDER.get(value, -1),
                    default="unknown",
                )
            )

            return self.result_type(
                succeeded=succeeded,
                stage_reached=stage,
                diagnostics=diagnostics,
                duration_seconds=time.monotonic() - started,
            )

        except Exception as exc:
            (build_dir / "adapter-error.json").write_text(
                json.dumps(
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            raise