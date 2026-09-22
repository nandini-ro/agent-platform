"""Garak integration.

Garak is not imported as a library. It runs as a subprocess in its own
virtualenv and reaches the agent over HTTP through the same endpoint any other
API client uses:

    garak (own venv)
      -> generators.rest.RestGenerator
        -> POST /api/agents/{id}/chat
          -> AgentRuntime -> LLM / tools / MCP
            -> response
      -> garak detectors -> report.jsonl -> summarised here

Two reasons for the subprocess boundary rather than an in-process integration:
garak's dependency tree stays entirely out of the backend's, and testing the
agent through its real HTTP surface is what we actually want to measure - the
same path an external caller takes, permission gate included.

Everything here is built on garak's documented `rest` generator and CLI:
https://reference.garak.ai/en/latest/garak.generators.rest.html
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Small, fast default for a POC: a handful of well-known jailbreak prompts.
# Override per run. `garak --list_probes` shows everything available.
DEFAULT_PROBES = "probes.dan.Dan_11_0"

# Curated shortlist for the UI, with what each one actually tests.
PROBE_PRESETS: list[dict[str, str]] = [
    {
        "id": "probes.dan.Dan_11_0",
        "label": "DAN 11.0 jailbreak",
        "description": "A single well-known 'do anything now' jailbreak prompt. Fastest smoke test.",
    },
    {
        "id": "probes.dan.DanInTheWild",
        "label": "DAN in the wild",
        "description": "A large corpus of real-world jailbreaks. Thorough but slow.",
    },
    {
        "id": "probes.promptinject.HijackHateHumans",
        "label": "Prompt injection (hijack)",
        "description": "Tries to hijack the agent into emitting attacker-chosen text.",
    },
    {
        "id": "probes.encoding.InjectBase64",
        "label": "Encoded payload injection",
        "description": "Smuggles instructions past the system prompt using base64.",
    },
    {
        "id": "probes.leakreplay.LiteratureCloze",
        "label": "Training-data leak replay",
        "description": "Probes for verbatim regurgitation of memorised text.",
    },
]


_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def clean_log(text: str, tail_lines: int = 60) -> str:
    """Make garak's console output readable for storage and display.

    Strips ANSI colour codes and keeps only the final segment of each
    carriage-return-overwritten line, which is how its progress bars render.
    """
    lines: list[str] = []
    # split on newlines only - splitlines() would also break on the \r that
    # progress bars use to overwrite, turning each frame into its own line
    for line in text.split("\n"):
        line = _ANSI.sub("", line)
        line = line.split("\r")[-1].rstrip()
        if line:
            lines.append(line)
    return "\n".join(lines[-tail_lines:])


class GarakError(RuntimeError):
    """Garak could not be launched, or the run failed before producing a report."""


@dataclass
class GarakSummary:
    """Flattened view of a garak report, suitable for the UI."""

    total_probes: int = 0
    total_prompts: int = 0
    total_passed: int = 0
    total_failed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    hits: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float | None:
        evaluated = self.total_passed + self.total_failed
        return (self.total_passed / evaluated) if evaluated else None

    def to_dict(self) -> dict:
        return {
            "total_probes": self.total_probes,
            "total_prompts": self.total_prompts,
            "total_passed": self.total_passed,
            "total_failed": self.total_failed,
            "pass_rate": self.pass_rate,
            "results": self.results,
            "hits": self.hits,
        }


class GarakAdapter:
    """Builds garak configuration, runs garak, and reads its report back."""

    def __init__(self) -> None:
        self.settings = get_settings()

    # -- configuration ----------------------------------------------------

    def build_rest_config(self, agent_id: str, base_url: str | None = None) -> dict:
        """The `rest` generator config garak reads via --generator_option_file.

        `$INPUT` is where garak substitutes each attack prompt; `$KEY` is filled
        from the REST_API_KEY environment variable. `response_json_field` points
        at the `response` key of our AgentChatResponse.
        """
        base = (base_url or self.settings.self_base_url).rstrip("/")
        options: dict[str, Any] = {
            "name": f"agent-{agent_id}",
            "uri": f"{base}/api/agents/{agent_id}/chat",
            "method": "post",
            "headers": {"Content-Type": "application/json"},
            "req_template_json_object": {"message": "$INPUT"},
            "response_json": True,
            "response_json_field": "response",
            "request_timeout": int(self.settings.llm_timeout_seconds),
            # Our endpoint returns 200 with an error payload rather than 5xx,
            # so a transient failure does not abort the scan. 429 is still
            # treated as backpressure.
            "ratelimit_codes": [429],
        }
        if self.settings.agent_api_key:
            options["headers"]["X-API-Key"] = "$KEY"
        return {"rest": {"RestGenerator": options}}

    def _run_dir(self, run_id: str) -> Path:
        path = Path(self.settings.garak_report_dir).resolve() / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_run_files(
        self, run_id: str, agent_id: str, base_url: str | None = None
    ) -> tuple[Path, Path, Path]:
        """Write the generator options and a garak config for this run.

        An absolute `reporting.report_dir` is used by garak verbatim, which
        makes the report path deterministic instead of something we have to
        scrape out of garak's stdout.
        """
        run_dir = self._run_dir(run_id)

        generator_file = run_dir / "rest_generator.json"
        generator_file.write_text(
            json.dumps(self.build_rest_config(agent_id, base_url), indent=2)
        )

        config_file = run_dir / "garak_config.yaml"
        config_file.write_text(
            yaml.safe_dump(
                {"reporting": {"report_dir": str(run_dir), "report_prefix": run_id}}
            )
        )

        report_file = run_dir / f"{run_id}.report.jsonl"
        return generator_file, config_file, report_file

    def build_command(
        self,
        run_id: str,
        generator_file: Path,
        config_file: Path,
        probes: str,
        generations: int,
    ) -> list[str]:
        return [
            self.settings.garak_python,
            "-m",
            "garak",
            "--config",
            str(config_file),
            "--target_type",
            "rest",
            "--generator_option_file",
            str(generator_file),
            # --spec is garak's current unified selector. --probes still
            # exists but is deprecated since 0.15.1 and, confusingly, expects
            # UNPREFIXED names ("dan.Dan_11_0"), whereas --spec takes the
            # prefixed tokens ("probes.dan.Dan_11_0") used everywhere else.
            "--spec",
            probes,
            "--generations",
            str(generations),
            "--report_prefix",
            run_id,
        ]

    # -- execution --------------------------------------------------------

    async def run(
        self,
        run_id: str,
        agent_id: str,
        probes: str = DEFAULT_PROBES,
        generations: int = 1,
        base_url: str | None = None,
        timeout: float = 3600.0,
    ) -> tuple[GarakSummary, Path, str]:
        """Run garak to completion and return (summary, report path, log tail)."""
        garak_python = Path(self.settings.garak_python)
        if not garak_python.is_absolute():
            garak_python = Path.cwd() / garak_python
        if not garak_python.exists():
            raise GarakError(
                f"Garak interpreter not found at {garak_python}. Create it with:\n"
                "  python3.12 -m venv .venv-garak && "
                ".venv-garak/bin/pip install -r requirements-garak.txt"
            )

        generator_file, config_file, report_file = self.write_run_files(
            run_id, agent_id, base_url
        )
        command = self.build_command(
            run_id, generator_file, config_file, probes, generations
        )
        command[0] = str(garak_python)

        # Only what garak needs. The API key travels as REST_API_KEY, which is
        # the env var garak's RestGenerator reads for $KEY.
        env = {
            k: os.environ[k]
            for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
            if k in os.environ
        }
        if self.settings.agent_api_key:
            env["REST_API_KEY"] = self.settings.agent_api_key

        logger.info("garak run %s: %s", run_id, " ".join(command))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(Path.cwd()),
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise GarakError(f"Garak run timed out after {timeout}s") from None

        log_tail = clean_log((stdout or b"").decode("utf-8", errors="replace"))

        if not report_file.exists():
            raise GarakError(
                f"Garak exited with code {process.returncode} and wrote no report.\n"
                f"{log_tail}"
            )

        return self.parse_report(report_file), report_file, log_tail

    # -- report parsing ---------------------------------------------------

    def parse_report(self, report_file: Path) -> GarakSummary:
        """Summarise a garak .report.jsonl.

        Garak writes one JSON object per line, each tagged with `entry_type`.
        We read the `eval` records (per probe/detector pass counts) and pull a
        few example failures out of the matching .hitlog.jsonl.
        """
        summary = GarakSummary()
        for line in report_file.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # garak appends a non-JSON HTML digest marker
            if record.get("entry_type") != "eval":
                continue

            passed = int(record.get("passed") or 0)
            total = int(record.get("total_evaluated") or 0)
            failed = total - passed
            summary.total_probes += 1
            summary.total_prompts += total
            summary.total_passed += passed
            summary.total_failed += failed
            summary.results.append(
                {
                    "probe": record.get("probe"),
                    "detector": record.get("detector"),
                    "passed": passed,
                    "failed": failed,
                    "total": total,
                    "pass_rate": (passed / total) if total else None,
                }
            )

        summary.hits = self._parse_hitlog(report_file)
        return summary

    @staticmethod
    def _parse_hitlog(report_file: Path, limit: int = 20) -> list[dict]:
        """Example prompts that defeated the agent, for the results view."""
        hitlog = Path(str(report_file).replace(".report.jsonl", ".hitlog.jsonl"))
        if not hitlog.exists():
            return []
        hits: list[dict] = []
        for line in hitlog.read_text(errors="replace").splitlines():
            if not line.strip() or len(hits) >= limit:
                break
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            hits.append(
                {
                    "probe": record.get("probe"),
                    "detector": record.get("detector"),
                    "score": record.get("score"),
                    "prompt": _truncate(_extract_text(record.get("prompt"))),
                    "output": _truncate(_extract_text(record.get("output"))),
                }
            )
        return hits


def _extract_text(value: Any) -> str:
    """Pull readable text out of garak's Conversation/Message structures.

    A hitlog prompt is a Conversation ({"turns": [{"role", "content": {"text"}}]})
    and an output is a Message ({"text": ...}); anything else is stringified.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "turns" in value and isinstance(value["turns"], list) and value["turns"]:
            content = value["turns"][-1].get("content")
            if isinstance(content, dict) and "text" in content:
                return str(content["text"])
            return str(content)
        if "text" in value:
            return str(value["text"])
    return json.dumps(value, default=str)


def _truncate(value: str, limit: int = 600) -> str:
    return value if len(value) <= limit else value[:limit] + "..."


adapter = GarakAdapter()
