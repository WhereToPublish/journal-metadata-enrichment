"""mistral_runtime.py — Mistral Agents SDK runtime for journal research.

Replaces the OMP swarm run for one batch: Python itself fans out one
`mistralai.agents` agent run per journal (bounded by a semaphore), so no
orchestrator agent is needed.  Each per-journal agent:

- receives the journal input payload inlined in its user message,
- follows the research protocol in `agent/prompt/subagent.md` plus a short
  runtime-adaptation section (no file transport),
- fetches DOAJ / OpenAlex / CrossRef and other pages through host-side tools
  (`fetch_url`, `web_search`) run by this Python process,
- returns its result as a **Pydantic-validated structured output**
  (`output_type=AgentResult` from the shared `agent_result.py` module — the
  same contract the OMP runtime's result files are validated against).

The runner converts every structured answer into the same
`results/<idx>-<slug>.json` file contract that `run_enrichment.py` already
collects, so validation, deduplication and persistence are unchanged.
Failed or timed-out journals get an explicit `unresolved` result file — with
this runtime nothing keeps running after the process exits.
"""

from __future__ import annotations

import asyncio
import html as html_module
import http.client
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


from mistralai import agents
from mistralai.agents import Busy, Completed, Failed, InvalidOutput, ToolError

from agent_result import AgentResult, unresolved_result, write_result_file
from enrichment_common import SUBAGENT_PROTOCOL_PATH, SwarmRunResult, slugify

# ---------------------------------------------------------------------------
# Configuration (env overrides; the smallest/cheapest model by default)
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("JOURNALMIND_MISTRAL_MODEL", "ministral-3b-latest")
DEFAULT_CONCURRENCY = int(os.environ.get("JOURNALMIND_MISTRAL_CONCURRENCY", "4"))
DEFAULT_PER_JOURNAL_TIMEOUT = float(os.environ.get("JOURNALMIND_MISTRAL_TIMEOUT_SECONDS", "600"))
DEFAULT_MAX_ITERATIONS = int(os.environ.get("JOURNALMIND_MISTRAL_MAX_ITERATIONS", "16"))

FETCH_TIMEOUT_SECONDS = 20.0
MAX_DOWNLOAD_BYTES = 600_000  # raw cap per fetch; HTML is stripped, then truncated
MAX_RESPONSE_CHARS = 60_000  # what the model actually sees; keeps credits small
SEARCH_RESULT_LIMIT = 8
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

_RUNTIME_ADAPTATION = """

## Runtime Adaptation — Mistral Agents SDK (takes precedence over the file transport)

- Your journal input is given INLINE in this conversation as a JSON object with
  the same keys as the input file (`journal`, `known_metadata`, `requested_gaps`,
  `api_lookup_urls`). Do NOT read or write any file: you have no file access.
- Fetch URLs with the `fetch_url` tool (never with curl or any shell command;
  you have no shell). `fetch_url` returns HTML pages as readable text and
  non-HTML responses (the JSON APIs) as-is.
- Use the `api_lookup_urls` first, exactly as the protocol says.
- When the public APIs do not resolve a gap and you need to discover other
  pages, use the `web_search` tool (DuckDuckGo; returns title/url/snippet
  results) and then `fetch_url` on a promising result.
- Do NOT write a result file. Give your final answer as the structured result
  required by the harness: one object with `journal` (the exact journal name
  from the input), `suggestions` (only the fields listed in `requested_gaps`),
  `status` ("ok" when at least one suggestion survives the rules, otherwise
  "unresolved"), and optional `notes`. Follow every research, evidence and
  confidence rule of the protocol exactly.
"""


def build_instructions() -> str:
    """Research protocol (subagent.md) + the runtime adaptation section."""
    protocol = SUBAGENT_PROTOCOL_PATH.read_text(encoding="utf-8")
    return protocol + _RUNTIME_ADAPTATION


def build_user_prompt(input_payload: dict) -> str:
    return (
        "Research exactly one academic journal. Your input is the JSON below.\n"
        "Follow the research protocol in your instructions exactly "
        "(public APIs first via fetch_url, evidence rules, strict per-field "
        "rules, confidence guidance), then give your final structured answer.\n\n"
        "```json\n" + json.dumps(input_payload, ensure_ascii=False) + "\n```"
    )


# ---------------------------------------------------------------------------
# Host-side tools (run by this Python process when the model calls them)
# ---------------------------------------------------------------------------


def _http_get(url: str) -> str:
    """GET a URL with a browser User-Agent; return the response text."""
    url = _normalize_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS, context=context) as response:
        body = response.read(MAX_DOWNLOAD_BYTES + 1).decode("utf-8", errors="replace")
    return body[:MAX_DOWNLOAD_BYTES]


def _normalize_url(url: str) -> str:
    """Percent-encode spaces and other forbidden characters in the URL path/query."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Not an absolute HTTP(S) URL: {url!r}")
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%:@&=+$,~*'(!)")
    query = urllib.parse.quote(parts.query, safe="&=+$,?/:@,~*'(!)")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def looks_like_html(body: str, url: str) -> bool:
    lowered = body[:2048].lower()
    if "<html" in lowered or "<!doctype html" in lowered:
        return True
    return bool(re.search(r"\.(x?html?|php)(\?|$)", url, re.I))


def _html_to_text(page: str) -> str:
    """Strip an HTML page down to readable text to keep tool results small."""
    page = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1\s*>", " ", page)
    page = re.sub(r"(?is)<!--.*?-->", " ", page)
    page = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|br|td)>", "\n", page)
    page = re.sub(r"<[^>]+>", " ", page)
    page = html_module.unescape(page)
    page = re.sub(r"[ \t]+", " ", page)
    page = re.sub(r"\n\s*\n+", "\n", page)
    return page.strip()[:MAX_RESPONSE_CHARS]


@agents.tool
def fetch_url(url: str) -> str:
    """Fetch a public web page or API URL and return its content as text.

    HTML pages are stripped to readable text; JSON and other non-HTML
    responses are returned as-is. Responses are truncated to 60,000
    characters. Use this for the DOAJ / OpenAlex / CrossRef API lookup URLs
    and for any public journal or publisher page. Publisher sites sometimes
    block automated fetches (403) — treat that as an unusable source, never
    guess.
    """
    try:
        body = _http_get(url)
    except (urllib.error.HTTPError, urllib.error.URLError, http.client.HTTPException,
            ValueError, TimeoutError, OSError) as exc:
        raise ToolError(f"fetch_url failed for {url}: {exc}")
    if looks_like_html(body, url):
        return _html_to_text(body)
    return body[:MAX_RESPONSE_CHARS]


def _ddg_results(page: str) -> list[dict[str, str]]:
    """Parse the DuckDuckGo lite results table into title/url/snippet dicts.

    The lite markup has one or more `result-link` anchors followed by a single
    `result-snippet` cell per organic result; only the first anchor after the
    previous snippet is the canonical result link.
    """
    tokens: list[tuple[int, str, dict[str, str]]] = []
    anchor_pattern = (
        r"(?is)<a\b(?=[^>]*class=[\"']result-link[\"'])[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>"
    )
    for match in re.finditer(anchor_pattern, page):
        link = html_module.unescape(match.group(1))
        title = html_module.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
        tokens.append((match.start(), "A", {"title": title, "url": link, "snippet": ""}))
    snippet_pattern = r"(?is)<td[^>]*class=[\"']result-snippet[\"'][^>]*>(.*?)</td>"
    for match in re.finditer(snippet_pattern, page):
        snippet = html_module.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).strip()
        snippet = re.sub(r"\s+", " ", snippet)
        tokens.append((match.start(), "S", {"snippet": snippet}))

    results: list[dict[str, str]] = []
    pending: dict[str, str] | None = None
    for _, kind, payload in sorted(tokens, key=lambda t: t[0]):
        if kind == "A":
            if pending is None:
                pending = payload  # first anchor of a new organic result
        else:
            if pending is not None:
                # DuckDuckGo lite wraps outbound links in a redirect.
                link = pending["url"]
                if "duckduckgo.com/l/" in link or link.startswith("/l/?"):
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(
                        "https:" + link if link.startswith("//") else link
                    ).query)
                    link = parsed.get("uddg", [link])[0]
                pending["url"] = link
                pending["snippet"] = payload["snippet"]
                results.append(pending)
                pending = None
    return results


@agents.tool
def web_search(query: str) -> str:
    """Search the public web (DuckDuckGo) and return up to 8 results.

    Returns a JSON list of {title, url, snippet} objects. Use it only when
    the public APIs do not resolve a gap; then fetch_url a promising result.
    """
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query)
    try:
        page = _http_get(url)
    except (urllib.error.HTTPError, urllib.error.URLError, http.client.HTTPException,
            ValueError, TimeoutError, OSError) as exc:
        raise ToolError(f"web_search failed for {query!r}: {exc}")

    results = _ddg_results(page)[:SEARCH_RESULT_LIMIT]
    if not results:
        raise ToolError(f"web_search returned no results for {query!r}: try different keywords.")
    return json.dumps(results, ensure_ascii=False)


# ---------------------------------------------------------------------------
# The batch runner
# ---------------------------------------------------------------------------


class MistralSwarmRunner:
    """Fans out one Agents SDK agent run per journal, concurrency-bounded."""

    def __init__(
        self,
        log_dir: Path,
        batch_timeout_seconds: float,
        model: str | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        per_journal_timeout: float = DEFAULT_PER_JOURNAL_TIMEOUT,
    ) -> None:
        if not os.environ.get("MISTRAL_API_KEY"):
            raise RuntimeError("MISTRAL_API_KEY is not set — get one from the Mistral Studio console.")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.model = model or DEFAULT_MODEL
        self.concurrency = concurrency
        self.per_journal_timeout = per_journal_timeout
        self.batch_timeout_seconds = batch_timeout_seconds
        self.agent = agents.Agent(
            model=self.model,
            name="journalmind-researcher",
            instructions=build_instructions(),
            tools=[fetch_url, web_search],
            output_type=AgentResult,
            max_iterations=DEFAULT_MAX_ITERATIONS,
        )
        self.stats = {"ok": 0, "unresolved": 0, "error": 0, "retry": 0}

    # -- per-journal research ---------------------------------------------

    async def _research_one(self, input_payload: dict) -> AgentResult:
        """Run the agent for one journal, with one schema-failure retry."""
        journal = input_payload["journal"]
        content = build_user_prompt(input_payload)

        state = await self.agent.run(content, timeout=self.per_journal_timeout)
        if isinstance(state, Busy):
            return unresolved_result(journal, "Agent session was busy.")
        if isinstance(state, Failed):
            return unresolved_result(
                journal, f"Agent run failed: {state.error.code}: {state.error.message}"
            )

        if isinstance(state, Completed):
            if isinstance(state.output, AgentResult):
                return state.output
            # InvalidOutput or None: retry once with a compact reminder.
            self.stats["retry"] += 1
            reason = "the turn ended without a final answer"
            if isinstance(state.output, InvalidOutput):
                reason = f"the previous answer did not match the required schema: {state.output.error}"
            reminder = (
                content
                + f"\n\nYour previous final answer was invalid: {reason}. "
                "Reply again with an answer that conforms exactly to the structured "
                "result schema (journal, suggestions[], status, notes)."
            )
            retry_state = await self.agent.run(reminder, timeout=self.per_journal_timeout)
            if isinstance(retry_state, Completed) and isinstance(retry_state.output, AgentResult):
                return retry_state.output
            return unresolved_result(journal, f"structured output failed twice: {reason}")

        return unresolved_result(journal, f"unexpected turn state: {state!r}")

    # -- batch ---------------------------------------------------------------

    async def _run_batch(self, session_id: str, mission_dir: Path,
                         journals: list[dict[str, Any]]) -> SwarmRunResult:
        results_dir = mission_dir / "results"
        semaphore = asyncio.Semaphore(self.concurrency)
        log_lock = asyncio.Lock()

        async def one(journal_gap: dict[str, Any]) -> None:
            stem = f"{journal_gap['_index']:02d}-{journal_gap['_slug']}"
            input_payload = json.loads((mission_dir / "inputs" / f"{stem}.json").read_text(encoding="utf-8"))
            started = time.monotonic()
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        self._research_one(input_payload), timeout=self.per_journal_timeout
                    )
                except asyncio.TimeoutError:
                    result = unresolved_result(
                        journal_gap["name"], f"timed out after {self.per_journal_timeout:.0f}s"
                    )
                except Exception as exc:  # agent-level failure: explicit unresolved result
                    result = unresolved_result(
                        journal_gap["name"], f"agent error: {exc.__class__.__name__}: {exc}"
                    )
            self.stats[result.status if result.status in {"ok", "unresolved"} else "error"] += 1
            write_result_file(results_dir / f"{stem}.json", result)
            elapsed = time.monotonic() - started
            async with log_lock:
                with (self.log_dir / f"{session_id}.mistral.log").open("a", encoding="utf-8") as log:
                    log.write(
                        f"[{session_id}] {journal_gap['name']}: status={result.status} "
                        f"suggestions={len(result.suggestions)} ({elapsed:.1f}s)"
                        f"{' — ' + result.notes if result.notes else ''}\n"
                    )

        async with asyncio.TaskGroup() as group:
            for journal_gap in journals:
                group.create_task(one(journal_gap))

        done = sum(
            1 for j in journals
            if (results_dir / f"{j['_index']:02d}-{j['_slug']}.json").exists()
        )
        final_message = (
            f"DONE {done}/{len(journals)} — {self.stats['ok']} ok, "
            f"{self.stats['unresolved']} unresolved, {self.stats['error']} error, "
            f"{self.stats['retry']} retry(ies)"
        )
        self.stats = {"ok": 0, "unresolved": 0, "error": 0, "retry": 0}
        return SwarmRunResult(session_id=session_id, final_message=final_message)

    @staticmethod
    def _indexed(journals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed: list[dict[str, Any]] = []
        for index, journal_gap in enumerate(journals):
            entry = dict(journal_gap)
            entry["_index"] = index
            entry["_slug"] = slugify(journal_gap["name"])
            indexed.append(entry)
        return indexed

    async def run_async(self, session_id: str, mission_dir: Path,
                        journals: list[dict[str, Any]]) -> SwarmRunResult:
        """Write one result file per journal; enforce the overall batch timeout.

        Async so it can run inside the Mistral Studio workflow worker's event
        loop (activities) as well as standalone.
        """
        indexed = self._indexed(journals)
        try:
            return await asyncio.wait_for(
                self._run_batch(session_id, mission_dir, indexed),
                timeout=self.batch_timeout_seconds,
            )
        except TimeoutError:
            return SwarmRunResult(
                session_id=session_id,
                timed_out=True,
                final_message=(
                    f"TIMEOUT {self.batch_timeout_seconds:.0f}s — collected the result files "
                    "that finished; the rest were left for the late-result sweep and the next run."
                ),
            )

    def run(self, session_id: str, mission_dir: Path,
            journals: list[dict[str, Any]]) -> SwarmRunResult:
        """Sync wrapper (CLI use): write one result file per journal per batch."""
        return asyncio.run(self.run_async(session_id, mission_dir, journals))
