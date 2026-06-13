# 00. K-Perf 2단계 분석 요약 (GuideLLM 코드 지도, C 레벨)

> Scope: `vllm-project/guidellm` @ `fb3e862`. 모듈 docstring + 시그니처(`ast`) + grep 기반 정적 분석. 함수 본문 미열람.

## 9개 패키지 1줄 요약

| # | 패키지 | 역할 (모듈 docstring 기반) | 외부 통신 관련성 |
|---|---|---|---|
| 1 | `backends/` | 추론 서버와 통신하는 백엔드 추상화 + OpenAI/HTTP·vLLM/Python 구현 | 🔴 핵심 — httpx 호출 모두 여기 |
| 2 | `benchmark/` | 벤치마크 오케스트레이션·프로파일·진행률·출력 포맷·리포트 스키마 | 🟡 HTML 출력 시 GitHub Pages 자산 참조(`settings.py:61`) |
| 3 | `cli/` | click 기반 CLI. `benchmark/run/from_file`, `preprocess/dataset`, `mock-server`, `config` 4그룹 | 🟢 외부 통신 없음 |
| 4 | `data/` | deserializer→preprocessor→loader→finalizer 4단 파이프라인 + HF/synthetic/file/in-memory 등 다중 소스 지원 | 🟡 `load_dataset` HF 경로 6곳, `push_to_hub` 1곳 |
| 5 | `extras/` | 옵셔널 의존성(`audio/vision/vllm`)이 필요한 코드 격리 | 🟡 사용자 URL 데이터 fetch 가능(httpx) |
| 6 | `mock_server/` | OpenAI/vLLM API 호환 Mock 서버(sanic). chat/completions/responses/tokenizer 핸들러 | 🟡 토크나이저 로드 시 HF 사용 |
| 7 | `scheduler/` | 멀티프로세스 워커 그룹·스케줄링 전략·제약조건. ThreadSafeSingleton 기반 `Scheduler` | 🔴 워커가 backend.resolve를 호출 — per-request HTTP 발생 지점 |
| 8 | `schemas/` | pydantic 모델 (`GenerationRequest/Response`, `RequestInfo`, statistics 등) | 🟢 |
| 9 | `utils/` | 레지스트리·싱글톤·CLI 파서·콘솔·IPC 인코딩·HF 헬퍼·BSD-3 default_group 등 | 🟡 `hf_transformers.py`, `trace_io.py`, `text.py`에 외부 호출 흔적 |

## 호출 체인 핵심 (벤치마크 1회 실행 기준)

```
__main__.py:6
  → cli/__init__.py:38 (click root)
    → cli/benchmark/__init__.py (DefaultGroupHandler, default=run)
      → cli/benchmark/run.py:329 def run(**kwargs)
        → BenchmarkGenerativeTextArgs.create(...)
        → asyncio.run(benchmark_generative_text(args, ...))
          → resolve_backend → Backend.create → OpenAIHTTPBackend
              → process_startup / validate / default_model / process_shutdown
          → resolve_processor → (지연된 from_pretrained)
          → resolve_request_loader → DataLoaderRegistry → TorchDataLoader
              → DatasetDeserializerFactory → load_dataset(...)
              → MediaEncoder → extras/{audio,vision}.py (URL이면 httpx.get)
          → resolve_profile / resolve_output_formats
          → Benchmarker().run
            → Scheduler().run  (싱글톤)
              → WorkerProcessGroup.create_processes → WorkerProcess(s)
                → WorkerProcess.run_async
                  → backend.process_startup / validate (워커별)
                  → loop: backend.resolve → httpx.request / .stream  ★ per-request
```

## 통신 접점 — 가장 중요한 5개 (파일:라인)

| 단계 | 위치 | 의미 |
|---|---|---|
| 벤치 본 트래픽(스트리밍) | `backends/openai/http.py:483` `async with self._async_client.stream(**request_kwargs)` | 측정 대상 |
| 벤치 본 트래픽(비스트리밍) | `backends/openai/http.py:451` `await self._async_client.request(**request_kwargs)` | 측정 대상 |
| Backend validate(워커마다) | `backends/openai/http.py:285` 및 `scheduler/worker.py:268` | 사내 서버 헬스체크 |
| 토크나이저 fetch | `data/processor.py:24` `AutoTokenizer.from_pretrained(...)` | HF Hub. 사전 캐시로 회피 |
| 데이터셋 fetch | `data/deserializers/huggingface.py:75, 95` `load_dataset(...)` | HF Hub. 사전 캐시 또는 로컬 파일 사용으로 회피 |

## K-Perf 관점 즉시 의사결정 이슈

1. **OpenAI HTTP 백엔드 단일 의존 여부 확정**: 현재 기본값은 `openai_http`이며, vLLM Python 백엔드는 `HAS_VLLM` 가용성 조건부. K-Perf 1차 릴리스가 **OpenAI 호환 API만 지원**으로 가도 되는지, 혹은 별도 NPU SDK 직접 호출 백엔드를 추가해야 하는지 빠른 결정 필요 — `backends/backend.py:Backend.create` + `RegistryMixin` 구조로 새 백엔드 추가는 가능.
2. **워커당 backend 재초기화 모델 수용 여부**: `WorkerProcess.run_async`가 워커마다 `backend.process_startup()`을 다시 호출 → 워커가 N개면 startup/validate가 N회. 측정 시작 직전에 N회의 GET/POST가 사내 서버로 발생함을 K-Perf 운영 가이드에 명기 필요.
3. **`encode_media` 기본 활성**: `cli/benchmark/run.py:357`이 `data_preprocessors`가 비어 있으면 자동으로 `[{"kind": "encode_media"}]`를 끼움. 폐쇄망에서 데이터셋에 URL이 섞여 있으면 `extras/vision.py`/`audio.py`의 httpx가 외부로 나갈 수 있음 — **기본값을 K-Perf에선 빈 리스트로 바꿀지** 정책 결정 필요.
4. **HTML 출력 자산 처리(`settings.py:61`)**: 본 단계에서는 `outputs/html.py`가 그 URL을 어떻게 쓰는지 본문을 보지 않았음(C 레벨 제약). 3단계에서 (a) 빌드타임 자산 fetch인지 (b) 런타임 iframe 임베드인지 확인 후 폐쇄망 우회 방법을 정한다.
5. **`Scheduler`가 `ThreadSafeSingletonMixin`** : 단일 프로세스 내에선 1개 싱글톤이지만 워커 프로세스 안에서 별도로 다시 만들어짐. K-Perf가 동일 프로세스에서 **여러 벤치마크를 순차 실행**할 경우, 싱글톤 상태가 누적되어 영향이 있는지 3단계에서 확인 필요(기본적인 fresh-state 보장 메커니즘이 있는지).
