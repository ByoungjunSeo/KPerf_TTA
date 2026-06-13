"""K-Perf GuideLLM wrapper — Stage 1 (structural check only).

This module provides:

* :func:`check`     — imports the GuideLLM public symbols K-Perf will rely on
                      and prints their signatures / field lists. No network,
                      no benchmark execution.
* :func:`run_sweep` — Stage-2 skeleton for the concurrent sweep. Argument
                      mapping is left as TODO until the field names produced
                      by :func:`check` are confirmed against the running
                      GuideLLM build.

All imports use paths verified against ``vllm-project/guidellm`` @ ``fb3e862``
(see ``analysis/02_code_map/`` for the static evidence).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kperf.report import render_html

# -- GuideLLM public imports (verified @ fb3e862) -----------------------------
# Source: src/guidellm/backends/__init__.py:__all__
from guidellm.backends import OpenAIHTTPBackend

# Source: src/guidellm/benchmark/__init__.py:__all__
from guidellm.benchmark import (
    BenchmarkGenerativeTextArgs,
    ConcurrentProfile,
    GenerativeBenchmarksReport,
    GenerativeMetrics,
    benchmark_generative_text,
)


def _print_signature(label: str, obj: Any) -> None:
    """Print ``inspect.signature(obj)``, falling back to ``repr`` if unavailable."""
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError) as exc:
        print(f"[{label}] signature unavailable: {exc!r}")
        return
    print(f"[{label}] {obj.__qualname__}{sig}")


def _print_pydantic_fields(label: str, model_cls: Any) -> None:
    """Print pydantic model field names + type annotations + defaults."""
    fields = getattr(model_cls, "model_fields", None)
    if fields is None:
        print(f"[{label}] {model_cls.__qualname__}: not a pydantic v2 model "
              f"(no .model_fields)")
        return
    print(f"[{label}] {model_cls.__qualname__} fields ({len(fields)}):")
    for name, info in fields.items():
        # FieldInfo exposes .annotation and either .default or .default_factory
        ann = getattr(info, "annotation", None)
        default = getattr(info, "default", inspect._empty)
        factory = getattr(info, "default_factory", None)
        if factory is not None:
            default_repr = f"<factory {factory!r}>"
        elif default is inspect._empty:
            default_repr = "<required>"
        else:
            default_repr = repr(default)
        print(f"    - {name}: {ann!r} = {default_repr}")


def _check_field_presence(label: str, model_cls: Any, expected: list[str]) -> None:
    """Report which of ``expected`` field names exist on ``model_cls``."""
    fields = getattr(model_cls, "model_fields", None) or {}
    present = [n for n in expected if n in fields]
    missing = [n for n in expected if n not in fields]
    print(f"[{label}] expected-field presence on {model_cls.__qualname__}:")
    for name in expected:
        mark = "OK " if name in fields else "?? "
        print(f"    {mark}{name}")
    if missing:
        print(f"    (missing: {missing})")
    else:
        print(f"    (all {len(present)} expected fields present)")


def check() -> None:
    """Stage-1 structural sanity check.

    Imports GuideLLM symbols and prints:

    * ``benchmark_generative_text`` signature
    * ``BenchmarkGenerativeTextArgs`` field list + presence of expected fields
    * ``GenerativeMetrics`` field list + presence of expected fields
    * ``OpenAIHTTPBackend``, ``ConcurrentProfile``, ``GenerativeBenchmarksReport``
      class identities (smoke import).

    No network calls, no benchmark execution.
    """
    print("=" * 72)
    print("K-Perf check() — GuideLLM structural verification (no load)")
    print("=" * 72)

    # 1) Smoke imports
    print("\n[imports]")
    for name, obj in [
        ("guidellm.backends.OpenAIHTTPBackend", OpenAIHTTPBackend),
        ("guidellm.benchmark.benchmark_generative_text", benchmark_generative_text),
        ("guidellm.benchmark.BenchmarkGenerativeTextArgs", BenchmarkGenerativeTextArgs),
        ("guidellm.benchmark.ConcurrentProfile", ConcurrentProfile),
        ("guidellm.benchmark.GenerativeMetrics", GenerativeMetrics),
        ("guidellm.benchmark.GenerativeBenchmarksReport", GenerativeBenchmarksReport),
    ]:
        print(f"    {name} -> {obj!r}")

    # 2) benchmark_generative_text signature
    print("\n[signature]")
    _print_signature("benchmark_generative_text", benchmark_generative_text)

    # 3) BenchmarkGenerativeTextArgs fields
    print("\n[args fields]")
    _print_pydantic_fields("BenchmarkGenerativeTextArgs", BenchmarkGenerativeTextArgs)

    # NOTE: The user prompt mentioned `rate_type` as an expected field name.
    # The current fb3e862 source uses `profile` (with `--rate-type` as a CLI
    # alias on the `run` command, not a model field). We list what the prompt
    # asked about *and* what the source actually defines, so the diff is
    # visible from the check() output rather than hidden by renaming.
    print("\n[args expected-field check] (per prompt)")
    _check_field_presence(
        "BenchmarkGenerativeTextArgs (prompt list)",
        BenchmarkGenerativeTextArgs,
        ["target", "data", "rate_type", "rate", "max_seconds"],
    )

    # 4) GenerativeMetrics fields
    print("\n[metrics fields]")
    _print_pydantic_fields("GenerativeMetrics", GenerativeMetrics)
    print("\n[metrics expected-field check] (per prompt)")
    _check_field_presence(
        "GenerativeMetrics (prompt list)",
        GenerativeMetrics,
        [
            "time_to_first_token_ms",
            "time_per_output_token_ms",
            "request_latency",
            "tokens_per_second",
            "output_tokens_per_second",
        ],
    )

    # 5) ConcurrentProfile / GenerativeBenchmarksReport / OpenAIHTTPBackend
    #    quick signature peek for awareness, no construction.
    print("\n[other class signatures]")
    _print_signature("ConcurrentProfile.__init__", ConcurrentProfile)
    _print_signature("OpenAIHTTPBackend.__init__", OpenAIHTTPBackend)

    print("\n[done] check() complete — no load was generated.")


# -- Metric extraction paths (confirmed @ fb3e862) ----------------------------
# GenerativeMetrics.<metric> is a StatusDistributionSummary, which is a
#   StatusBreakdown[DistributionSummary x4]  -> .successful / .incomplete /
#   .errored / .total   (src/guidellm/schemas/base.py:215, statistics.py:637)
# Each DistributionSummary has .mean and .percentiles (Percentiles), and
#   Percentiles has .p50/.p90/.p99/...   (statistics.py:114, :32)
# So the P90 of successful TTFT is:
#     b.metrics.time_to_first_token_ms.successful.percentiles.p90
#
# Units (src/guidellm/schemas/request_stats.py):
#   time_to_first_token_ms / time_per_output_token_ms / inter_token_latency_ms
#       -> already multiplied by 1000  => MILLISECONDS
#   request_latency (E2EL) -> end - start, docstring "in seconds"  => SECONDS
#       => K-Perf converts E2EL to ms by *1000 (constant below).
#
# The concurrency (streams) for each benchmark step lives on the scheduling
# strategy: b.config.strategy.streams  (ConcurrentStrategy, strategies.py:308).
#
# All paths/units verified at runtime by run_sweep() before being trusted.

_E2EL_SECONDS_TO_MS = 1000.0
_NA = "N/A"


def _pcts(metrics: Any, attr: str, scale: float = 1.0) -> dict[str, Any]:
    """Return {p50, p90, p99} of the *successful* distribution for ``attr``.

    ``scale`` multiplies each value (used to convert E2EL seconds -> ms).
    Returns ``N/A`` placeholders if the metric or path is absent (no guessing).
    """
    summary = getattr(metrics, attr, None)
    if summary is None:
        return {"p50": _NA, "p90": _NA, "p99": _NA}
    try:
        p = summary.successful.percentiles
        return {"p50": p.p50 * scale, "p90": p.p90 * scale, "p99": p.p99 * scale}
    except AttributeError:
        return {"p50": _NA, "p90": _NA, "p99": _NA}


def _mean(metrics: Any, attr: str) -> Any:
    """Return the mean of the *successful* distribution, or ``N/A``."""
    summary = getattr(metrics, attr, None)
    try:
        return summary.successful.mean
    except AttributeError:
        return _NA


def _percentile(metrics: Any, attr: str, name: str = "p90") -> Any:
    """Return a single named percentile of the *successful* distribution, or N/A."""
    summary = getattr(metrics, attr, None)
    try:
        return getattr(summary.successful.percentiles, name)
    except AttributeError:
        return _NA


def extract_metrics(benchmark: Any) -> dict[str, Any]:
    """Extract one concurrency step's metrics from a GenerativeBenchmark.

    All access paths and units were confirmed at runtime (see module docstring).
    Missing fields are reported as ``"N/A"`` rather than guessed.
    """
    metrics = benchmark.metrics
    totals = metrics.request_totals
    strategy = benchmark.config.strategy

    return {
        # concurrency level for this step (ConcurrentStrategy.streams)
        "concurrency": getattr(strategy, "streams", _NA),
        "requests": {
            "successful": totals.successful,
            "errored": totals.errored,
            "incomplete": totals.incomplete,
            "total": totals.total,
        },
        # already in milliseconds
        "ttft_ms": _pcts(metrics, "time_to_first_token_ms"),
        "tpot_ms": _pcts(metrics, "time_per_output_token_ms"),
        "itl_ms": _pcts(metrics, "inter_token_latency_ms"),  # reference only
        # request_latency is in SECONDS -> convert to ms
        "e2el_ms": _pcts(metrics, "request_latency", scale=_E2EL_SECONDS_TO_MS),
        # system throughput (tokens/s)
        "throughput_tok_s": _mean(metrics, "tokens_per_second"),
        "throughput_tok_s_p90": _percentile(metrics, "tokens_per_second", "p90"),
        # per-user output rate (tokens/s)
        "tps_per_user_tok_s": _mean(metrics, "output_tokens_per_second"),
    }


def _confirm_units_at_runtime(benchmark: Any) -> None:
    """Print raw vs *1000 E2EL and a TTFT sample to confirm units empirically."""
    m = benchmark.metrics
    rl_p50_raw = m.request_latency.successful.percentiles.p50
    ttft_p50 = m.time_to_first_token_ms.successful.percentiles.p50
    print("[units] runtime confirmation (concurrency="
          f"{getattr(benchmark.config.strategy, 'streams', '?')}):")
    print(f"    request_latency.successful.p50 (raw)   = {rl_p50_raw}")
    print(f"    request_latency.successful.p50 * 1000  = {rl_p50_raw * 1000.0}")
    print(f"    time_to_first_token_ms.successful.p50  = {ttft_p50}")
    print("    => request_latency raw is ~O(0.1-1.0); TTFT(ms) is ~O(10).")
    print("    => request_latency is SECONDS; E2EL stored as *1000 (ms).")


def _print_table(rows: list[dict[str, Any]]) -> None:
    """Print the per-concurrency summary table."""
    def fmt(v: Any) -> str:
        return f"{v:.2f}" if isinstance(v, (int, float)) else str(v)

    header = (f"{'conc':>4} | {'TTFT P90 (ms)':>13} | {'TPOT P90 (ms)':>13} | "
              f"{'E2EL P90 (ms)':>13} | {'thrpt (tok/s)':>13} | {'succ/fail':>10}")
    print("\n" + header)
    print("-" * len(header))
    for r in rows:
        succ = r["requests"]["successful"]
        fail = r["requests"]["errored"]
        print(f"{fmt(r['concurrency']):>4} | "
              f"{fmt(r['ttft_ms']['p90']):>13} | "
              f"{fmt(r['tpot_ms']['p90']):>13} | "
              f"{fmt(r['e2el_ms']['p90']):>13} | "
              f"{fmt(r['throughput_tok_s']):>13} | "
              f"{f'{succ}/{fail}':>10}")


_GUIDELLM_PINNED_COMMIT = "fb3e862"  # submodule pin (fallback if git unavailable)
_MODEL_FETCH_FAILED = "(자동취득 실패)"


def _fetch_model_id(target: str) -> str | None:
    """GET ``{target}/v1/models`` and return the first served model id.

    Uses httpx (already a guidellm dependency). Returns ``None`` on any failure
    so the caller can record the failure explicitly rather than guess a name.
    """
    import httpx

    url = target.rstrip("/").removesuffix("/v1") + "/v1/models"
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        model_id = data[0].get("id") if data else None
        return model_id or None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, AttributeError):
        return None


def _guidellm_commit() -> str:
    """Best-effort short commit of the guidellm submodule; fallback to the pin.

    Avoids hardcoding by asking git at runtime; if git/the submodule path is
    unavailable, returns the known pinned commit explicitly.
    """
    submodule = Path(__file__).resolve().parent.parent / "external" / "guidellm"
    try:
        out = subprocess.run(
            ["git", "-C", str(submodule), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        commit = out.stdout.strip()
        return commit or _GUIDELLM_PINNED_COMMIT
    except (subprocess.SubprocessError, OSError):
        return _GUIDELLM_PINNED_COMMIT


async def run_sweep(
    rates: list[float] | None = None,
    max_seconds: int = 10,
    output_path: str | Path = "kperf_result.json",
    report_path: str | Path = "kperf_report.html",
    target: str = "http://localhost:8000",
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Stage 2-B / 3-B: concurrent sweep + K-Perf HTML report.

    Runs ``profile="concurrent"`` over ``rates`` (default ``[1, 2, 4, 8]``), each
    step capped at ``max_seconds``. Extracts per-concurrency distributions via
    :func:`extract_metrics`, prints a summary table, writes the collected
    ``list[dict]`` to ``output_path`` (the report generator's input), then
    renders a self-contained HTML report to ``report_path`` via
    :func:`kperf.report.render_html`.

    GuideLLM's own json/csv/html file outputs are disabled (``outputs=[]``);
    only our ``kperf_result.json`` + ``kperf_report.html`` are written.

    Returns the collected ``list[dict]``.
    """
    rates = rates if rates is not None else [1, 2, 4, 8]
    profile = "concurrent"

    # Model id for the report: manual override, else auto from /v1/models.
    if model is not None:
        report_model = model
        model_source = "manual"
        backend_model = model
    else:
        fetched = _fetch_model_id(target)
        report_model = fetched if fetched else _MODEL_FETCH_FAILED
        model_source = "auto(/v1/models)"
        backend_model = ""  # empty -> backend uses server's first model
        print(f"[model] /v1/models -> {report_model}")

    args = BenchmarkGenerativeTextArgs(
        data=[{"kind": "synthetic_text", "prompt_tokens": 128, "output_tokens": 128}],
        profile=profile,
        rate=rates,
        backend_kwargs={
            "kind": "openai_http",
            "target": target,
            "model": backend_model,
        },
        data_column_mapper={"kind": "generative_column_mapper"},
        data_preprocessors=[{"kind": "encode_media"}],
        data_finalizer={"kind": "generative"},
        data_loader={"kind": "pytorch"},
        max_seconds=max_seconds,
        outputs=[],  # suppress GuideLLM's default json/csv; we write our own
    )

    print("=" * 72)
    print(f"K-Perf run_sweep() — Stage 2-B (concurrent sweep rate={rates}, "
          f"max_seconds={max_seconds})")
    print("=" * 72)
    print("[run] calling benchmark_generative_text(args) ...")

    report, _extra = await benchmark_generative_text(args)

    print(f"\n[result] len(report.benchmarks) = {len(report.benchmarks)}")
    if not report.benchmarks:
        print("[result] no benchmarks produced — aborting.")
        return []

    # Confirm E2EL unit empirically on the first step before trusting extraction.
    _confirm_units_at_runtime(report.benchmarks[0])

    rows = [extract_metrics(b) for b in report.benchmarks]

    _print_table(rows)

    out = Path(output_path)
    out.write_text(json.dumps(rows, indent=2))
    print(f"\n[save] wrote {len(rows)} concurrency steps -> {out.resolve()}")
    print(f"[save] top-level keys per row: {list(rows[0].keys())}")

    # --- K-Perf HTML report (Stage 3-B) -------------------------------------
    meta = {
        "model": report_model,
        "model_source": model_source,
        "target": target,
        "profile": profile,
        "rate": rates,
        "max_seconds": max_seconds,
        "scenario": f"synthetic_text prompt=128 / output=128 ({profile})",
        "measured_at": datetime.now(timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S %Z"),
        "guidellm_commit": _guidellm_commit(),
    }
    report_out = render_html(rows, meta, str(report_path))
    print(f"[save] wrote HTML report -> {Path(report_out).resolve()}")

    print("\n[done] run_sweep() complete.")
    return rows


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        asyncio.run(run_sweep())
    else:
        check()
