# 04. 외부 통신 · 텔레메트리 · 폐쇄망 적합성

> 분석 대상: `vllm-project/guidellm` @ `fb3e862`
> 본 문서는 **정적 grep 결과만** 정리한다. 함수가 실제로 네트워크를 호출하는지·조건부인지의 동적 검증은 다음 단계.

## D-1. HTTP 라이브러리 import

`grep -rIn -E '\b(import requests|from requests|import urllib|from urllib|import httpx|from httpx|import aiohttp|from aiohttp)\b' src/guidellm/` 결과:

| 파일 | 라인 | 토큰 | 비고 |
|---|---|---|---|
| `src/guidellm/backends/openai/http.py` | 18 | `import httpx` | OpenAI 호환 백엔드 |
| `src/guidellm/backends/openai/http.py` | 39 | (주석: `# NOTE: This value is taken from httpx's default`) | 코드 아님 |
| `src/guidellm/extras/audio.py` | 6 | `import httpx` | 오디오 자산 fetch 추정 |
| `src/guidellm/extras/vision.py` | 8 | `import httpx` | 비전 자산 fetch 추정 |
| `src/guidellm/utils/text.py` | 20 | `import httpx` | 텍스트 자산 fetch 추정 |

- `requests`, `urllib`, `aiohttp` 직접 import는 **0건** (다만 `requests`는 `transformers`/`huggingface_hub` 등 의존성 안에서 전이로 사용될 수 있음).

## D-2. URL 리터럴 (`https?://`) — src/guidellm 내부

총 11건. 비주석 라인만 추림:

| 파일:라인 | URL (대표) | 분류 |
|---|---|---|
| `src/guidellm/backends/openai/http.py:188` | `target="http://localhost:8000"` | 기본값/테스트 |
| `src/guidellm/settings.py:61` | `https://vllm-project.github.io/guidellm/ui/v0.5.4/index.html` | **외부 GitHub Pages 자산** (리포트 HTML 템플릿) |
| `src/guidellm/benchmark/schemas/generative/entrypoints.py:65,71` | `target="http://localhost:8000/v1"` | 기본값/예시 |
| `src/guidellm/cli/mock_server.py:130` | `http://{host}:{port}` (포매팅) | 콘솔 출력 문구 |
| `src/guidellm/cli/__init__.py:14` | `--target http://localhost:8000` (docstring 예제) | 문서 |
| `src/guidellm/extras/audio.py:24` | `("http://", "https://")` 접두어 분기 | URL 여부 판정 |
| `src/guidellm/cli/benchmark/run.py:62` | `e.g., http://localhost:8000` (CLI help text) | 도움말 |
| `src/guidellm/extras/vision.py:29` | `("http://", "https://")` | URL 여부 판정 |
| `src/guidellm/extras/vision.py:192` | `image_spec.startswith(("http://", "https://"))` | URL 여부 판정 |
| `src/guidellm/scheduler/constraints/saturation.py:95` | `https://personal.math.ubc.ca/~cbm/aands/` | 수식 출처 참조(주석) |

> **사용자 입력 URL을 그대로 fetch**하는 코드 패턴(`startswith("http")` → 다운로드)이 audio/vision extras에 보임. 폐쇄망에서 그런 URL이 들어오면 실패할 수 있다. 다음 단계에서 fallback/예외 처리 여부 확인 필요.

### D-2-1. UI(`src/ui/`) 측 외부 URL

| 파일 | URL | 의미 |
|---|---|---|
| `src/ui/.env.development` | `ASSET_PREFIX=https://vllm-project.github.io/guidellm/ui/dev` | 개발 빌드용 자산 prefix |
| `src/ui/.env.staging` | `ASSET_PREFIX=https://vllm-project.github.io/guidellm/ui/release/latest` | 스테이징 자산 prefix |
| `src/ui/.env.production` | `ASSET_PREFIX=https://vllm-project.github.io/guidellm/ui/latest` | **프로덕션** 자산 prefix |
| `src/ui/.env.local` / `.env.example` | `http://localhost:3000` | 로컬 |
| `src/ui/lib/store/workloadDetailsWindowData.ts:165` | `http://192.168.4.13:8000` | 목업 사이트 데이터 |

> `settings.py:61`의 기본 리포트 자산도 같은 GitHub Pages 호스트. **폐쇄망 환경에서는 이 자산을 사내로 미러링하거나, GuideLLM 설정으로 로컬 경로로 오버라이드해야 HTML 리포트가 정상 렌더링된다.**

## D-3. HuggingFace · datasets 호출

`grep -rIn -E '\b(huggingface_hub|hf_hub_download|HfApi|snapshot_download|load_dataset|from_pretrained)\b' src/guidellm/`:

| 파일 | 라인 | 토큰 |
|---|---|---|
| `src/guidellm/data/deserializers/file.py` | 8, 109, 131, 153, 175, 241 | `from datasets import Dataset, load_dataset` + `load_dataset("csv"/"json"/"parquet"/"arrow"/"webdataset", ...)` |
| `src/guidellm/data/deserializers/huggingface.py` | 12, 75, 95 | `load_dataset(...)` |
| `src/guidellm/data/processor.py` | 24, 28, 29 | `AutoTokenizer.from_pretrained(...)` |
| `src/guidellm/utils/trace_io.py` | 13, 48 | `from datasets import Dataset, load_dataset`, `load_dataset(...)` |
| `src/guidellm/utils/hf_transformers.py` | 23 | `AutoTokenizer.from_pretrained(...)` |
| `src/guidellm/mock_server/handlers/{completions,responses,chat_completions,tokenizer}.py` | 각 1건 | `PreTrainedTokenizer.from_pretrained(config.processor)` / `AutoTokenizer.from_pretrained(...)` |

- `huggingface_hub`, `hf_hub_download`, `HfApi`, `snapshot_download` 직접 토큰 매치는 0건. **`datasets` / `transformers` API를 통해 간접적으로 HF Hub와 통신**하는 패턴(다운로드 → 캐시).

## D-4. 텔레메트리 / 분석 벤더

`grep -rIn -iE '\b(sentry|posthog|analytics|telemetry|wandb|mlflow|datadog|newrelic|amplitude|segment\.io|opentelemetry)\b' src/guidellm/`:

| 결과 | 0건 |

> **GuideLLM 코드 안에는 외부 텔레메트리/애널리틱스 SDK 매치가 0건.** 의존성 측에서도 `wandb`, `mlflow`, `sentry-sdk`, `opentelemetry-*` 등은 `uv.lock`에서 발견되지 않음.

## D-5. socket / OS 환경변수

| 패턴 | 결과 |
|---|---|
| `import socket` / `from socket` / `socket.` (src/guidellm 내) | **0건** |
| `os.environ` / `os.getenv` / `getenv(` | 2건: `src/guidellm/data/builders.py:541`, `src/guidellm/utils/env_validator.py:45` |

외부 서비스 키 관련:

| 환경 변수 | 매치 위치 | 비고 |
|---|---|---|
| `HF_TOKEN` | `src/guidellm/data/builders.py:534, 538, 541, 542, 544, 547` | `processed_dataset.push_to_hub(hub_dataset_id, token=hf_token)` — **HuggingFace Hub로 push 시에만 사용** |
| `OPENAI_API_KEY` | 0건(코드 본문 내) | 단, OpenAI 백엔드의 인증 헤더 설정은 다른 경로로 들어올 가능성. 다음 단계에서 확인 |
| `HF_HOME`, `HF_DATASETS_OFFLINE`, `TRANSFORMERS_OFFLINE` | 0건(GuideLLM 측 직접 참조) | `datasets`/`transformers` 라이브러리가 자체적으로 해석함 |

또한 `pydantic-settings` 기반의 `Settings` 클래스가 `GUIDELLM__` 접두어로 .env/환경변수에서 설정을 읽음 (`src/guidellm/settings.py:77, 126`).

## D-6. 폐쇄망(에어갭) 적합성 가설

세 줄 요약:

1. **소스 코드 자체의 외부 호출 면적은 작다** — 직접 호출하는 라이브러리는 `httpx`뿐이고, GuideLLM 코드 안에 박힌 외부 호스트는 `vllm-project.github.io` 1개와 예시 `localhost:8000`이 전부. 텔레메트리/애널리틱스는 0건이라 사용자 모르게 데이터를 전송하는 코드는 grep 한 정적 분석 범위에서 발견되지 않았다.
2. **사실상의 폐쇄망 진입 장벽은 (a) HTML 리포트 자산이 GitHub Pages를 기본값으로 가리킴 (`settings.py:61`), (b) `datasets` / `transformers`가 모델·데이터셋을 HF Hub에서 끌어오는 동작에 의존하는 경로 두 가지**다. (a)는 `GUIDELLM__REPORT_GENERATION__SOURCE` 환경변수 등으로 로컬 경로로 우회 가능할 가능성이 높으나 검증 필요. (b)는 표준적인 `HF_HOME` + 사전 캐시 워밍 또는 `HF_DATASETS_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` 로 회피하는 절차가 필요.
3. **PyPI + `download.pytorch.org/whl/cpu` 두 인덱스에 의존**하므로 폐쇄망 사내 미러는 두 곳 모두 복제해야 한다. **GPU 휠을 배포하지 않을 거면 NVIDIA proprietary 13~14개 패키지를 자연스럽게 제외**할 수 있어 행정 비용을 크게 줄일 수 있다 — 자세한 결정은 `03_dependencies.md` C-6 참조. **(추가 분석 필요)**

## D-7. 다음 단계에서 동적 검증해야 할 항목

- `httpx` 호출 4곳(`backends/openai/http.py`, `extras/audio.py`, `extras/vision.py`, `utils/text.py`)의 실제 fetch 동작이 **폐쇄망에서 graceful failure인지 hard crash인지**.
- `settings.py:61`의 `source`가 환경변수 오버라이드로 **로컬 파일 경로**를 받아도 동작하는지.
- `data/builders.py`의 `push_to_hub` 경로가 **폐쇄망에서 비활성화 가능한지**(CLI 플래그/설정으로).
- `datasets.load_dataset("csv"|"json"|"parquet"|"arrow"|"webdataset", data_files=local_path, ...)` 호출이 **순수 로컬 경로만으로 완결**되는지(파일 deserializer 6경로).
