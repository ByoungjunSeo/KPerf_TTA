"""K-Perf self-contained interactive HTML report generator.

Takes the ``kperf_result.json`` schema (``list[dict]`` produced by
:func:`kperf.runner.run_sweep` / :func:`kperf.runner.extract_metrics`) plus a
``meta`` dict, and renders a single self-contained HTML file with an
MLCommons-style concurrency slider that moves an operating point across the
charts.

Hard constraints (see project notes):

* **No external JS / CSS / CDN / fonts / image URLs** — closed-network friendly.
  Charts are inline ``<svg>``; interactivity is inline vanilla JS; images are
  base64 data URIs. Nothing is fetched at view time.
* **No invented values** — only values present in the input are shown. The
  slider snaps ONLY to measured concurrency levels (no interpolated midpoints);
  the readout always shows measured values. Missing values render as ``"N/A"`` /
  ``"미측정"``; unmeasured panels (power/price) are left explicitly empty.

The SLO thresholds below come from the K-Perf training-doc reference values and
are kept as module-level constants so they are easy to audit / change.
"""

from __future__ import annotations

import base64
import html as _html
import json
from pathlib import Path
from typing import Any

__all__ = ["render_html", "SLO_TTFT_P90_MS", "SLO_TPOT_GRADES_MS"]

_ASSETS = Path(__file__).resolve().parent / "assets"

# -- SLO thresholds (K-Perf training-doc reference values) --------------------
SLO_TTFT_P90_MS: float = 2000.0
SLO_TPOT_GRADES_MS: list[float] = [30.0, 40.0, 50.0]

# -- chart geometry (viewBox; SVGs scale responsively via width:100%) ---------
_CW, _CH = 520, 300
_ML, _MR, _MT, _MB = 64, 16, 58, 46  # left/right/top/bottom margins
_BLUE = "#2563eb"


# -- small formatting helpers -------------------------------------------------
def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(v: Any, digits: int = 2) -> str:
    """Format a number; pass through ``"N/A"`` / non-numbers unchanged."""
    if _is_num(v):
        return f"{v:,.{digits}f}"
    return "N/A" if v is None else str(v)


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


# -- asset loading (base64 data URI; no external URLs) ------------------------
def _png_data_uri(filename: str) -> str | None:
    """Read ``assets/<filename>`` and return a ``data:image/png;base64,...`` URI.

    Returns ``None`` if the file is absent (caller falls back to a placeholder;
    no fabricated logo is drawn).
    """
    path = _ASSETS / filename
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _logo_block(meta: dict[str, Any]) -> str:
    """Header logo: meta override -> assets/tta_logo.png -> dotted placeholder."""
    uri = meta.get("logo_data_uri") or _png_data_uri("tta_logo.png")
    if uri:
        return (
            f'<img class="logo" src="{uri}" alt="TTA 로고" '
            'style="height:44px;width:auto">'
        )
    return '<div class="logo-ph">[ TTA 로고 ]</div>'


# -- inline SVG charts (return markup + per-point pixel geometry for JS) -------
def _svg_head(title: str, better: str) -> list[str]:
    parts = [
        f'<svg viewBox="0 0 {_CW} {_CH}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="{_esc(title)}" '
        'style="width:100%;height:auto;display:block;'
        'background:#fff;border:1px solid #ddd;border-radius:6px">',
        f'<text x="{_ML}" y="20" text-anchor="start" font-size="14" '
        f'font-weight="600" fill="#222">{_esc(title)}</text>',
    ]
    if better:
        parts.append(
            f'<text x="{_CW - _MR}" y="20" text-anchor="end" font-size="11" '
            f'font-weight="600" fill="#16a34a">{_esc(better)}</text>'
        )
    return parts


def _overlay(cid: str, pts: list, default_idx: int, marker: bool) -> str:
    """Highlight ring (+ optional vertical marker line) repositioned by JS."""
    init = pts[default_idx] if (pts and pts[default_idx]) else (pts[0] if pts else None)
    ix, iy = (init[0], init[1]) if init else (-99, -99)
    y0, y1 = _MT, _CH - _MB
    out = ""
    if marker:
        out += (
            f'<line id="{cid}-marker" class="hl-marker" x1="{ix}" y1="{y0}" '
            f'x2="{ix}" y2="{y1}" stroke="{_BLUE}" stroke-width="1.5" '
            'stroke-dasharray="4 3" opacity="0"/>'
        )
    out += (
        f'<circle id="{cid}-ring" class="hl-ring" cx="{ix}" cy="{iy}" r="7" '
        f'fill="none" stroke="{_BLUE}" stroke-width="3" opacity="0"/>'
    )
    return out


def _cat_chart(
    cid: str,
    title: str,
    better: str,
    x_labels: list[str],
    color: str,
    y_values: list[Any],
    y_unit: str,
    default_idx: int,
) -> tuple[str, list]:
    """Categorical chart: x = measured concurrency slots, y = one metric."""
    x0, x1, y0, y1 = _ML, _CW - _MR, _MT, _CH - _MB
    plot_w, plot_h = x1 - x0, y1 - y0
    nums = [y for y in y_values if _is_num(y)]
    # Single measured value: centre it vertically using value ±10% (avoids a
    # point pinned to the top and any zero-division). Multi-point keeps the
    # 0-based axis (unchanged behaviour).
    if len(nums) == 1:
        v = nums[0]
        lo_y, hi_y = (v * 0.9, v * 1.1) if v else (0.0, 1.0)
    else:
        lo_y, hi_y = 0.0, (max(nums) * 1.15 if nums else 1.0)
    if hi_y <= lo_y:
        hi_y = lo_y + 1.0
    rng_y = hi_y - lo_y
    n = len(x_labels)
    xs = [x0 + plot_w / 2] if n == 1 else [x0 + plot_w * i / (n - 1) for i in range(n)]

    def ypx(v: float) -> float:
        return y0 + plot_h * (1 - (v - lo_y) / rng_y)

    pts = [
        [round(xs[i], 1), round(ypx(y_values[i]), 1)] if _is_num(y_values[i]) else None
        for i in range(n)
    ]

    p = _svg_head(title, better)
    # axes
    p.append(f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="#888"/>')
    p.append(f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="#888"/>')
    # y gridlines + ticks
    for i in range(6):
        val = lo_y + rng_y * i / 5
        py = ypx(val)
        p.append(
            f'<line x1="{x0}" y1="{py:.1f}" x2="{x1}" y2="{py:.1f}" stroke="#eee"/>'
        )
        p.append(
            f'<text x="{x0 - 6}" y="{py + 3:.1f}" text-anchor="end" font-size="10" '
            f'fill="#666">{val:,.0f}</text>'
        )
    p.append(
        f'<text x="14" y="{y0 + plot_h / 2}" text-anchor="middle" font-size="11" '
        f'fill="#444" transform="rotate(-90 14 {y0 + plot_h / 2})">{_esc(y_unit)}</text>'
    )
    # x tick labels + axis title
    for i, lab in enumerate(x_labels):
        p.append(
            f'<text x="{xs[i]:.1f}" y="{y1 + 16:.1f}" text-anchor="middle" '
            f'font-size="11" fill="#444">{_esc(lab)}</text>'
        )
    p.append(
        f'<text x="{x0 + plot_w / 2:.1f}" y="{_CH - 6}" text-anchor="middle" '
        'font-size="11" fill="#444">동시성 (concurrency)</text>'
    )
    # data polyline + base points
    drawn = [(pt[0], pt[1]) for pt in pts if pt]
    if len(drawn) >= 2:
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in drawn)
        p.append(f'<polyline points="{poly}" fill="none" stroke="{color}" '
                 'stroke-width="2"/>')
    for x, y in drawn:
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}"/>')
    # single measured point: state plainly there is no curve (no fabricated line)
    if len(drawn) < 2:
        p.append(
            f'<text x="{x0 + plot_w / 2:.1f}" y="{y0 + 16:.1f}" text-anchor="middle" '
            'font-size="10" fill="#9aa3b2">단일 동시성 측정 — 곡선 없음(측정점 1개)'
            "</text>"
        )
    # interactive overlay
    p.append(_overlay(cid, pts, default_idx, marker=True))
    p.append("</svg>")
    return "".join(p), pts


def _scatter_chart(
    cid: str,
    title: str,
    x_vals: list[Any],
    y_vals: list[Any],
    conc_labels: list[str],
    x_unit: str,
    y_unit: str,
    default_idx: int,
) -> tuple[str, list]:
    """Compact signature trade-off chart: x = TPS/user, y = system throughput.

    Smaller viewBox (360x300), data-range axes with ±8% padding (not 0-based),
    minimal gridlines, thin light connector, and only the selected operating
    point emphasised (ring overlay). All values from ``result`` — no fabrication.
    """
    # Local geometry — intentionally smaller than the categorical charts.
    w, h = 360, 300
    ml, mr, mt, mb = 56, 18, 40, 52
    x0, x1, y0, y1 = ml, w - mr, mt, h - mb
    plot_w, plot_h = x1 - x0, y1 - y0

    xn = [v for v in x_vals if _is_num(v)]
    yn = [v for v in y_vals if _is_num(v)]

    def _bounds(vals: list[float]) -> tuple[float, float]:
        if not vals:
            return 0.0, 1.0
        lo, hi = min(vals), max(vals)
        span = hi - lo
        pad = span * 0.08 if span > 0 else (abs(hi) * 0.08 or 1.0)
        return lo - pad, hi + pad

    lo_x, hi_x = _bounds(xn)
    lo_y, hi_y = _bounds(yn)
    rng_x = (hi_x - lo_x) or 1.0
    rng_y = (hi_y - lo_y) or 1.0

    def xpx(v: float) -> float:
        return x0 + plot_w * (v - lo_x) / rng_x

    def ypx(v: float) -> float:
        return y0 + plot_h * (1 - (v - lo_y) / rng_y)

    n = len(conc_labels)
    pts = [
        [round(xpx(x_vals[i]), 1), round(ypx(y_vals[i]), 1)]
        if (_is_num(x_vals[i]) and _is_num(y_vals[i]))
        else None
        for i in range(n)
    ]

    p = [
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="{_esc(title)}" '
        'style="width:100%;height:auto;display:block;background:#fff;'
        'border:1px solid #eef0f4;border-radius:8px">',
        f'<text x="{ml}" y="20" text-anchor="start" font-size="13" '
        f'font-weight="600" fill="#222">{_esc(title)}</text>',
    ]
    # minimal horizontal gridlines (3) with light y tick labels
    for i in range(1, 4):
        val = lo_y + rng_y * i / 4
        py = ypx(val)
        p.append(
            f'<line x1="{x0}" y1="{py:.1f}" x2="{x1}" y2="{py:.1f}" '
            'stroke="#f0f1f4"/>'
        )
        p.append(
            f'<text x="{x0 - 6}" y="{py + 3:.1f}" text-anchor="end" font-size="9" '
            f'fill="#9aa3b2">{val:,.0f}</text>'
        )
    # light axes (bottom + left)
    p.append(f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="#d6dae2"/>')
    p.append(f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="#d6dae2"/>')
    # x end tick labels (data min/max ends, padded range)
    p.append(
        f'<text x="{x0}" y="{y1 + 15:.1f}" text-anchor="start" font-size="9" '
        f'fill="#9aa3b2">{(min(xn) if xn else 0):,.0f}</text>'
    )
    p.append(
        f'<text x="{x1}" y="{y1 + 15:.1f}" text-anchor="end" font-size="9" '
        f'fill="#9aa3b2">{(max(xn) if xn else 0):,.0f}</text>'
    )
    # one-line axis titles (arrows convey "higher is better")
    p.append(
        f'<text x="14" y="{y0 + plot_h / 2:.1f}" text-anchor="middle" font-size="10" '
        f'fill="#5b6573" transform="rotate(-90 14 {y0 + plot_h / 2:.1f})">'
        f'{_esc(y_unit)}</text>'
    )
    p.append(
        f'<text x="{x0 + plot_w / 2:.1f}" y="{h - 8}" text-anchor="middle" '
        f'font-size="10" fill="#5b6573">{_esc(x_unit)}</text>'
    )
    # thin light connector through points
    drawn = [(pt[0], pt[1]) for pt in pts if pt]
    if len(drawn) >= 2:
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in drawn)
        p.append(f'<polyline points="{poly}" fill="none" stroke="#c7d2fe" '
                 'stroke-width="1.5"/>')
    # small muted data points + compact concurrency labels (offset to avoid overlap)
    for i, pt in enumerate(pts):
        if not pt:
            continue
        p.append(f'<circle cx="{pt[0]:.1f}" cy="{pt[1]:.1f}" r="3" fill="#9db2e0"/>')
        p.append(
            f'<text x="{pt[0] + 6:.1f}" y="{pt[1] - 6:.1f}" font-size="9" '
            f'fill="#6b7280">c={_esc(conc_labels[i])}</text>'
        )
    # single measured point: no trade-off curve to draw
    if len(drawn) < 2:
        p.append(
            f'<text x="{x0 + plot_w / 2:.1f}" y="{y0 + 16:.1f}" text-anchor="middle" '
            'font-size="9" fill="#9aa3b2">단일 동시성 측정 — 곡선 없음(측정점 1개)'
            "</text>"
        )
    # selected operating point only: ring overlay (dark TTA blue)
    p.append(_overlay(cid, pts, default_idx, marker=False))
    p.append("</svg>")
    return "".join(p), pts


# -- table cell with P90 primary + p50/p99 sub -------------------------------
def _dist_cell(dist: Any) -> str:
    """Render a {p50,p90,p99} dict as a cell: big P90, small p50/p99 beneath."""
    if not isinstance(dist, dict):
        return '<td class="num">N/A</td>'
    p50, p90, p99 = dist.get("p50"), dist.get("p90"), dist.get("p99")
    return (
        '<td class="num">'
        f'<span class="big">{_fmt(p90)}</span>'
        f'<span class="sub">p50 {_fmt(p50)} · p99 {_fmt(p99)}</span>'
        "</td>"
    )


def _summary_table(result: list[dict[str, Any]]) -> str:
    head = (
        "<tr>"
        "<th>동시성</th>"
        "<th>TTFT P90 (ms)</th>"
        "<th>TPOT P90 (ms)</th>"
        "<th>E2EL P90 (ms)</th>"
        "<th>Throughput<br>(tok/s)</th>"
        "<th>TPS per user<br>(tok/s)</th>"
        "<th>성공 / 실패</th>"
        "</tr>"
    )
    rows = []
    for i, r in enumerate(result):
        req = r.get("requests", {}) or {}
        succ = req.get("successful", "N/A")
        fail = req.get("errored", "N/A")
        rows.append(
            f'<tr data-idx="{i}">'
            f'<td class="conc">{_esc(r.get("concurrency", "N/A"))}</td>'
            f"{_dist_cell(r.get('ttft_ms'))}"
            f"{_dist_cell(r.get('tpot_ms'))}"
            f"{_dist_cell(r.get('e2el_ms'))}"
            f'<td class="num"><span class="big">{_fmt(r.get("throughput_tok_s"))}'
            f'</span><span class="sub">p90 '
            f'{_fmt(r.get("throughput_tok_s_p90"))}</span></td>'
            f'<td class="num"><span class="big">'
            f'{_fmt(r.get("tps_per_user_tok_s"))}</span></td>'
            f'<td class="num">{_esc(succ)} / {_esc(fail)}</td>'
            "</tr>"
        )
    return f'<table class="summary">{head}{"".join(rows)}</table>'


def _slo_panel(result: list[dict[str, Any]]) -> str:
    """O/X grade table: pass = (TTFT P90 <= 2000ms) AND (TPOT P90 <= grade)."""
    grade_heads = "".join(
        f"<th>TPOT ≤ {g:.0f}ms 등급</th>" for g in SLO_TPOT_GRADES_MS
    )
    head = (
        "<tr><th>동시성</th><th>TTFT P90 (ms)</th>"
        f"<th>TTFT ≤ {SLO_TTFT_P90_MS:.0f}ms</th>"
        f"<th>TPOT P90 (ms)</th>{grade_heads}</tr>"
    )
    rows = []
    for i, r in enumerate(result):
        ttft = (r.get("ttft_ms") or {}).get("p90")
        tpot = (r.get("tpot_ms") or {}).get("p90")
        ttft_ok = _is_num(ttft) and ttft <= SLO_TTFT_P90_MS
        ttft_mark = (
            '<span class="ok">O</span>'
            if ttft_ok
            else ('<span class="bad">X</span>' if _is_num(ttft) else "미측정")
        )
        grade_cells = []
        for g in SLO_TPOT_GRADES_MS:
            if not (_is_num(ttft) and _is_num(tpot)):
                grade_cells.append("<td>미측정</td>")
            elif ttft_ok and tpot <= g:
                grade_cells.append('<td><span class="ok">O</span></td>')
            else:
                grade_cells.append('<td><span class="bad">X</span></td>')
        rows.append(
            f'<tr data-idx="{i}">'
            f'<td class="conc">{_esc(r.get("concurrency", "N/A"))}</td>'
            f'<td class="num">{_fmt(ttft)}</td>'
            f"<td>{ttft_mark}</td>"
            f'<td class="num">{_fmt(tpot)}</td>'
            f"{''.join(grade_cells)}"
            "</tr>"
        )
    note = (
        '<p class="note">기준: TTFT P90 ≤ '
        f"{SLO_TTFT_P90_MS:.0f}ms <b>그리고</b> TPOT P90 ≤ 등급값(30/40/50ms). "
        "두 조건을 모두 만족해야 해당 등급 통과(O). 임계값은 K-Perf 학습문서 기준값."
        "</p>"
    )
    return f'<table class="slo">{head}{"".join(rows)}</table>{note}'


def _meta_table(meta: dict[str, Any]) -> str:
    model_src = meta.get("model_source")
    model_disp = meta.get("model")
    if model_src:
        model_disp = f"{model_disp}  ({model_src})"
    fields = [
        ("모델", model_disp),
        ("Target", meta.get("target")),
        ("프로파일", meta.get("profile")),
        ("Rate (concurrency 단계)", meta.get("rate")),
        ("단계당 max_seconds", meta.get("max_seconds")),
        ("시나리오", meta.get("scenario")),
        ("측정 시각", meta.get("measured_at")),
        ("GuideLLM 커밋", meta.get("guidellm_commit")),
    ]
    rows = "".join(
        f"<tr><th>{_esc(k)}</th><td>{_esc(v) if v not in (None, '') else 'N/A'}"
        "</td></tr>"
        for k, v in fields
    )
    return f'<table class="meta">{rows}</table>'


def _slider_block(x_labels: list[str], default_idx: int) -> str:
    """Range slider snapping to measured concurrency indices only.

    With a single measured level the slider is disabled and visually hidden (a
    one-level slider has nothing to move to); a hidden ``#conc-slider`` input is
    still emitted so the JS ``select(0)`` call initialises the readout/panel.
    """
    n = len(x_labels)
    if n <= 1:
        conc = x_labels[0] if x_labels else "-"
        return (
            '<div class="slider-wrap">'
            '<label>동시성 슬라이더 — 단일 동시성 측정(측정점 1개)이라 비활성</label>'
            '<input type="range" id="conc-slider" min="0" max="0" step="1" '
            'value="0" disabled style="display:none">'
            f'<div class="single-note">동시성 c={_esc(conc)} (단일 측정) — '
            '동작점 고정</div>'
            "</div>"
        )
    options = "".join(f'<option value="{i}"></option>' for i in range(n))
    ticks = "".join(f"<span>{_esc(lab)}</span>" for lab in x_labels)
    return (
        '<div class="slider-wrap">'
        '<label for="conc-slider">동시성 슬라이더 — 측정 레벨에만 스냅 '
        '(중간값은 만들지 않음)</label>'
        f'<input type="range" id="conc-slider" min="0" max="{max(n - 1, 0)}" '
        f'step="1" value="{default_idx}" list="conc-ticks">'
        f'<datalist id="conc-ticks">{options}</datalist>'
        f'<div class="ticklabels">{ticks}</div>'
        "</div>"
    )


def _readout_block() -> str:
    """Operating-point readout cards; values filled by JS from KPERF_DATA."""
    def card(label: str, vid: str, unit: str) -> str:
        return (
            '<div class="ro-card"><div class="ro-label">' + label + "</div>"
            '<div class="ro-val"><span id="' + vid + '">-</span>'
            '<span class="ro-unit">' + unit + "</span></div></div>"
        )

    return (
        '<div class="readout">'
        '<div class="ro-head">선택 동작점 — 동시성 '
        '<span id="ro-conc" class="ro-conc">-</span></div>'
        '<div class="ro-grid">'
        + card("TTFT P90", "ro-ttft", "ms")
        + card("TPOT P90", "ro-tpot", "ms")
        + card("E2EL P90", "ro-e2el", "ms")
        + card("Throughput", "ro-thrpt", "tok/s")
        + card("TPS per user", "ro-tpsuser", "tok/s")
        + '<div class="ro-card ro-slo-card"><div class="ro-label">SLO 등급 (TPOT)'
          '</div><div id="ro-slo" class="ro-slo">-</div></div>'
        + "</div></div>"
    )


def _signature_panel(result: list[dict[str, Any]]) -> str:
    """Right-hand numeric + commentary panel for the signature card.

    Big throughput / interactivity numbers (filled by JS, linked to the slider)
    plus a static measured-range summary computed from real values only.
    """
    concs = [r.get("concurrency") for r in result if _is_num(r.get("concurrency"))]
    tps_pairs = [
        (r.get("throughput_tok_s"), r.get("concurrency"))
        for r in result
        if _is_num(r.get("throughput_tok_s"))
    ]
    if len(tps_pairs) == 1:
        tps_v, conc_v = tps_pairs[0]
        summary = f"동시성 {conc_v} 단일 측정값 · 처리량 {_fmt(tps_v, 0)} tok/s"
    elif concs and tps_pairs:
        max_tps, argmax_conc = max(tps_pairs, key=lambda p: p[0])
        summary = (
            f"측정 동시성 {min(concs)}~{max(concs)} · 최대 처리량 "
            f"{_fmt(max_tps, 0)} tok/s @ c={argmax_conc}"
        )
    else:
        summary = "측정 데이터 없음"

    return (
        '<div class="sig-panel">'
        '<div class="sig-head">동시성 c=<span id="sig-conc">-</span> 동작점</div>'
        '<div class="sig-cards">'
        '<div class="sig-sub"><div class="sig-label">System Throughput</div>'
        '<div class="sig-val"><span id="sig-thrpt">-</span>'
        '<span class="sig-unit">tok/s</span></div></div>'
        '<div class="sig-sub"><div class="sig-label">Interactivity (TPS/user)</div>'
        '<div class="sig-val"><span id="sig-tpsuser">-</span>'
        '<span class="sig-unit">tok/s/user</span></div></div>'
        '</div>'
        '<p class="sig-desc">오른쪽·위로 갈수록 좋음 — 처리량과 사용자 체감속도가 '
        '함께 큼.</p>'
        f'<p class="sig-desc">{_esc(summary)}</p>'
        "</div>"
    )


_SCRIPT_TMPL = """
const KPERF_DATA = __KPERF_DATA__;
const GEO = __GEO__;
const SLO_TTFT = __SLO_TTFT__;
const GRADES = __GRADES__;

function nf(v, d){
  return (typeof v === 'number')
    ? v.toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d})
    : 'N/A';
}
function ox(b){ return b ? '<span class="ok">O</span>' : '<span class="bad">X</span>'; }
function p90(o){ return (o && typeof o.p90 === 'number') ? o.p90 : null; }

function select(i){
  if (!KPERF_DATA.length) return;
  i = Math.max(0, Math.min(KPERF_DATA.length - 1, i | 0));
  const row = KPERF_DATA[i];

  for (const cid in GEO){
    const g = GEO[cid];
    const p = g.pts[i];
    const ring = document.getElementById(cid + '-ring');
    if (ring){
      if (p){ ring.setAttribute('cx', p[0]); ring.setAttribute('cy', p[1]); ring.style.opacity = '1'; }
      else { ring.style.opacity = '0'; }
    }
    if (g.marker){
      const mk = document.getElementById(cid + '-marker');
      if (mk){
        if (p){ mk.setAttribute('x1', p[0]); mk.setAttribute('x2', p[0]); mk.style.opacity = '1'; }
        else { mk.style.opacity = '0'; }
      }
    }
  }

  const ttft = p90(row.ttft_ms), tpot = p90(row.tpot_ms), e2el = p90(row.e2el_ms);
  document.getElementById('ro-conc').textContent = row.concurrency;
  document.getElementById('ro-ttft').textContent = nf(ttft, 2);
  document.getElementById('ro-tpot').textContent = nf(tpot, 2);
  document.getElementById('ro-e2el').textContent = nf(e2el, 2);
  document.getElementById('ro-thrpt').textContent = nf(row.throughput_tok_s, 2);
  document.getElementById('ro-tpsuser').textContent = nf(row.tps_per_user_tok_s, 2);

  // signature card panel (linked to the same selection)
  const sc = document.getElementById('sig-conc');
  if (sc) sc.textContent = row.concurrency;
  const st = document.getElementById('sig-thrpt');
  if (st) st.textContent = nf(row.throughput_tok_s, 2);
  const su = document.getElementById('sig-tpsuser');
  if (su) su.textContent = nf(row.tps_per_user_tok_s, 2);

  const ttftOk = (typeof ttft === 'number') && ttft <= SLO_TTFT;
  let slo = '';
  for (const g of GRADES){
    const known = (typeof ttft === 'number') && (typeof tpot === 'number');
    const pass = ttftOk && (typeof tpot === 'number') && tpot <= g;
    slo += '<span class="slo-pill">&le;' + g + 'ms ' + (known ? ox(pass) : '미측정') + '</span>';
  }
  document.getElementById('ro-slo').innerHTML = slo;

  const s = document.getElementById('conc-slider');
  if (s) s.value = i;
  document.querySelectorAll('tr[data-idx]').forEach(function(tr){
    tr.classList.toggle('sel', (+tr.dataset.idx) === i);
  });
}

document.addEventListener('DOMContentLoaded', function(){
  const s = document.getElementById('conc-slider');
  if (s){
    s.addEventListener('input', function(e){ select(+e.target.value); });
    select(+s.value);
  }
});
"""


def render_html(
    result: list[dict[str, Any]],
    meta: dict[str, Any],
    out_path: str,
) -> str:
    """Render the interactive K-Perf HTML report to ``out_path``.

    :param result: ``kperf_result.json`` schema — ``list[dict]``, one per
        measured concurrency step (see :func:`kperf.runner.extract_metrics`).
    :param meta: run metadata (model, target, profile, rate, max_seconds,
        scenario, measured_at, guidellm_commit).
    :param out_path: file path to write the self-contained HTML to.
    :return: the written file path.
    """
    result = result or []
    n = len(result)
    default_idx = n - 1 if n else 0
    x_labels = [str((r or {}).get("concurrency", "?")) for r in result]

    thrpt = [(r or {}).get("throughput_tok_s") for r in result]
    ttft_p90 = [((r or {}).get("ttft_ms") or {}).get("p90") for r in result]
    tps_user = [(r or {}).get("tps_per_user_tok_s") for r in result]

    # build the four linked charts (each returns markup + pixel geometry)
    svg_thrpt, pts_thrpt = _cat_chart(
        "thrpt", "Throughput vs Concurrency", "better ↑",
        x_labels, _BLUE, thrpt, "tokens / s", default_idx,
    )
    svg_lat, pts_lat = _cat_chart(
        "lat", "Latency vs Concurrency (TTFT P90)", "better ↓",
        x_labels, "#dc2626", ttft_p90, "ms (TTFT P90)", default_idx,
    )
    svg_inter, pts_inter = _cat_chart(
        "inter", "Interactivity vs Concurrency (TPS/user)", "better ↑",
        x_labels, "#7c3aed", tps_user, "tok/s/user", default_idx,
    )
    svg_sig, pts_sig = _scatter_chart(
        "sig", "Throughput vs Interactivity",
        tps_user, thrpt, x_labels,
        "사용자 체감속도 ↑ (tok/s/user)",
        "시스템 처리량 ↑ (tok/s)",
        default_idx,
    )

    geo = {
        "thrpt": {"marker": True, "pts": pts_thrpt},
        "lat": {"marker": True, "pts": pts_lat},
        "inter": {"marker": True, "pts": pts_inter},
        "sig": {"marker": False, "pts": pts_sig},
    }

    measured = "측정됨" if result else "데이터 없음"
    foot = (
        "재현성 메타 — "
        f"GuideLLM 커밋: {_esc(meta.get('guidellm_commit') or 'fb3e862')} · "
        f"모델: {_esc(meta.get('model') or 'N/A')} · "
        f"Target: {_esc(meta.get('target') or 'N/A')} · "
        f"프로파일: {_esc(meta.get('profile') or 'N/A')} · "
        f"Rate: {_esc(meta.get('rate') or 'N/A')} · "
        f"max_seconds: {_esc(meta.get('max_seconds') or 'N/A')} · "
        f"측정 시각: {_esc(meta.get('measured_at') or 'N/A')}"
    )

    style = """
    :root { font-family: -apple-system, "Segoe UI", "Noto Sans KR", sans-serif; }
    body { margin: 0; padding: 24px; background: #f6f7f9; color: #1f2430; }
    .wrap { max-width: 1080px; margin: 0 auto; }
    h1 { font-size: 24px; margin: 0 0 2px; }
    h2 { font-size: 17px; margin: 28px 0 10px; border-left: 4px solid #2563eb;
         padding-left: 8px; }
    .brand { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
    .brand .logo { display: block; }
    .brand .titles { line-height: 1.25; }
    .brand .subtitle { font-size: 13px; color: #5b6573; }
    .logo-ph { height: 44px; min-width: 120px; display: flex; align-items: center;
               justify-content: center; padding: 0 14px; border: 1px dashed #b6bdc9;
               border-radius: 6px; color: #8a93a2; font-size: 12px; }
    .card { background: #fff; border: 1px solid #e4e7ec; border-radius: 8px;
            padding: 16px; margin-bottom: 12px; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { border: 1px solid #e4e7ec; padding: 7px 9px; text-align: center; }
    th { background: #f0f3f8; font-weight: 600; }
    table.meta th { width: 200px; text-align: left; background: #f7f9fc; }
    table.meta td { text-align: left; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    td.conc { font-weight: 700; }
    .big { display: block; font-size: 14px; font-weight: 600; }
    .sub { display: block; font-size: 10px; color: #8a93a2; }
    .ok  { color: #16a34a; font-weight: 800; }
    .bad { color: #dc2626; font-weight: 800; }
    tr.sel td { background: #eaf1ff; }
    /* slider */
    .slider-wrap { display: flex; flex-direction: column; gap: 6px; }
    .slider-wrap label { font-size: 13px; color: #5b6573; font-weight: 600; }
    #conc-slider { width: 100%; accent-color: #2563eb; }
    .ticklabels { display: flex; justify-content: space-between; font-size: 12px;
                  color: #444; font-variant-numeric: tabular-nums; }
    .single-note { font-size: 13px; color: #2563eb; font-weight: 600;
                   background: #eff4ff; border: 1px solid #dce6fb;
                   border-radius: 8px; padding: 8px 12px; }
    /* readout */
    .readout .ro-head { font-size: 14px; font-weight: 700; margin-bottom: 10px; }
    .ro-conc { color: #2563eb; }
    .ro-grid { display: grid; gap: 10px;
               grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
    .ro-card { border: 1px solid #e4e7ec; border-radius: 8px; padding: 10px 12px;
               background: #fbfcfe; }
    .ro-label { font-size: 11px; color: #6b7280; }
    .ro-val { font-size: 22px; font-weight: 700; color: #1f2430; }
    .ro-unit { font-size: 11px; font-weight: 500; color: #8a93a2; margin-left: 4px; }
    .ro-slo-card { background: #f0f6ff; }
    .ro-slo { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
    .slo-pill { font-size: 12px; background: #fff; border: 1px solid #d6def0;
                border-radius: 999px; padding: 2px 8px; }
    /* charts */
    .charts { display: grid; gap: 16px;
              grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
    .charts > svg { width: 100%; height: auto; }
    /* signature 2-column card: chart (left) + numbers/commentary (right) */
    .sig-card { display: flex; gap: 24px; align-items: center; flex-wrap: wrap; }
    .sig-chart { flex: 1 1 360px; max-width: 460px; }
    .sig-chart > svg { width: 100%; height: auto; }
    .sig-panel { flex: 1 1 300px; text-align: center;
                 background: linear-gradient(160deg, #eff4ff 0%, #f7faff 100%);
                 border: 1px solid #dce6fb; border-radius: 12px; padding: 20px;
                 box-shadow: 0 2px 10px rgba(37, 99, 235, 0.08); }
    .sig-head { font-size: 14px; font-weight: 700; margin-bottom: 14px;
                color: #1f2430; }
    .sig-head #sig-conc { color: #2563eb; }
    .sig-cards { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center;
                 margin-bottom: 12px; }
    .sig-sub { flex: 1 1 150px; background: #fff; border: 1px solid #e4ebf8;
               border-radius: 8px; padding: 12px; }
    .sig-label { font-size: 12px; color: #6b7280; }
    .sig-val { font-size: 30px; font-weight: 800; color: #2563eb; line-height: 1.15;
               font-variant-numeric: tabular-nums; margin-top: 4px; }
    .sig-unit { font-size: 12px; font-weight: 500; color: #8a93a2; margin-left: 5px; }
    .sig-desc { font-size: 12px; color: #6b7280; margin: 6px 0 0; }
    .hl-ring { transition: cx .35s ease, cy .35s ease, opacity .2s ease;
               pointer-events: none; }
    .hl-marker { transition: x1 .35s ease, x2 .35s ease, opacity .2s ease;
                 pointer-events: none; }
    .note { font-size: 12px; color: #5b6573; margin: 8px 0 0; }
    .empty { color: #8a93a2; font-style: italic; }
    footer { margin-top: 24px; font-size: 11px; color: #6b7280;
             border-top: 1px solid #e4e7ec; padding-top: 10px; }
    """

    summary_section = (
        _summary_table(result)
        if result
        else '<p class="empty">결과 데이터가 없습니다 (미측정).</p>'
    )

    if result:
        interactive = (
            '<h2>동작점 탐색 (동시성 슬라이더)</h2>'
            f'<div class="card">{_slider_block(x_labels, default_idx)}'
            f'{_readout_block()}</div>'
            '<h2>곡선 — 동시성별 (better 방향 표기, 슬라이더와 연동)</h2>'
            f'<div class="card charts">{svg_thrpt}{svg_lat}{svg_inter}</div>'
            '<h2>시그니처 — Throughput vs Interactivity (트레이드오프)</h2>'
            f'<div class="card sig-card"><div class="sig-chart">{svg_sig}</div>'
            f'{_signature_panel(result)}</div>'
        )
    else:
        interactive = (
            '<h2>동작점 탐색</h2>'
            '<div class="card"><p class="empty">측정 데이터가 없어 '
            '슬라이더/차트를 표시하지 않습니다.</p></div>'
        )

    script = (
        _SCRIPT_TMPL.replace("__KPERF_DATA__", json.dumps(result, ensure_ascii=False))
        .replace("__GEO__", json.dumps(geo))
        .replace("__SLO_TTFT__", repr(SLO_TTFT_P90_MS))
        .replace("__GRADES__", json.dumps([int(g) for g in SLO_TPOT_GRADES_MS]))
    )

    # favicon (optional): assets/tta_symbol.png as base64 data URI, no external URL
    favicon_uri = meta.get("favicon_data_uri") or _png_data_uri("tta_symbol.png")
    favicon_tag = (
        f'<link rel="icon" type="image/png" href="{favicon_uri}">'
        if favicon_uri
        else ""
    )

    doc = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>K-Perf 성능 측정 리포트</title>
{favicon_tag}
<style>{style}</style>
</head>
<body>
<div class="wrap">

  <header>
    <div class="brand">
      {_logo_block(meta)}
      <div class="titles">
        <h1>K-Perf 성능 측정 리포트</h1>
        <div class="subtitle">한국정보통신기술협회(TTA)</div>
      </div>
    </div>
    <div class="card">{_meta_table(meta)}</div>
  </header>

  {interactive}

  <h2>요약 — 동시성별 분포 (P90 중심, p50/p99 병기)</h2>
  <div class="card">{summary_section}</div>

  <h2>SLO 등급 패널</h2>
  <div class="card">{_slo_panel(result)}</div>

  <h2>전성비 / 가성비</h2>
  <div class="card">
    <p class="empty">전력 미측정 — 본 구현에서 TPS/W · TPS/원 추가 예정.</p>
    <p class="note">전력·가격 데이터를 측정/입력하기 전까지 값을 표시하지 않습니다
       (지어내지 않음).</p>
  </div>

  <footer>{foot} · 데이터 상태: {measured}</footer>

</div>
<script>{script}</script>
</body>
</html>
"""
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return out_path
