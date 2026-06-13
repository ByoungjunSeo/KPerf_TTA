# 02. `guidellm benchmark run` 호출 체인 추정 — 통신 접점 식별

> 분석 방식: import 그래프 + 클래스/메서드 시그니처 + grep으로 식별한 정적 호출 관계.
> **함수 본문 로직은 미열람**(예: `for` 루프의 조건, 예외 흐름 등은 다음 단계). 다만 어느 줄에서 외부 호출이 발생하는지의 **앵커(파일:라인)** 는 grep으로 확정.
> 명령 가정: `guidellm benchmark run --target http://... --data <hf-id|path|json> [--model X --processor Y]`

## 1. 한눈에 보는 호출 체인

```
[Python entry] python -m guidellm  ──► guidellm/__main__.py:6
                                    └─ from guidellm.cli import cli; cli()

[click root]   cli (group)          ──► guidellm/cli/__init__.py:38
                                    └─ add_command(benchmark)

[click group]  benchmark (group)    ──► guidellm/cli/benchmark/__init__.py:20
                                    └─ DefaultGroupHandler(default="run")
                                    └─ add_command(run)

[click cmd]    run (command)        ──► guidellm/cli/benchmark/run.py:329 def run(**kwargs)
                                    ├─ BenchmarkGenerativeTextArgs.create(scenario, **kwargs)   ← schemas/generative/entrypoints
                                    └─ asyncio.run(benchmark_generative_text(args, progress, console))

[orchestrator] benchmark_generative_text  ──► guidellm/benchmark/entrypoints.py:432
                                          │
                                          │   ┌───── 1) Backend 준비 ─────┐
                                          ├─► resolve_backend(args.backend_kwargs)         (entrypoints.py:81)
                                          │   ├─ Backend.create(args)                     (backends/backend.py)
                                          │   │   └─ OpenAIHTTPBackend(...)               (backends/openai/http.py)
                                          │   ├─ await backend.process_startup()          (openai/http.py:212-244)  ★ httpx.AsyncClient 생성
                                          │   ├─ await backend.validate()                 (openai/http.py:271-295)  ★★ 첫 HTTP 요청 (서버 헬스/모델 목록)
                                          │   ├─ await backend.default_model()            (openai/http.py:298-310)  ★★ GET /v1/models
                                          │   └─ await backend.process_shutdown()         (openai/http.py:256-262)
                                          │
                                          │   ┌───── 2) 토크나이저 준비 ─┐
                                          ├─► resolve_processor(args.processor, model)   (entrypoints.py:142)
                                          │   └─ (실제 from_pretrained 호출은 ProcessorFactory 안에서 지연 실행)
                                          │       └─ guidellm/data/processor.py:24 AutoTokenizer.from_pretrained(...)  ★★★ HF Hub fetch
                                          │
                                          │   ┌───── 3) 데이터 파이프라인 ─┐
                                          ├─► resolve_request_loader(...)                (entrypoints.py:229)
                                          │   └─ DataLoaderRegistry.create(config=..., processor_factory=ProcessorFactory(...))
                                          │      └─ TorchDataLoader (run.py에서 kind="pytorch" 강제)
                                          │         └─ 내부에서 DatasetDeserializerFactory.create(data_arg)
                                          │            ├─ HuggingFaceDatasetDeserializer  → load_dataset(id, ...)     ★★★ HF Hub fetch
                                          │            ├─ TextFile/CSV/JSON/Parquet/Arrow DatasetDeserializer → load_dataset("csv"/"json"/...) (로컬 경로 OK)
                                          │            ├─ SyntheticTextDatasetDeserializer (로컬 생성, 외부 없음)
                                          │            └─ InMemoryDict/ItemList DatasetDeserializer (외부 없음)
                                          │         └─ preprocessors 적용 (`encode_media` 기본)
                                          │            └─ MediaEncoder → guidellm/extras/{audio,vision}.py
                                          │               └─ httpx.get(URL)  ★ 사용자 데이터가 URL일 때만
                                          │
                                          │   ┌───── 4) 프로파일·출력 ─────┐
                                          ├─► resolve_profile(...)                       (entrypoints.py:309)  — 외부 호출 없음
                                          ├─► resolve_output_formats(...)                (entrypoints.py:398)  — 외부 호출 없음(파일 시스템만)
                                          │
                                          │   ┌───── 5) 실제 벤치마크 루프 ─┐
                                          └─► Benchmarker().run(...)                     (benchmark/benchmarker.py)
                                              └─ Scheduler().run(requests, backend, strategy, env)            (scheduler/scheduler.py)
                                                 └─ WorkerProcessGroup(requests, backend, strategy)          (scheduler/worker_group.py)
                                                    └─ create_processes() → 다수의 WorkerProcess              (scheduler/worker.py)
                                                       └─ WorkerProcess.run_async()
                                                          ├─ await self.backend.process_startup()  (worker.py:266)  ★ 각 워커에서 AsyncClient 재생성
                                                          ├─ await self.backend.validate()         (worker.py:268)  ★★ 워커별 헬스체크
                                                          ├─ _process_requests_loop()
                                                          │   └─ _process_next_request → _schedule_request
                                                          │      └─ async for resp, info in self.backend.resolve(...) (worker.py:397) ★★★★ 매 요청마다 HTTP
                                                          │         └─ OpenAIHTTPBackend.resolve()  (openai/http.py:323)
                                                          │            ├─ _resolve_non_streaming → self._async_client.request(**)   (openai/http.py:451)
                                                          │            └─ _resolve_streaming     → self._async_client.stream(**)    (openai/http.py:483)
                                                          └─ await self.backend.process_shutdown() (worker.py:286)
```

기호:
- ★ = 외부 통신 가능성(조건부)
- ★★ = 외부 통신 발생(서버 측 응답 의존)
- ★★★ = 외부 통신 발생(HF Hub 또는 사용자 URL)
- ★★★★ = 벤치마크의 정상 동작 시 가장 빈번한 외부 호출 지점(매 요청 = 1 HTTP)

## 2. 통신 접점 파일·라인 인덱스

벤치마크 1회 실행에서 **실제로 외부 네트워크를 칠 수 있는 지점**을 한 표로 모음:

| # | 시점(체인상의 단계) | 파일:라인 | 메서드 | 대상 호스트 | 회피 가능성 |
|---|---|---|---|---|---|
| 1 | (1) Backend startup | `backends/openai/http.py:233-244` | `httpx.AsyncClient(...)` 생성 | (없음 — 클라이언트 생성만) | — |
| 2 | (1) Backend validate | `backends/openai/http.py:285` | `await self._async_client.request(**validate_kwargs)` | **`--target` 호스트** (예: `localhost:8000`) | ✅ 사내 추론 서버면 폐쇄망 OK |
| 3 | (1) default_model 조회 | `backends/openai/http.py:305` | `await self._async_client.get(target, headers=...)` | 동일(`/v1/models`) | ✅ 동일 |
| 4 | (2) ProcessorFactory가 토크나이저 로드 | `data/processor.py:24` | `AutoTokenizer.from_pretrained(processor_or_model_id, ...)` | **HF Hub (`huggingface.co`)** 기본값 | ⚠️ `HF_HOME` 사전캐시 + `TRANSFORMERS_OFFLINE=1` 필요 |
| 5 | (2) 보조 토크나이저 헬퍼 | `utils/hf_transformers.py:23` | 동일 패턴 | 동일 | 동일 |
| 6 | (3) HF Hub 데이터셋 로드 | `data/deserializers/huggingface.py:75, 95` | `load_dataset(str(data), **kwargs)` | **HF Hub (`huggingface.co/datasets`)** | ⚠️ 동일. `HF_DATASETS_OFFLINE=1` 필요 |
| 7 | (3) 파일 기반 데이터셋 | `data/deserializers/file.py:109, 131, 153, 175, 241` | `load_dataset("csv"/"json"/"parquet"/"arrow"/"webdataset", data_files=local_path)` | **로컬 파일만** | 🟢 외부 통신 없음 |
| 8 | (3) Trace 데이터 입출력 | `utils/trace_io.py:48` | `load_dataset(...)` | (인자에 따라 로컬 또는 HF Hub) | ⚠️ |
| 9 | (3) Preprocessor가 URL 데이터 fetch | `extras/audio.py`, `extras/vision.py` | `httpx.<...>` (사용자 데이터 안의 URL이 입력일 때) | **사용자 제공 URL** | 🟡 사용자가 URL을 안 주면 미발생 |
| 10 | (3) Mock 서버 토크나이저 로드 | `mock_server/handlers/{chat_completions, completions, responses, tokenizer}.py` | `AutoTokenizer.from_pretrained(config.processor)` / `PreTrainedTokenizer.from_pretrained(...)` | HF Hub 기본값 | ⚠️ Mock 서버를 폐쇄망에서 띄울 거면 사전 캐시 |
| 11 | (3) HF Hub로 데이터셋 푸시 | `data/builders.py:541-547` | `processed_dataset.push_to_hub(hub_dataset_id, token=hf_token)` | HF Hub (쓰기) | 🟢 `--push-to-hub` 옵션을 안 주면 미발생 |
| 12 | (5) 매 요청 비스트리밍 | `backends/openai/http.py:451` | `await self._async_client.request(**request_kwargs)` | **`--target` 호스트** | ✅ |
| 13 | (5) 매 요청 스트리밍 | `backends/openai/http.py:483` | `async with self._async_client.stream(**request_kwargs) as stream` | 동일 | ✅ |
| 14 | (출력) HTML 리포트 자산 | `settings.py:61` 의 기본값 `https://vllm-project.github.io/guidellm/ui/v0.5.4/index.html` 가 `outputs/html.py`에서 읽힐 가능성 | (다음 단계에서 동적 검증) | **GitHub Pages** | 🔴 폐쇄망에선 자산 미러링 또는 `GUIDELLM__REPORT_GENERATION__SOURCE` 환경변수 오버라이드 필요 |

> 12 / 13 번은 **정상 벤치마크 트래픽**(원래 측정 대상). 다른 항목은 측정 대상 외 통신.

## 3. 통신 발생 빈도 가설 (정성)

| 단계 | 발생 시점 | 빈도 |
|---|---|---|
| #1~3 (Backend startup/validate/default_model) | 부트스트랩 시 1회 + 워커별 1회 | O(워커 수) |
| #4~5 (ProcessorFactory tokenizer) | 토크나이저 캐시 미스 시 1회 | 캐시 워밍하면 0회 |
| #6 (HF Hub dataset) | 데이터 캐시 미스 시 1회 | 캐시 워밍하면 0회 |
| #7 (로컬 파일) | N/A — 로컬 파일 IO만 | 0회 |
| #9 (URL 미디어 fetch) | 사용자 데이터 안에 URL이 있을 때만 | 사용 데이터에 의존 |
| #12~13 (per-request HTTP) | **벤치마크 본 트래픽** | O(요청 수) — 수만~수십만 |
| #14 (HTML 자산) | HTML 출력 시 1회 | 출력 포맷에 따라 |

## 4. 폐쇄망(에어갭) 적용 시 점검 순서 (제안)

1. **`--target`은 사내 추론 서버**로 한정 → #2/#3/#12/#13은 자동으로 사내망 안. **OK.**
2. **`--processor`/`--model`는 토크나이저까지 포함해 사전 캐시 워밍** 후, `HF_HOME=/내부캐시 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1`로 실행. → #4/#5/#6/#10 회피.
3. **`--data`는 로컬 파일 경로/synthetic만 허용**하는 정책 → #6/#11 회피, #7로 수렴.
4. **`--data-preprocessors`에서 `encode_media`가 사용자 URL을 만나는 경로**(#9)를 사용 규약으로 금지하거나, fetch 단계에서 `is_url`가 True여도 즉시 fail-fast 하도록 패치 검토.
5. **HTML 출력 자산**(#14)는 `outputs/html.py` 내부 사용 패턴 확인 후, (a) 사내 미러 호스팅 또는 (b) `GUIDELLM__REPORT_GENERATION__SOURCE`를 `file:///...` 로 강제. **동적 검증은 3단계로 미룸.**

## 5. 본 문서에서 못 짚은 것 (의도적 미열람)

- 함수 본문(특히 `Scheduler.run`, `WorkerProcess._process_requests_loop`, `OpenAIHTTPBackend.resolve` 내부) — 재시도·예외·타임아웃·스트림 파싱 등 동적 행동.
- 멀티프로세스 IPC(`utils/messaging.py`, `utils/encoding.py`) 의 동작 메커니즘.
- `Profile` 의 `sweep / concurrent / poisson / constant` 등 각 모드별 스케줄링 결정 로직.
- HTML 리포트가 실제로 `settings.source`를 어떤 방식으로 끌어다 쓰는지(다운로드 vs. iframe).

위 항목은 모두 **3단계(런타임 동작·실제 호출 흐름) 또는 4단계(외부 호출 차단 정책 검증)** 에서 다룰 후보다.
