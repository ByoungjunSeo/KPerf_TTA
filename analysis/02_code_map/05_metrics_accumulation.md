# 05. TTFT / TPOT / ITL / Throughput 통계 산출 메커니즘 — 줄 단위 확정

> 분석 대상: `vllm-project/guidellm` @ `fb3e862`
> 분석 모드: 본문 줄 단위 열람. 모든 주장은 `파일:라인` 근거 동반.
> 이 문서는 **04 문서(`04_http_resolve_anatomy.md`)에서 내가 한 두 결론을 명시 정정**한다.

## 0. 결론 미리보기 — 04 문서 정정

| 04에서의 결론 | 05 실측 결과 |
|---|---|
| ❌ "TPOT 계산식이 코드에 없음" (04 §6-1) | ✅ **있음**. `GenerativeRequestStats.time_per_output_token_ms` computed_field가 명시 정의: `1000 * (last_token_iteration - request_start) / output_tokens` (`schemas/request_stats.py:163-180`) |
| ❌ "ITL 분포는 청크 시각 미보관으로 산출 불가" (04 §6-1) | ⚠️ **부분만 맞음**. 청크 시각이 보관 안 되는 건 사실. 하지만 분포는 그 사실과 무관하게 **요청 1건당 평균 ITL 단일값**을 `(값, 가중치=output_tokens-1)`로 모아 **가중 CDF 분위수**로 산출됨 (`metrics.py:937-945` + `statistics.py:252-310, 53-111`). 즉 "intra-request 토큰 간격 분포"는 산출 불가지만, "inter-request 토큰-가중 ITL 분포"는 산출됨 |

이 두 정정의 코드 근거가 본 문서의 본체다.

## 1. 데이터 흐름 한눈에

```
[runtime, per-request]
  http.py:481-518       backend가 RequestInfo.timings.{request_start, first_token_iteration,
                                                       last_token_iteration, token_iterations,
                                                       request_iterations, request_end} 채움
  worker.py:413         resolve_end = time.time()
  worker_group.py:583   queued = time.time() (메인 측)

[scheduler.run yields]   (response, request, request_info, scheduler_state) ─┐
                                                                            │ benchmarker.py:133-151
                                                                            ▼
[accumulator]                                                accumulator.update_estimate(response, request, info, state)
  accumulator.py:808-859                                       │
                                                              ├─ status별 GenerativeRequestsAccumulator.update_estimate
                                                              │     → compile_stats → response.compile_stats(request, info)
                                                              │     → GenerativeRequestStats 인스턴스 생성 (request_stats.py)
                                                              │     → self.requests_stats.append(stats)  ★ 원본 보관
                                                              │
                                                              └─ GenerativeMetricsAccumulator.update_estimate(stats, duration)
                                                                    → RunningMetricStats 4종을 sum/count로 누적
                                                                       (분포 산출 X — mean만)

[end of strategy: Benchmarker.run inner loop ends]
  benchmarker.py:161-164  benchmark = benchmark_class.compile(accumulator=..., scheduler_state=...)
                            │
                            ▼ benchmark/schemas/generative/benchmark.py:139-163
                            GenerativeBenchmark.compile
                              ├─ SchedulerMetrics.compile(accumulator, scheduler_state)   (mean만)
                              └─ GenerativeMetrics.compile(accumulator)                    ★ 분포 산출 본진
                                   │
                                   ▼ benchmark/schemas/generative/metrics.py:840-996
                                   GenerativeMetrics.compile
                                     ├─ successful = accumulator.completed.get_within_range(start, end)  ← list[GenerativeRequestStats]
                                     ├─ incomplete = accumulator.incomplete.get_within_range(...)
                                     ├─ errored = accumulator.errored.get_within_range(...)
                                     │
                                     └─ 각 메트릭마다 StatusDistributionSummary.from_values_function(lambda req: ..., successful, incomplete, errored)
                                          │
                                          ▼ schemas/statistics.py:706-749, 252-310, 53-111
                                          1) 함수로 (value, weight) 추출 → list로 모음
                                          2) value 정렬 + 중복 weight 합산 → 가중 PDF
                                          3) np.cumsum → Percentiles.from_pdf → searchsorted
```

핵심: **분포 percentile은 "compile 시점"의 *원본 GenerativeRequestStats 리스트*에서 산출된다.** RunningMetricStats는 실시간 mean 표시용일 뿐, p50/p95 산출에 쓰이지 않는다.

> **04 정정에 결정적인 사실**: 모든 `GenerativeRequestStats`가 `GenerativeRequestsAccumulator.requests_stats: list`에 보관됨 (`accumulator.py:578, 657`). `sample_requests` 옵션이 켜져 있으면 reservoir sampling이 적용되지만 기본값은 None=전부 보관 (`accumulator.py:572-574`). 분포 산출에는 보관된 stats의 **computed_field**(request_stats.py)를 lambda로 호출해 값을 뽑는다.

## 2. 지표별 — "출력 표 컬럼 → 계산식 → 입력 필드 → 파일:라인" 표

### 2-1. 콘솔 출력 표 구조

콘솔 표 컬럼은 `add_stats(stats, ..., types=("median","p95"))`이 자동 생성 — Mdn / p95 두 컬럼 (`console.py:100, 119-129, 174-184`). `_get_stat_type_name_val`이 `("Mdn", stats.median)`, `("p95", stats.percentiles.p95)`를 반환.

### 2-2. 메트릭 매핑 표

| 콘솔 컬럼 | GuideLLM 필드 | 출력 단위 | 컴퓨트 식 | 입력 (RequestTimings 등) | 정의 파일:라인 | compile 시 분포 입력 |
|---|---|---|---|---|---|---|
| **TTFT Mdn / p95** | `metrics.time_to_first_token_ms` | **ms** | `1000 * (first_token_iteration - request_start)` | `info.timings.first_token_iteration`, `info.timings.request_start` | `request_stats.py:148-159`; **×1000 변환은 159줄** | `lambda req: req.time_to_first_token_ms or 0.0` → 단일값, 비가중 (`metrics.py:922-927`) |
| **ITL Mdn / p95** | `metrics.inter_token_latency_ms` | **ms** | `1000 * (last_token_iteration - first_token_iteration) / (output_tokens - 1)` | 동일 + `output_tokens` | `request_stats.py:182-201`; **×1000 변환은 201줄** | `lambda req: (req.inter_token_latency_ms or 0.0, (req.output_tokens or 1.0) - 1.0)` → **(값, 가중치=N-1)** (`metrics.py:937-945`) |
| **TPOT Mdn / p95** | `metrics.time_per_output_token_ms` | **ms** | `1000 * (last_token - request_start) / output_tokens` (단 `last_token = last_token_iteration or request_end_time`) | 동일 + `output_tokens` | `request_stats.py:161-180`; **×1000 변환은 180줄** | `lambda req: (req.time_per_output_token_ms or 0.0, req.output_tokens or 0.0)` → **(값, 가중치=N)** (`metrics.py:928-936`) |
| **Request Latency Sec Mdn / p95** | `metrics.request_latency` | **초** (변환 없음) | `request_end - request_start` | `info.timings.request_end`, `info.timings.request_start` | `request_stats.py:97-110` (초 단위, ms 변환 없음) | `lambda req: req.request_latency or 0.0` → 단일값 (`metrics.py:891-896`) |
| Requests Concurrency Mdn/Mean | `metrics.request_concurrency` | (요청 수) | overlap interval | `request_start_time`, `request_end_time` (computed) | `metrics.py:878-890` → `StatusDistributionSummary.concurrency_distribution_from_timings_function` | (시간축 위 동시 처리 요청 수의 가중 분포) |
| Requests Per Sec Mean | `metrics.requests_per_second` | req/s | `rate_distribution_from_timings_function(function=lambda req: req.request_end_time, ...)` | `request_end_time` (computed) | `metrics.py:870-877` | 각 요청 종료 시각을 이벤트로 보고, 인접 이벤트 간 local duration으로 1/duration 산출 |
| **Input Tokens Per Sec Mean** | `metrics.prompt_tokens_per_second` | tok/s | 동상. `function=lambda req: req.prompt_tokens_timing` | `prompt_tokens_timing = (first_token_iteration or request_end_time, prompt_tokens)` | `metrics.py:946-951`; 입력 `request_stats.py:275-288` | (시각, 토큰수) 이벤트의 rate 가중 분포 |
| **Output Tokens Per Sec Mean** | `metrics.output_tokens_per_second` | tok/s | `function=lambda req: req.output_tokens_timings` | linspace로 보간된 `[(t_i, tokens_i), ...]` | `metrics.py:952-957`; 입력 `request_stats.py:291-316` | 동일 |
| **Total Tokens Per Sec Mean** | `metrics.tokens_per_second` | tok/s | `function=lambda req: req.total_tokens_timings` | prompt + output timings 합 | `metrics.py:958-963`; 입력 `request_stats.py:343-350` | 동일 |
| (요청별) `output_tokens_per_second` 등 | `GenerativeRequestStats.output_tokens_per_second` computed_field | tok/s | `output_tokens / request_latency` | — | `request_stats.py:214-223` | 분포는 별도(rate 방식과 다름). 콘솔 출력 표엔 미사용. |

### 2-3. 단위 변환 위치 — 한눈에

| 변환 | 정확한 줄 |
|---|---|
| TTFT (초 → ms) | `request_stats.py:159` `return 1000 * (first_token - start)` |
| TPOT (초 → ms) | `request_stats.py:180` `return 1000 * (last_token - start) / output_tokens` |
| ITL (초 → ms) | `request_stats.py:201` `return 1000 * (last_token - first_token) / (output_tokens - 1)` |
| Request Latency | **변환 없음, 초 그대로** `request_stats.py:110` `return end - start` |
| Output token throughput | rate 산출은 초 기반, 변환 없음 (`statistics.py:380` `rates = occurrences / durations`) |

> 모든 timings는 backend가 `time.time()`로 박는 Unix epoch seconds (04 문서 §5 그대로 확인됨). 변환은 ms 표기 필드명에 한해 명시적으로 ×1000.

## 3. TPOT 분모 — 확정 결론

> 04 §6-1에서 "분모 후보 3가지"를 제시했었음. 본문 실측 결과 **분모는 `output_tokens`(즉 `GenerativeRequestStats.output_tokens`) 한 가지로 확정**된다.

`request_stats.py:163-180` 본문:

```python
@computed_field
@property
def time_per_output_token_ms(self) -> float | None:
    if (
        (start := self.info.timings.request_start) is None
        or (
            (last_token := self.last_token_iteration or self.request_end_time)
            is None
        )
        or (output_tokens := self.output_tokens) is None
        or output_tokens == 0
    ):
        return None
    return 1000 * (last_token - start) / output_tokens
```

핵심:
- **분자**: `last_token - start` = `(last_token_iteration or request_end_time) - request_start`. 즉 첫 토큰 도착까지의 시간(=TTFT)을 **포함**한다. 일반적 의미의 "TPOT"는 첫 토큰 후 평균이지만, 여기서는 **첫 토큰 시간이 분자에 포함**된 형태.
- **분모**: `output_tokens` = `GenerativeRequestStats.output_tokens` computed_field (`request_stats.py:121-132`)
  - 1순위: `self.output_metrics.total_tokens` — 즉 `UsageMetrics`의 합산값(text+image+video+audio). 정상 응답 시 서버가 보낸 `usage.completion_tokens`에서 추출됨 (`backends/openai/request_handlers.py:386-398`의 `extract_metrics`).
  - 2순위(폴백): `self.info.timings.token_iterations` — 핸들러가 카운트한 SSE updated chunk 수 (`request_stats.py:130`).
  - **즉 기본 경로는 "서버 측 응답 usage의 토큰 수"** 이고, usage가 누락된 경우에만 청크 카운트로 폴백.

→ **04 §6-1의 후보 ③ `(request_end - first_token_iteration) / output_tokens`은 틀렸음** — 실제는 `(last_token_iteration or request_end_time) - request_start`. 04 후보 ②(서버측 토큰 수 사용)에 더 가깝지만, 정확히는 "TTFT 포함" 형식.

## 4. ITL 분포 산출 메커니즘 — 확정 결론

### 4-1. 요청 1건 당 단일 ITL 값

`request_stats.py:182-201`:

```python
@computed_field
@property
def inter_token_latency_ms(self) -> float | None:
    first_token = self.first_token_iteration
    last_token = self.last_token_iteration
    output_tokens = self.output_tokens
    if (
        first_token is None or last_token is None
        or output_tokens is None or output_tokens <= 1
    ):
        return None
    return 1000 * (last_token - first_token) / (output_tokens - 1)
```

이건 요청 1건의 **평균 ITL** 1개 (출력 토큰 사이 평균 간격). **개별 토큰 사이 간격 N-1개는 보관/계산되지 않음**. 04 §3의 "청크 시각 미보관" 관찰은 그대로 유효.

### 4-2. 분포 — 가중 CDF 분위수

`metrics.py:937-945`:

```python
inter_token_latency_ms=StatusDistributionSummary.from_values_function(
    function=lambda req: (
        req.inter_token_latency_ms or 0.0,     # 값
        (req.output_tokens or 1.0) - 1.0,      # 가중치 = 토큰 간격 개수
    ),
    successful=successful, incomplete=incomplete, errored=errored,
)
```

`StatusDistributionSummary.from_values_function` (`statistics.py:706-749`):
- 각 요청 객체에 `function`을 적용해 `(value, weight)` 튜플을 추출 → 리스트로 모음.

`DistributionSummary.from_values` (`statistics.py:252-310`):
1. `(value, weight)` 리스트를 numpy로 변환
2. value 기준 정렬
3. **중복 value의 weight 합산** (statistics.py:285-287)
4. `probabilities = weights / total_weight` → 정규화된 PDF (statistics.py:301)
5. `from_pdf` 호출 → `Percentiles.from_pdf` → `np.cumsum` + `np.searchsorted`로 분위수 산출 (`statistics.py:104-110`)

→ **결과 분포의 의미**:
- 표본 = 요청 1건당 1개의 평균 ITL 값.
- 가중치 = 그 요청이 생성한 토큰 간격 개수(N−1).
- 가중 CDF 분위수: "토큰 간격 1개를 균일 무작위로 뽑았을 때, 그 간격이 속한 요청의 *평균* ITL이 X 이하일 확률".

### 4-3. 04 추정에 대한 명시적 판정

| 04 §6-1의 항목 | 판정 |
|---|---|
| "ITL 분포는 청크/토큰별 시각이 보관되지 않아 산출 불가" | ❌ **부분 오류**. 청크 시각 미보관 자체는 사실(`request_stats.py:319-340` `iter_tokens_timings`가 `np.linspace`로 균등 보간하는 데서 재확인). 하지만 **분포 자체는 "요청별 평균 ITL의 토큰 가중 분포"로 산출됨**. |
| "ITL 분포가 필요하면 upstream 패치 또는 K-Perf fork 필요" | ⚠️ **사용 목적에 따라 다름**. K-Perf가 요구하는 게 (a) **요청별 평균 ITL의 분위수**라면 → 현재 GuideLLM 산출로 충분. (b) **개별 토큰 간 간격의 분위수**(즉 long-tail이 어떤 토큰에서 발생하는지)라면 → GuideLLM은 산출 불가, fork 필요. |

> K-Perf 명세 문서에서 "ITL p95"가 어느 의미인지 명시 합의 필요. 일반 LLM 벤치마크에서 "ITL p95"의 통상 의미는 (b)이지만, GuideLLM이 보고하는 것은 (a)다.

## 5. p50 / p90 / p95 / p99 — 산출 위치와 입력 표본

### 5-1. 산출 위치

`Percentiles.from_pdf` (`statistics.py:53-111`):

```python
cdf_probs = np.cumsum(probabilities)
return Percentiles(**{
    key: pdf[np.searchsorted(cdf_probs, value, side="left"), 0].item()
    for key, value in percentile_probs.items()
})
```

지원 분위수: `p001/p01/p05/p10/p25/p50/p75/p90/p95/p99/p999` 11개 (`statistics.py:41-51, 77-88`).

알고리즘: **가중 CDF의 `searchsorted("left")`** → 보간 없음(numpy의 `lower` 메서드와 유사). p95 = "누적확률 0.95에 처음 도달하는 value".

### 5-2. 입력 표본 — 메트릭별 표

| 메트릭 | from_values 함수 람다가 반환하는 것 | 표본의 의미 |
|---|---|---|
| `time_to_first_token_ms` | `req.time_to_first_token_ms` (단일 float) | **요청 1건 = 표본 1개** (비가중) |
| `time_per_output_token_ms` | `(req.time_per_output_token_ms, req.output_tokens)` 튜플 | **요청 1건 = 표본 1개**, 가중치 = 출력 토큰 수 |
| `inter_token_latency_ms` | `(req.inter_token_latency_ms, req.output_tokens - 1)` 튜플 | **요청 1건 = 표본 1개**, 가중치 = 토큰 간격 개수 N−1 |
| `request_latency` | `req.request_latency` (단일 float) | **요청 1건 = 표본 1개** (비가중) |
| `request_streaming_iterations_count` | `req.info.timings.request_iterations` | 요청 1건 = SSE chunk 수 표본 1개 (비가중) |
| `prompt_token_count` / `output_token_count` / `total_token_count` | 단일 토큰 수 | 요청 1건 = 토큰 수 표본 1개 (비가중) |
| `requests_per_second` | `req.request_end_time` (이벤트 시각) | **요청 종료 시각 모음 → rate_distribution_from_timings → (rate, local_duration) 가중 분포** |
| `output_tokens_per_iteration` | `[tokens for (_t, tokens) in req.output_tokens_timings]` | 요청 1건당 N개의 표본(보간된 토큰 수)을 flatten |
| `iter_tokens_per_iteration` | `[tokens for (_t, tokens) in req.iter_tokens_timings]` | 동일 |
| `prompt_tokens_per_second` / `output_tokens_per_second` / `tokens_per_second` | `(timestamp, token_count)` 리스트 | **rate_distribution_from_timings_function** 경로 — 이벤트 시각·점유 가중치로 시간축 throughput 분포 산출 |

> 두 모드의 차이:
> - **`from_values_function`** (위쪽 9개): 요청별 단일값(또는 N개 리스트) → 정렬·중복 결합 → 가중 PDF → CDF percentile.
> - **`rate_distribution_from_timings_function`** (아래쪽 4개): 이벤트 시각 시퀀스 → 각 이벤트의 local duration 계산(인접 이벤트 미드포인트 차) → rate = occurrences/duration → (rate, duration) 가중 PDF → CDF percentile.

### 5-3. measurement window — 어느 표본을 계산에 쓰는가

`GenerativeMetrics.compile` 진입 직후 (`metrics.py:849-860`):

```python
start_time = accumulator.timings.finalized_measure_start
end_time = accumulator.timings.finalized_measure_end
successful = accumulator.completed.get_within_range(start_time, end_time)
incomplete = accumulator.incomplete.get_within_range(start_time, end_time)
errored = accumulator.errored.get_within_range(start_time, end_time)
```

`get_within_range` (`accumulator.py:608-632`)는 `stats.request_end_time >= start_time and stats.request_start_time <= end_time`을 만족하는 stats만 통과. 즉 **warmup/cooldown 시각으로 잘라낸 표본**으로 분포 계산.

## 6. System Throughput (tokens/s) — 정확한 계산

### 6-1. 흐름

`metrics.py:952-957`:

```python
output_tokens_per_second=StatusDistributionSummary.rate_distribution_from_timings_function(
    function=lambda req: req.output_tokens_timings,
    successful=successful, incomplete=incomplete, errored=errored,
)
```

`req.output_tokens_timings` (`request_stats.py:291-316`):
- `[(first_token_iteration, 1.0), (linspace_t1, iter_tokens_per_iteration), ...]` (N=token_iterations 등분 보간)
- 청크 시각이 보관 안 되므로 첫/마지막 사이를 균등 분할한 **가짜 timing**.
- 토큰 수가 1개 이하면 단일 표본만 반환.

### 6-2. rate 산출 (statistics.py:313-388)

1. 모든 요청의 timing 이벤트를 flatten → `(timestamp, weight)` 리스트
2. `[start_time, 0.0]`, `[end_time, 0.0]` 양 끝 sentinel 삽입
3. timestamp로 정렬, threshold(1/10 ms) 이내 인접 이벤트 병합
4. 각 이벤트의 local duration = 인접 미드포인트 차 (양 끝은 절반만)
5. `rates = occurrences / durations` ← 각 이벤트의 순간 throughput
6. `(rate, duration)` 쌍을 `from_values`에 넘겨 가중 분위수 산출

→ **결과**: "측정창 안에서 어떤 1초를 무작위로 뽑았을 때 그 1초가 속한 인스턴트의 toks/s가 X 이상일 확률" 식의 분포. mean은 시간 가중 평균 throughput.

### 6-3. K-Perf "system throughput tokens/s" 정의 일치 여부

- K-Perf가 "측정창 전체 토큰수 / 측정창 duration"(단순 sum 평균)을 기대한다면 → GuideLLM의 `mean`은 그 값과 **다를 수 있다**. GuideLLM은 인접 이벤트 간 local duration 가중이라서 균일하지 않은 시간 분포에서 값이 달라짐.
- K-Perf 사양 합의가 필요한 항목 — **확인 필요**.

## 7. K-Perf 지표와의 정합 / 차이 요약

| K-Perf 후보 정의 | GuideLLM 측 산출 | 정합도 |
|---|---|---|
| TTFT = "첫 토큰 도착까지 시간" | `1000 * (first_token_iteration - request_start)` (ms) | ✅ 일치 |
| TPOT = "첫 토큰 이후 평균 토큰 생성 시간" | `1000 * (last_token - request_start) / output_tokens` — **TTFT를 분자에 포함**, 분모는 서버측 토큰 수. | ⚠️ 식이 일반 정의와 다름. K-Perf가 `(last_token - first_token) / (output_tokens - 1)` 형식을 원하면 사실 그것은 GuideLLM의 **ITL** 값과 같다 |
| ITL = "토큰 사이 평균 간격 분포" | 요청별 평균 ITL의 가중 분포(가중치=N−1). **개별 토큰 간격 분포 아님**. | ⚠️ 의미 차이. 본문 §4-3 판정 참조 |
| E2EL = "요청 종료 - 요청 시작" | `request_latency = request_end - request_start` (초) | ✅ 일치 |
| System Throughput tokens/s | rate_distribution_from_timings 기반 (local duration 가중). | ⚠️ "총 토큰/총 시간" 단순 평균과 다를 수 있음 |
| Per-user TPS | (확인 필요 — 현재 콘솔 출력 컬럼에서는 `output_tokens_per_second`(시스템 단위)만 보임. per-user 출력 위치는 본 단계에서 미확인) | ⚠️ **확인 필요** |
| 분위수 산출 알고리즘 | weighted CDF의 `np.searchsorted("left")`, 보간 없음 | numpy 기본 `linear` interpolation과 다름. K-Perf가 보간 분위수를 요구하면 차이 발생 |

## 8. 실측 출력 숫자(`concurrent@1`) 추적 — `TTFT Mdn 16.2ms / p95 19.0ms`, `ITL Mdn 3.5ms`, `TPOT Mdn 3.5ms`, `Request Latency Mdn 3.6s` 가 어디서 나오나

| 숫자 | 출처 (정확) |
|---|---|
| `TTFT Mdn 16.2ms` | `report.benchmarks[i].metrics.time_to_first_token_ms.successful.median` (`metrics.py:795`, 표 출력 `console.py:449-453`) |
| `TTFT p95 19.0ms` | 동일 객체의 `.percentiles.p95` (`statistics.py:49`, 출력 `console.py:182-183`) |
| `ITL Mdn 3.5ms` | `report.benchmarks[i].metrics.inter_token_latency_ms.successful.median` (`metrics.py:801`, 출력 `console.py:454-458`) |
| `TPOT Mdn 3.5ms` | `report.benchmarks[i].metrics.time_per_output_token_ms.successful.median` (`metrics.py:798`, 출력 `console.py:459-463`) |
| `Request Latency Mdn 3.6s` | `report.benchmarks[i].metrics.request_latency.successful.median` (`metrics.py:778`, 출력 `console.py:444-448` with `name="Sec"`) |

추가 관찰: TPOT Mdn(3.5ms) ≈ ITL Mdn(3.5ms) 이 거의 같은데, **이는 출력 토큰이 충분히 많을 때 자연스러운 결과**다. 식 비교:

- TPOT = `1000 * (last_token - request_start) / output_tokens`
- ITL  = `1000 * (last_token - first_token) / (output_tokens - 1)`

차이는 분자에서 `request_start ↔ first_token`(= TTFT만큼 차이), 분모에서 `N ↔ N-1`(=1 차이). 큰 N과 작은 TTFT/E2EL 비율에서 둘은 수렴. **즉 코드 정의상 두 값은 거의 같게 나오도록 설계**되어 있고, K-Perf가 두 지표를 동시 보고할 때 의미 차이를 명확히 설명하지 않으면 사용자가 혼동할 수 있다.

## 9. 본 단계에서 의도적으로 미열람

- `outputs/csv.py`, `outputs/html.py`, `outputs/serialized.py`의 출력 포맷 본문 — 표 컬럼 의미와는 별개로 단위 표기/소수자리 처리가 어디서 결정되는지.
- `rate_distribution_from_timings`의 `threshold=1e-4`(1/10 ms) 이내 이벤트 병합 정책의 영향.
- `SchedulerState` 본문 — `start_requests_time`/`end_processing_time`/`processing_requests` 같은 필드 정의.
- per-user TPS 출력 위치 (콘솔에서는 `output_tokens_per_second`(시스템) 만 보였음).

위 항목 중 per-user TPS 위치 확인은 K-Perf "per-user TPS" 요구가 있을 때 다음 단계 우선순위.
