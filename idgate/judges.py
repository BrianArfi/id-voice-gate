"""Judge backends. Each one takes a prompt and returns the parsed JSON verdict.

    claude-cli     (default) headless Claude Code: `claude -p`, prompt on stdin.
                   Binary from CLAUDE_BIN, else `claude` on PATH. Uses your Claude
                   login, so no API key. Tools are disabled: pure text generation.
    anthropic-api  Anthropic Messages API over plain urllib. Needs ANTHROPIC_API_KEY.
                   No SDK required.
    command        Any shell command. Gets the prompt on stdin, prints the JSON verdict.
                   Set it with IDGATE_JUDGE_CMD or --judge-cmd. Use it for a local model,
                   another provider, or a proxy.
    none           No judge: lint only. Clearly labelled as such in every result.

Every failure raises JudgeUnavailable. The caller treats that as NOT passed.
"""
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

BACKENDS = ("claude-cli", "anthropic-api", "command", "none")

DEFAULT_MODELS = {
    "claude-cli": "sonnet",
    "anthropic-api": "claude-sonnet-5",
    "command": "",
    "none": "",
}


def anthropic_url():
    return (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/") + "/v1/messages"


ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = int(os.environ.get("IDGATE_MAX_TOKENS") or 8192)

# Text generation only: the judge never needs a tool.
CLAUDE_DISALLOWED_TOOLS = ["Bash", "Read", "Edit", "Write", "NotebookEdit", "Task",
                           "WebFetch", "WebSearch", "Glob", "Grep"]
JSON_SUFFIX = "\n\nReply with ONLY valid JSON, no other text, no code fence."


class JudgeUnavailable(Exception):
    pass


def model_for(backend, override=""):
    return override or DEFAULT_MODELS.get(backend, "")


def claude_bin():
    """CLAUDE_BIN wins, then `claude` on PATH. None when neither exists."""
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    return shutil.which("claude")


def parse_json(text):
    """Parse a JSON object from model output. Tolerates a code fence or chatter around it."""
    if not text or not text.strip():
        raise ValueError("empty output")
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s[:4].lower() == "json":
            s = s[4:].strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        return json.loads(s[start:end + 1])
    raise ValueError("no JSON object in output: %r" % s[:200])


def _with_retry(send, prompt):
    """One retry when the answer is not valid JSON."""
    last = None
    for attempt in range(2):
        out = send(prompt if attempt == 0 else prompt + JSON_SUFFIX)
        try:
            return parse_json(out)
        except ValueError as e:
            last = e
    raise JudgeUnavailable("judge did not return valid JSON: %s" % last)


def _claude_cli(prompt, model, timeout):
    exe = claude_bin()
    if not exe:
        raise JudgeUnavailable("claude CLI not found: install Claude Code, or set CLAUDE_BIN")
    cmd = [exe, "-p", "--output-format", "text", "--disallowed-tools", *CLAUDE_DISALLOWED_TOOLS]
    if model:
        cmd += ["--model", model]

    def send(p):
        try:
            # A neutral working directory, so a project's CLAUDE.md does not leak into the judge.
            proc = subprocess.run(cmd, input=p, capture_output=True, text=True, encoding="utf-8",
                                  timeout=timeout, cwd=tempfile.gettempdir())
        except subprocess.TimeoutExpired:
            raise JudgeUnavailable("claude CLI timed out after %ss" % timeout)
        except OSError as e:
            raise JudgeUnavailable("claude CLI could not start: %s" % e)
        if proc.returncode != 0:
            raise JudgeUnavailable("claude CLI exit %s: %s" % (
                proc.returncode, (proc.stderr or proc.stdout or "").strip()[:300]))
        return proc.stdout or ""
    return _with_retry(send, prompt + JSON_SUFFIX)


def _anthropic_api(prompt, model, timeout):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise JudgeUnavailable("ANTHROPIC_API_KEY is not set")

    def send(p):
        body = json.dumps({"model": model, "max_tokens": MAX_TOKENS,
                           "messages": [{"role": "user", "content": p}]}).encode("utf-8")
        req = urllib.request.Request(anthropic_url(), data=body, method="POST", headers={
            "x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise JudgeUnavailable("Anthropic API HTTP %s: %s" % (e.code, detail))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise JudgeUnavailable("Anthropic API unreachable: %s" % e)
        parts = [c.get("text", "") for c in data.get("content") or [] if c.get("type") == "text"]
        return "".join(parts)
    return _with_retry(send, prompt + JSON_SUFFIX)


def _command(prompt, command, timeout):
    if not command:
        raise JudgeUnavailable("judge 'command' needs IDGATE_JUDGE_CMD or --judge-cmd")

    def send(p):
        try:
            proc = subprocess.run(command, shell=True, input=p, capture_output=True, text=True,
                                  encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise JudgeUnavailable("judge command timed out after %ss" % timeout)
        if proc.returncode != 0:
            raise JudgeUnavailable("judge command exit %s: %s" % (
                proc.returncode, (proc.stderr or "").strip()[:300]))
        return proc.stdout or ""
    return _with_retry(send, prompt + JSON_SUFFIX)


def call(backend, prompt, model="", timeout=240, command=""):
    """Run the prompt through one backend. -> parsed JSON. Raises JudgeUnavailable."""
    if backend == "claude-cli":
        return _claude_cli(prompt, model, timeout)
    if backend == "anthropic-api":
        return _anthropic_api(prompt, model or DEFAULT_MODELS["anthropic-api"], timeout)
    if backend == "command":
        return _command(prompt, command, timeout)
    if backend == "none":
        raise JudgeUnavailable("judge backend is 'none' (lint only)")
    raise JudgeUnavailable("unknown judge backend %r (use one of %s)" % (backend, ", ".join(BACKENDS)))
