"""
tools/code_interpreter.py
══════════════════════════════════════════════════════════════════════════════
Code Interpreter Tool

Executes Python snippets via:
  1. AWS Lambda (production) — invokes the standup-code-interpreter Lambda
  2. Local subprocess sandbox (development fallback)

The LLM can use this tool to:
  • Compute sprint velocity / burndown metrics from raw update data
  • Generate charts (returns base64 PNG)
  • Run ad-hoc data analysis on standup history
  • Validate JSON structures
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from config import AWS_REGION, CODE_INTERPRETER_LAMBDA

logger = logging.getLogger(__name__)


def run_code_snippet(code: str, context_data: Optional[dict] = None) -> dict:
    """
    Execute a Python snippet and return stdout, result value, and any charts.

    Args:
        code         : Python source code to execute
        context_data : Optional dict injected as `ctx` variable in the snippet

    Returns:
        {
            "stdout":   "<captured stdout>",
            "result":   <return value of last expression>,
            "chart_b64":"<base64 PNG if matplotlib figure was produced>",
            "error":    "<traceback if execution failed>",
            "backend":  "lambda" | "local",
        }
    """
    # Try Lambda first; fall back to local sandbox
    try:
        return _invoke_lambda(code, context_data)
    except Exception as exc:
        logger.warning("Code Interpreter Lambda failed (%s) — using local sandbox", exc)
        return _run_local(code, context_data)


# ── Lambda backend ────────────────────────────────────────────────────────────

def _invoke_lambda(code: str, context_data: Optional[dict]) -> dict:
    client = boto3.client("lambda", region_name=AWS_REGION)
    payload = json.dumps({
        "code":    code,
        "context": context_data or {},
    })
    resp = client.invoke(
        FunctionName=CODE_INTERPRETER_LAMBDA,
        InvocationType="RequestResponse",
        Payload=payload.encode(),
    )
    if resp.get("FunctionError"):
        raise RuntimeError(f"Lambda function error: {resp['FunctionError']}")

    body = json.loads(resp["Payload"].read())
    body["backend"] = "lambda"
    return body


# ── Local sandbox (subprocess, restricted) ───────────────────────────────────

_SANDBOX_WRAPPER = '''
import sys, json, io, traceback, base64, contextlib

context_data = {context_json}

# Inject ctx variable
ctx = context_data

_output = io.StringIO()
_result = None
_chart_b64 = ""
_error = ""

try:
    with contextlib.redirect_stdout(_output):
        exec(compile({code_repr}, "<snippet>", "exec"), {{"ctx": ctx}})
except Exception:
    _error = traceback.format_exc()

# Check if matplotlib produced a figure
try:
    import matplotlib.pyplot as plt
    figs = [plt.figure(i) for i in plt.get_fignums()]
    if figs:
        import io as _io
        buf = _io.BytesIO()
        figs[-1].savefig(buf, format="png", bbox_inches="tight")
        _chart_b64 = base64.b64encode(buf.getvalue()).decode()
        plt.close("all")
except Exception:
    pass

print(json.dumps({{
    "stdout":    _output.getvalue(),
    "result":    str(_result) if _result is not None else "",
    "chart_b64": _chart_b64,
    "error":     _error,
    "backend":   "local",
}}))
'''

def _run_local(code: str, context_data: Optional[dict]) -> dict:
    wrapper = _SANDBOX_WRAPPER.format(
        context_json=json.dumps(context_data or {}),
        code_repr=repr(code),
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", wrapper],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return {"stdout": "", "result": "", "chart_b64": "",
                    "error": proc.stderr, "backend": "local"}
        return json.loads(proc.stdout.strip().split("\n")[-1])
    except subprocess.TimeoutExpired:
        return {"stdout": "", "result": "", "chart_b64": "",
                "error": "Execution timed out (30s)", "backend": "local"}
    except Exception as exc:
        return {"stdout": "", "result": "", "chart_b64": "",
                "error": str(exc), "backend": "local"}