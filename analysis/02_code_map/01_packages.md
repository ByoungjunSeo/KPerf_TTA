# 01. GuideLLM 코드 지도 (C 레벨)

> 분석 대상: `vllm-project/guidellm` @ `fb3e862` (main)
> 분석 깊이: **C 레벨(파일·모듈 표면)**. 각 파일의 모듈 docstring 첫 줄과 `__all__` / 최상위 클래스·함수명만 사용. 함수 본문 로직은 의도적으로 미열람.
> 출처: `ast.get_docstring` 자동 추출 + `ast.walk` 클래스/함수 수집.

## 0. 전체 그림

`src/guidellm/` 구성:

| 계층 | 모듈 |
|---|---|
| 최상위 모듈 | `__init__.py`, `__main__.py`, `logger.py`, `settings.py`, `version.py` |
| **9개 서브패키지** | `backends/`, `benchmark/`, `cli/`, `data/`, `extras/`, `mock_server/`, `scheduler/`, `schemas/`, `utils/` |

서브패키지 간 의존 방향(`import` 그래프, 정적 grep 기반 요약):

```
cli ─┬─► benchmark ─┬─► backends ─► extras (vllm 가용성 체크)
     │              ├─► scheduler ─► utils
     │              ├─► data
     │              ├─► schemas
     │              └─► utils
     ├─► preprocess(cli) ─► data
     └─► mock_server(cli) ─► mock_server(pkg)

extras ─► vision / audio / vllm (optional deps 게이팅)
schemas / utils 는 누구나 import 가능한 "leaf 가까운" 계층
```

> "scheduler가 backend를 워커 프로세스 안에서 사용한다"라는 런타임 결합은 동적이며, 다음 절(`02_benchmark_call_chain.md`)에서 자세히 다룬다.

## 1. 최상위 파일

| 경로 | 모듈 docstring 첫 줄 / 핵심 export |
|---|---|
| `__init__.py` | "Guidellm is a package that provides an easy and intuitive interface for…" — uvloop 설치 시도 |
| `__main__.py` | "GuideLLM command-line interface entry point" — `from guidellm.cli import cli; cli()` |
| `logger.py` | "Logger configuration for GuideLLM." — loguru 기반 |
| `settings.py` | 클래스: `LoggingSettings`, `DatasetSettings`, `ReportGenerationSettings`, `Settings` / 함수: `reload_settings`, `print_config` / `__all__ = settings 외 5개` |
| `version.py` | (정적 분석: 버전 문자열 노출용으로 추정) |

`Settings`는 `pydantic-settings.BaseSettings` 기반으로 `GUIDELLM__` 접두어 환경변수를 받는다. `ReportGenerationSettings.source` 기본값이 외부 GitHub Pages URL(1단계 04 문서 참조).

---

## 2. 패키지 1 — `backends/`

> docstring: "Backend infrastructure for GuideLLM language model interactions."

| 파일 | 표면 |
|---|---|
| `backend.py` | "Backend interface and registry for generative AI model interactions." — `class BackendArgs`, `class Backend` (`create`, `__init__`, `processes_limit`, `requests_limit`, `default_model`) |
| `openai/http.py` | "OpenAI HTTP backend implementation for GuideLLM." — `class OpenAIHTTPBackendArgs` + `class OpenAIHTTPBackend`. 메서드: `process_startup`, `process_shutdown`, `validate`, `available_models`, `default_model`, **`resolve`**, `_resolve_non_streaming`, `_resolve_streaming`, `_aiter_lines`, `_build_headers`, `_check_tool_call_expectations` |
| `openai/__init__.py` | `__all__`: `AudioRequestHandler, ChatCompletionsRequestHandler, OpenAIHTTPBackend, OpenAIRequestHandler, OpenAIRequestHandlerFactory, ResponsesRequestHandler, TextCompletionsRequestHandler` |
| `openai/request_handlers.py` | "Request handlers for formatting requests and processing API responses from…" |
| `vllm_python/__init__.py` | "VLLM Python API backend package." |
| `vllm_python/vllm.py` | "VLLM Python API backend implementation for GuideLLM." |
| `vllm_python/vllm_response.py` | "VLLM-specific response handler for building GenerationResponse from vLLM output." |

핵심 진입점: `Backend.create(args) → OpenAIHTTPBackend` (`args.kind == "openai_http"` 기본값) 또는 `VLLMPythonBackend` (vLLM 라이브러리 가용 시).

---

## 3. 패키지 2 — `benchmark/`

> docstring: "Benchmark execution and performance analysis framework."

| 파일 | 표면 |
|---|---|
| `benchmarker.py` | "Benchmark execution orchestration and lifecycle management." — `class Benchmarker.run(self, accumulator_class, benchmark_class, requests, backend, profile, environment, warmup, cooldown, sample_requests, prefer_response_metrics, progress)` |
| `entrypoints.py` | "Primary interface for executing and re-importing generative text benchmarks." — 함수: `resolve_backend`, `resolve_processor`, `resolve_item_from_registry`, `resolve_request_loader`, `resolve_profile`, `resolve_output_formats`, **`benchmark_generative_text`**, `reimport_benchmarks_report` |
| `profiles.py` | "Orchestrate multi-strategy benchmark execution through configurable profiles." — `Profile`, `ProfileType` |
| `progress.py` | "Progress tracking and console display for benchmark execution monitoring." — `GenerativeConsoleBenchmarkerProgress` |
| `outputs/output.py` | "Base output interface for generative benchmarking results." |
| `outputs/console.py` | "Console output formatter for generative benchmarker results." — `GenerativeBenchmarkerConsole` |
| `outputs/csv.py` | "CSV output formatter for benchmark results." |
| `outputs/html.py` | "HTML output formatter for benchmark results." (← `settings.source` GitHub Pages 자산 가져오는 후보) |
| `outputs/serialized.py` | "Serialized output handler for generative benchmark reports." (json) |
| `outputs/__init__.py` | "Output formatters for benchmark results." — `GenerativeBenchmarkerOutput`, `GenerativeBenchmarkerConsole` |
| `scenarios/__init__.py` | "Builtin benchmark scenario definitions and discovery utilities." — `get_builtin_scenarios()` |
| `schemas/base.py` | "Base schemas for benchmark execution, metric accumulation, and result compilation." — `TransientPhaseConfig` |
| `schemas/generative/benchmark.py` | `GenerativeBenchmark` |
| `schemas/generative/accumulator.py` | `GenerativeBenchmarkAccumulator` |
| `schemas/generative/entrypoints.py` | `BenchmarkGenerativeTextArgs` (CLI 전체 인자가 모이는 pydantic 모델) |
| `schemas/generative/metrics.py` | 메트릭 스키마(TTFT, ITL 등) |
| `schemas/generative/report.py` | `GenerativeBenchmarksReport` (`load_file`, `benchmarks` 리스트) |

핵심 진입점: `benchmark_generative_text(args, progress, console)` — `cli/benchmark/run.py:418`에서 `asyncio.run()` 으로 호출.

---

## 4. 패키지 3 — `cli/`

> docstring: "GuideLLM command-line interface entry point."

| 파일 | 표면 |
|---|---|
| `__init__.py` | click `cli` 그룹. 4개 서브커맨드 등록: `config`, `mock_server`, `benchmark`, `preprocess` |
| `config.py` | "Configuration display command." — `guidellm config` |
| `mock_server.py` | "Mock server command for testing." — `guidellm mock-server` |
| `benchmark/__init__.py` | "Benchmark command group." — `DefaultGroupHandler`, default=`run`. 등록: `run`, `from_file` |
| `benchmark/run.py` | "Benchmark run command." — 50+ click 옵션, 본문에서 `BenchmarkGenerativeTextArgs.create(...)` → `asyncio.run(benchmark_generative_text(args=...))` |
| `benchmark/from_file.py` | "Benchmark from-file command." — 저장된 리포트 재가공 |
| `preprocess/__init__.py` | "Preprocess command group." |
| `preprocess/dataset.py` | "Dataset preprocessing command." — `guidellm preprocess dataset` |

> CLI 전체가 `click` 기반. 환경변수 자동 접두어는 `GUIDELLM` (`context_settings={"auto_envvar_prefix": "GUIDELLM"}`). 외부 명령 호출/시스템 콜은 없음.

---

## 5. 패키지 4 — `data/`

| 파일 | 표면 |
|---|---|
| `__init__.py` | `__all__`로 20개 + 심볼 re-export (DataArgs, DataEntrypointArgs, DataLoaderRegistry, GenerativeRequestCollator, ProcessorFactory 등) |
| `builders.py` | `class ShortPromptStrategy`, `ShortPromptStrategyHandler`, `PromptTooShortError` / 함수: `parse_synthetic_config`, **`process_dataset`** (HF Hub push 포함; `HF_TOKEN` 사용 지점) |
| `collators.py` | `class GenerativeRequestCollator` |
| `config.py` | 함수: `load_config`, `_load_config_dict`, `_load_config_file`, `_load_config_str` |
| `entrypoints.py` | 함수: `process_dataset` (CLI preprocess 진입점) |
| `processor.py` | `class ProcessorFactory` (HF `AutoTokenizer.from_pretrained` 1지점) |
| `deserializers/deserializer.py` | `DatasetDeserializer`, `DatasetDeserializerFactory` |
| `deserializers/file.py` | `class TextFile/CSVFile/JSONFile/ParquetFile/ArrowFile DatasetDeserializer` + `FileDataArgs`; `__all__`에 `DBFileDatasetDeserializer`, `HDF5FileDatasetDeserializer`, `TarFileDatasetDeserializer` 도 등장(파일 내 클래스 정의는 별도 경로일 가능성) |
| `deserializers/huggingface.py` | `HuggingFaceDataArgs`, `HuggingFaceDatasetDeserializer` (`load_dataset(...)` 호출) |
| `deserializers/memory.py` | `InMemoryDict*`, `InMemoryItemList*` 6종 |
| `deserializers/synthetic.py` | `SyntheticTextPrefixBucketConfig`, `SyntheticTextDataArgs`, `_SyntheticTextExamplesIterable`, `SyntheticTextDataset`, `SyntheticTextDatasetDeserializer` |
| `deserializers/trace_synthetic.py` | "Trace file deserializer that generates synthetic prompts per row." |
| `loaders/loader.py` | `DataLoader`, `DataLoaderRegistry` (factory) |
| `loaders/torch.py` | `TorchDataLoaderArgs`, `DatasetsIterator`, `TorchDataLoader` (`pytorch` loader, run.py에서 강제 사용) |
| `preprocessors/preprocessor.py` | `DatasetPreprocessor`, `DataDependentPreprocessor`, `PreprocessorRegistry` |
| `preprocessors/encoders.py` | `MediaEncoderArgs`, `MediaEncoder` (`encode_media` preprocessor, run.py 기본값) |
| `preprocessors/mappers.py` | `GenerativeColumnMapperArgs`, `GenerativeColumnMapper`, `PoolingColumnMapper` |
| `preprocessors/tool_calling.py` | "Preprocessor for extracting prompts from tool calling datasets." — `ToolCallingMessageExtractor` |
| `preprocessors/turn_pivot.py` | `TurnPivotArgs`, `TurnPivot` |
| `finalizers/finalizer.py` | `DatasetFinalizer`, `FinalizerRegistry` |
| `finalizers/generative.py` | `GenerativeRequestFinalizerConfig`, `GenerativeRequestFinalizer` (run.py 기본 `kind=generative`) |
| `schemas/base.py` | `DataNotSupportedError`, `GenerativeDatasetColumnType` |
| `schemas/entrypoints.py` | `DataLoaderArgs`, `DataArgs`, `DataPreprocessorArgs`, `DataFinalizerArgs`, `DataEntrypointArgs` |
| `schemas/preprocess.py` | `PreprocessDatasetConfig` |
| `utils/dataset.py` | `resolve_dataset_split`, `DEFAULT_SPLITS` |

> `data/` 는 deserializer → preprocessor → loader → finalizer 의 4단계 파이프라인. 모든 등록은 `*Registry` 패턴(런타임 등록·문자열 키 조회).

---

## 6. 패키지 5 — `extras/`

> docstring: "Code that depends on optional dependencies. Each submodule should be deferred imported."

| 파일 | 표면 |
|---|---|
| `audio.py` | 함수: `is_url`, `encode_audio`, `_decode_audio`, `_encode_audio`, `get_file_name`. `httpx` 사용(URL fetch) |
| `vision.py` | 함수: `is_url`, `encode_image`, `resize_image`, `image_dict_to_pil`, `encode_video`, `get_file_format`. `httpx` 사용 |
| `vllm.py` | `HAS_VLLM` 가용성 플래그 export (다른 모듈이 이를 import해서 분기) |
| `__init__.py` | 모듈 docstring만 |

> 옵셔널 의존성(`audio`/`vision`/`vllm` extras) 사용 코드를 한 곳에 격리. **여기에서 `httpx`로 사용자 URL을 직접 fetch한다 → 폐쇄망에서 사용자가 URL 데이터를 줄 때 차단 지점**.

---

## 7. 패키지 6 — `mock_server/`

> docstring: "GuideLLM Mock Server for OpenAI and vLLM API compatibility."

| 파일 | 표면 |
|---|---|
| `__init__.py` | 모듈 docstring |
| `server.py` | "High-performance mock server for OpenAI and vLLM API compatibility testing." (sanic 기반) |
| `config.py` | "Configuration settings for the mock server component." |
| `models.py` | "Pydantic models for OpenAI API and vLLM API request/response validation." |
| `utils.py` | "Mock server utilities for text generation and tokenization testing." |
| `handlers/__init__.py` | "HTTP request handlers for the GuideLLM mock server." |
| `handlers/chat_completions.py` | "OpenAI Chat Completions API endpoint handler for the mock server." |
| `handlers/completions.py` | "Legacy OpenAI Completions API handler for the mock server." |
| `handlers/responses.py` | "OpenAI Responses API endpoint handler for the mock server." |
| `handlers/tokenizer.py` | "HTTP request handler for vLLM tokenization API endpoints in the mock server." |

> `mock_server`는 **수신(서버)** 측이라 외부로 connect-out 하지 않음(단, `from_pretrained`로 HF에서 토크나이저는 끌어옴). 폐쇄망에서 자체 호스팅용으로 활용 가능.

---

## 8. 패키지 7 — `scheduler/`

> docstring: "Scheduler subsystem for orchestrating benchmark workloads and managing worker processes."

| 파일 | 표면 |
|---|---|
| `__init__.py` | re-exports (`Scheduler`, `Constraint`, `ConstraintInitializer`, `NonDistributedEnvironment`, `StrategyType` 등) |
| `scheduler.py` | "Thread-safe singleton scheduler for distributed benchmarking workload coordination." — `class Scheduler.run(self, requests, backend, strategy, env)` |
| `worker_group.py` | "Multi-process worker group orchestration for distributed request scheduling." — `WorkerProcessGroup` (create_processes, start, request_updates, shutdown) + `WorkerGroupState` |
| `worker.py` | "Worker process implementation for distributed request execution and coordination." — `WorkerProcess.run/run_async/_process_requests/_schedule_request/_send_update`. **`backend.process_startup/validate/resolve/process_shutdown` 호출 위치(worker.py:266/268/286/397)** |
| `environments.py` | "Environment abstractions for coordinating scheduler execution across distributed nodes." — `Environment`, `NonDistributedEnvironment` |
| `schemas.py` | "Core data structures and interfaces for the GuideLLM scheduler system." (`RequestInfo` 등) |
| `strategies.py` | "Request scheduling strategies for controlling benchmark request processing patterns." — `SchedulingStrategy`, `StrategyType` |
| `constraints/constraint.py` | "Core constraint system protocols and base classes." |
| `constraints/error.py` | "Error-based constraint implementations." |
| `constraints/request.py` | "Request-based constraint implementations." |
| `constraints/saturation.py` | "Over-saturation detection constraint implementation." |
| `constraints/factory.py` | "Factory for creating and managing constraint initializers." — `ConstraintsInitializerFactory` |

> `Scheduler`는 `ThreadSafeSingletonMixin` 기반 싱글톤. 워커 그룹 → 워커 프로세스가 다중 프로세스로 분리되며, **각 워커 프로세스 안에서 backend 인스턴스가 부활(process_startup)** → 실제 HTTP 호출은 워커가 발생.

---

## 9. 패키지 8 — `schemas/`

> docstring: "Pydantic schema models for GuideLLM operations."

| 파일 | 표면 |
|---|---|
| `base.py` | "Pydantic utilities for polymorphic model serialization and registry integration." |
| `info.py` | "Core data structures and interfaces for the GuideLLM scheduler system." (`RequestInfo`) |
| `request.py` | "Request schema definitions for generation operations." — `GenerationRequest` |
| `request_stats.py` | "Request statistics and metrics for generative AI benchmark analysis." |
| `response.py` | "Backend response models for request and response handling." — `GenerationResponse` |
| `statistics.py` | "Statistical distribution analysis and summary calculations for benchmark metrics." |
| `tool_call.py` | "Tool call data models for streaming and non-streaming responses." |

---

## 10. 패키지 9 — `utils/`

> docstring: "Utils should be imported from their respective sub-submodules."

| 파일 | 표면 |
|---|---|
| `arg_string.py` | "Utilities for parsing argument strings into Python dictionaries." |
| `auto_importer.py` | "Automatic module importing utilities for dynamic class discovery." |
| `cli.py` | `parse_list`, `parse_list_floats`, `parse_json`, `parse_json_list`, `parse_arguments`, `set_if_not_default`, `class Union` |
| `colors.py` | `class Colors` |
| `console.py` | "Console utilities for rich terminal output and status updates." — `Console` |
| `default_group.py` | **BSD-3-Clause 외부 코드 채택 영역**(`click-default-group` 변종) — `DefaultGroupHandler` |
| `dict.py` | "Utility functions for working with dictionaries." |
| `encoding.py` | "Message encoding utilities for multiprocess communication with Pydantic model support." |
| `env_validator.py` | `validate_env_vars`, `list_set_env`, `get_valid_env_vars`, `_extract_click_env_vars`, `_extract_settings_env_vars`, `_walk_settings_fields` |
| `functions.py` | "Utility functions for safe operations and value handling." |
| `hf_datasets.py` | `save_dataset_to_file` (HF datasets 저장) |
| `hf_transformers.py` | `check_load_processor` (HF tokenizer 로드 헬퍼; `AutoTokenizer.from_pretrained` 1지점) |
| `imports.py` | `__all__ = ['json']` (json 라이브러리 호환 shim 추정) |
| `messaging.py` | "Inter-process messaging abstractions for distributed scheduler coordination." — `InterProcessMessagingQueue` |
| `mixins.py` | "Mixin classes for common metadata extraction and object introspection." — `InfoMixin` |
| `random.py` | `class IntegerRangeSampler` |
| `registry.py` | "Registry system for dynamic object registration and discovery." — `RegistryMixin` |
| `singleton.py` | "Singleton pattern implementations for ensuring single instance classes." — `ThreadSafeSingletonMixin` |
| `synchronous.py` | "Async utilities for waiting on synchronization objects." |
| `text.py` | "Text processing utilities for content manipulation and formatting operations." (`httpx` import 있음 — URL→텍스트 다운로드 추정) |
| `trace_io.py` | "Shared trace file I/O for replay benchmarks." (HF `datasets.load_dataset` 사용) |
| `typing.py` | `get_literal_vals` |

> `utils/registry.py`와 `utils/singleton.py`가 패키지 전체 디자인 패턴의 기둥. 모든 deserializer/preprocessor/loader/finalizer/backend가 `RegistryMixin` 기반 문자열 키 등록.
