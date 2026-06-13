# 01. GuideLLM 저장소 거시 구조 · 진입점 · 빌드/테스트/CI

> 분석 대상: `vllm-project/guidellm` (로컬 clone 경로 `/root/guidellm_kperf_analysis/guidellm`)
> 분석 모드: 정적(static) read-only. 코드 로직은 의도적으로 보지 않음.

## A. 저장소 거시 구조

### A-1. 체크아웃 메타데이터

| 항목 | 값 |
|---|---|
| 원격 URL | `https://github.com/vllm-project/guidellm.git` |
| 브랜치 | `main` |
| HEAD 커밋 | `fb3e862aed4cae34beb981aaac2ece99597eb386` |
| Clone 깊이 | `--depth 50` (얕은 clone) |

최근 3개 커밋:

| Hash | Date | Subject |
|---|---|---|
| `fb3e862` | 2026-05-28 | Run a useful set of tox environments by default (#748) |
| `3b3f93f` | 2026-05-28 | Run a useful set of tox environments by default |
| `577f925` | 2026-05-27 | Fix blank HTML report by serving UI assets from GitHub Pages (#744) |

### A-2. 규모 지표

| 항목 | 값 | 비고 |
|---|---|---|
| 전체 파일 수 (`.git` 제외) | **545** | `find` 기준 |
| Python 파일 수 | **226** | |
| Python LoC (대략) | **65,736** | `wc -l` 합산 |
| TypeScript/TSX 파일 수 | **186** | `src/ui/` 프론트엔드 포함 |
| TS/TSX LoC (대략) | **8,522** | |
| 테스트 파일 (`tests/*.py`) | **94** | `unit 80 / integration 7 / e2e 6 / ui 0(JS측)` |
| Python 지원 버전 | `>=3.10.0,<4.0` | pyproject.toml |

> 본 저장소는 **Python 백엔드 + Next.js 기반 HTML 리포트 UI** 하이브리드 구조다. 분석 1단계의 초점은 Python 측이지만, UI 자산이 외부 GitHub Pages를 가리키는 점은 별도 문서(`04_network_and_airgap.md`)에서 다룬다.

### A-3. 최상위 디렉터리 역할 (디렉터리명·README·docstring 1줄 기반, 코드 내부 미열람)

| 경로 | 추정 역할 (확정 아님) |
|---|---|
| `src/guidellm/` | GuideLLM Python 패키지 본체 |
| `src/ui/` | Next.js(React/TypeScript) 기반 HTML 리포트 UI |
| `tests/` | pytest(Python) + jest/cypress(UI) 테스트 |
| `docs/` | mkdocs 문서. `getting-started/`, `guides/`, `examples/`, `developer/`, `stylesheets/`, `scripts/`, `assets/` 서브 디렉터리 |
| `scripts/` | 단일 파일 `lock.sh` (uv lock 생성용 셸 스크립트) |
| `.github/` | `actions/` (재사용 액션 2종) + `workflows/` (8종) + `ISSUE_TEMPLATE/` |
| `.husky/` | git pre-commit 훅 (frontend lint 연결로 추정) |

### A-4. `src/guidellm/` 1단계 서브패키지

| 모듈 | `__init__.py` docstring 첫 줄 |
|---|---|
| `backends/` | Backend infrastructure for GuideLLM language model interactions. |
| `benchmark/` | Benchmark execution and performance analysis framework. |
| `cli/` | GuideLLM command-line interface entry point. |
| `data/` | (docstring 없음 — `from .builders import ShortPromptStrategy` 등 re-export) |
| `extras/` | Code that depends on optional dependencies. Each submodule should be deferred imported. |
| `mock_server/` | GuideLLM Mock Server for OpenAI and vLLM API compatibility. |
| `scheduler/` | Scheduler subsystem for orchestrating benchmark workloads and managing worker processes. |
| `schemas/` | Pydantic schema models for GuideLLM operations. |
| `utils/` | Utils should be imported from their respective sub-submodules. |

기타 모듈 파일:
- `__init__.py`, `__main__.py`, `logger.py`, `settings.py`, `version.py`

### A-5. 깊이-3 디렉터리 트리 (노이즈 제외)

```
.
├── docs
│   ├── assets
│   ├── developer
│   ├── examples
│   ├── getting-started
│   ├── guides
│   │   └── multimodal
│   ├── scripts
│   └── stylesheets
├── .github
│   ├── actions
│   │   ├── python-uv
│   │   └── run-tox
│   ├── ISSUE_TEMPLATE
│   └── workflows
├── .husky
├── scripts
├── src
│   ├── guidellm
│   │   ├── backends
│   │   ├── benchmark
│   │   ├── cli
│   │   ├── data
│   │   ├── extras
│   │   ├── mock_server
│   │   ├── scheduler
│   │   ├── schemas
│   │   └── utils
│   └── ui
│       ├── app
│       ├── lib
│       ├── public
│       └── types
└── tests
    ├── e2e
    ├── integration
    │   └── scheduler
    ├── ui
    │   ├── cypress
    │   ├── integration
    │   ├── __mocks__
    │   └── unit
    └── unit
        ├── backends
        ├── benchmark
        ├── data
        ├── entrypoints
        ├── extras
        ├── mock_server
        ├── scheduler
        ├── schemas
        └── utils
```

---

## E. 진입점(Entry Point)

`pyproject.toml`에 단일 콘솔 스크립트 진입점이 선언되어 있다:

```toml
[project.entry-points.console_scripts]
guidellm = "guidellm.__main__:cli"
```

- **CLI 이름**: `guidellm`
- **매핑 모듈**: `guidellm.__main__:cli`
- `setup.py`는 setuptools-git-versioning을 통한 동적 버전 산출 용도이며 별도의 entry_points 선언은 두지 않음.
- 패키지 임포트 시 `src/guidellm/__init__.py`에서 `uvloop` 적용을 시도하나 모듈 내부 로직 분석은 다음 단계로 미룸.

---

## F. 빌드 · 테스트 · CI

### F-1. 최상위 메타파일

| 파일 | 존재 | 1줄 요약 |
|---|---|---|
| `pyproject.toml` | ✓ | PEP 621 메타데이터, setuptools 빌드 백엔드, ruff/mypy/pytest 설정 포함 |
| `setup.py` | ✓ | `setuptools-git-versioning` 기반 동적 버전 결정용 보조 스크립트 |
| `MANIFEST.in` | ✓ | `include LICENSE` 한 줄 |
| `tox.ini` | ✓ | `test-unit/test-integration/test-e2e/lint-check/lint-fix/type-check/link-check` 환경 정의 |
| `Makefile` | **✗** | 없음 |
| `Containerfile` | ✓ | Fedora python-313-minimal 기반 멀티스테이지 빌드 이미지 |
| `Containerfile.vllm` | ✓ | `vllm/vllm-openai` 이미지를 베이스로 GuideLLM을 추가 설치 |
| `package.json` + `package-lock.json` | ✓ | UI(Next.js) 빌드/테스트 의존성 (직접 17 + 개발 38) |
| `cypress.config.ts`, `jest.config.cjs`, `jest.setup.ts` | ✓ | UI 테스트 설정 |
| `eslint.config.js`, `.prettierrc`, `.prettierignore` | ✓ | UI 린터/포매터 |
| `mkdocs.yml`, `.mdformat.toml`, `.markdownlint.yaml`(없음) | mkdocs.yml만 ✓ | 문서 빌드(mkdocs) |
| `.pre-commit-config.yaml` | ✓ | pre-commit 훅 정의 (구체 훅 목록은 본 단계 범위 외) |
| `.gitignore` (+ `.containerignore` 심볼릭 링크) | ✓ | |
| `uv.lock` | ✓ | 약 **165개 패키지** 락 (uv 기반 재현 가능 환경) |

### F-2. CI 워크플로 (`.github/workflows/`)

| 파일 | 워크플로명(헤더) | 1줄 추정(이름 기반) |
|---|---|---|
| `build-multiarch-container.yml` | Build Multi-Arch Container | 멀티 아키텍처 컨테이너 이미지 빌드 |
| `container-maintenance.yml` | Container Image Maintenance | 컨테이너 이미지 유지보수(태그/정리 등 추정) |
| `development.yml` | Development | 개발 브랜치 push/PR 트리거 검증 |
| `main.yml` | Main | main 브랜치 진입 시 통합 검증 |
| `nightly.yml` | Nightly | 야간 정기 빌드/테스트 |
| `quality.yml` | Quality Checks | ruff/mypy/mdformat 등 품질 검사 |
| `release.yml` | Release | 릴리스 자동화 |
| `testing.yml` | Tests | pytest 등 테스트 매트릭스 |

> 워크플로 내부 step 분석은 본 단계 범위 외 — 다음 단계에서 폐쇄망 적용 시 영향이 큰 워크플로(특히 release/nightly)를 별도로 분석한다.

### F-3. 테스트

- 프레임워크: **pytest** (`[tool.pytest.ini_options]`에서 `addopts='-s -vvv --cache-clear'`, 마커 `smoke/sanity/regression` 정의)
- 테스트 디렉터리: `tests/unit`(80), `tests/integration`(7), `tests/e2e`(6), `tests/ui`(JS 측 — Python 파일 0개, jest/cypress)
- 보조: `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `pytest-rerunfailures`, `pytest-timeout`, `pytest-httpx`, `respx` (dev extras)
- UI 측: `jest`, `cypress` (package.json scripts)

### F-4. tox 환경 (요약)

| 환경 | 명령(요약) |
|---|---|
| `tests` | `pytest tests/` |
| `test-unit` / `test-integration` / `test-e2e` | 각 디렉터리에 한정한 pytest |
| `lint-check` | `ruff format --check --diff`, `ruff check`, `mdformat --check` |
| `lint-fix` | `ruff format`, `ruff check --fix`, `mdformat` |
| `type-check` | `mypy --check-untyped-defs` |
| `link-check` | (정의 존재, 본 단계에서는 미상세) |
