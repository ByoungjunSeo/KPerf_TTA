# 00. K-Perf 1단계 분석 요약 (GuideLLM 저장소 구조 · 라이선스 · 의존성 · 외부 통신)

> Scope: `vllm-project/guidellm` @ `fb3e862` (main, 2026-05-28 시점) — 코드 로직은 아직 보지 않음.

## 5줄 요약 × 4

### 01. 저장소 구조 (`01_repo_overview.md`)
- Python 백엔드(`src/guidellm/`, 226 파일 ~65.7k LoC) + Next.js UI(`src/ui/`, 186 TS/TSX ~8.5k LoC) 하이브리드. Python 백엔드는 `backends/benchmark/cli/data/extras/mock_server/scheduler/schemas/utils` 9개 서브패키지로 잘 분리되어 있음.
- 단일 CLI 진입점 `guidellm = guidellm.__main__:cli` (콘솔 스크립트 1개).
- 빌드: setuptools + `setuptools-git-versioning`(동적 버전), `tox` 환경 8종(테스트 3 / 린트 2 / 타입 1 / 링크 1 / 통합 1), pre-commit, mkdocs.
- 컨테이너: `Containerfile` (Fedora python-313-minimal 기반) + `Containerfile.vllm` (vLLM OpenAI 이미지 기반).
- CI: GitHub Actions 8종 (`build-multiarch-container, container-maintenance, development, main, nightly, quality, release, testing`).

### 02. 라이선스 감사 (`02_license_audit.md`)
- 상류 자체는 **Apache-2.0 단일** 라이선스 (`LICENSE`만 존재, `NOTICE`/`COPYING` 없음).
- **소스 헤더의 `SPDX-License-Identifier:` 사용은 0건** — 파일 단위 라이선스 추적이 약함.
- 임베드된 외부 라이선스 코드 1건: `src/guidellm/utils/default_group.py` (BSD-3-Clause, "click-default-group" Heungsub Lee). Apache와 양립 가능.
- 카피레프트(GPL/AGPL/LGPL)가 **소스 트리 안에** 임베드된 흔적 0건. 다만 의존성 측에는 LGPL 1건 존재(아래 참조).
- Apache-2.0 4대 의무는 K-Perf fork 시점에 "사본 동봉/변경 표시/notice 보존/NOTICE 동봉" 4개 모두 우리 측에서 절차 정립이 필요(현재 모두 미정).

### 03. 의존성 (`03_dependencies.md`)
- 직접 의존성 24개(런타임), extras `perf/tokenizers/audio/vision/recommended/all` + `dev` 그룹. 락 파일 `uv.lock` 기준 전이 의존성 포함 **165개**.
- `pyproject.toml`이 PyPI 외 **explicit index `download.pytorch.org/whl/cpu`** 를 등록 → 폐쇄망 미러 작업 시 두 인덱스 모두 복제 필요.
- 로컬 `dist-info/METADATA` 기준 96개 라이선스 분포: MIT 45 / BSD 18 / Apache 18 / MPL-2.0 4 / PSF/ISC/2-clause/Public Domain 다수. 나머지 69개는 로컬 미설치라 "확인 필요".
- **요주의 패키지**: `pycountry` = **LGPL-2.1-only** (전이, 정확한 의존 경로 확인 필요), `orjson`/`tqdm`/`certifi` = MPL-2.0(약한 카피레프트), `nvidia-*` 14종 = NVIDIA proprietary(GPU 휠 배포 시에만 등장).
- CPU-only 빌드로 한정하면 NVIDIA proprietary 패키지를 자연스럽게 제외할 수 있어 행정 비용이 크게 감소.

### 04. 외부 통신 · 폐쇄망 (`04_network_and_airgap.md`)
- HTTP 라이브러리는 `httpx`만 직접 import (4개 파일). `requests`/`urllib`/`aiohttp` 직접 import는 0건.
- 코드 내 외부 호스트 URL은 사실상 1개: `https://vllm-project.github.io/guidellm/ui/v0.5.4/index.html` (`settings.py:61`, 리포트 HTML 자산). UI .env도 같은 호스트.
- **텔레메트리/애널리틱스 SDK(`sentry/posthog/wandb/mlflow/datadog/opentelemetry/...`) 매치 0건** — 사용자 모르게 데이터를 전송하는 코드 흔적 없음.
- HF Hub 의존: `datasets.load_dataset` 6경로, `AutoTokenizer.from_pretrained` 5경로, `push_to_hub` 1경로(`HF_TOKEN` 필요). 폐쇄망에서는 사전 캐시 워밍 + `HF_*_OFFLINE` 환경변수 전략 필요.
- 폐쇄망 적합성: 코드 외부 호출 면적 자체는 작으나, **(1) HTML 리포트 자산 GitHub Pages 의존, (2) HF Hub 동적 fetch** 두 경로는 실제 동적 검증으로 회피 가능성을 확인해야 함.

---

## 즉시 의사결정 이슈 (Top 5)

1. **HTML 리포트 자산 호스팅 정책**: `settings.py:61`의 기본값이 `vllm-project.github.io`. 폐쇄망 K-Perf에서 (a) 사내 정적 호스트로 복제, (b) `GUIDELLM__REPORT_GENERATION__SOURCE` 환경변수 오버라이드로 로컬 파일 경로 사용, 중 어떤 방식을 표준으로 둘지 결정 필요. (오버라이드 가능 여부 자체는 다음 단계에서 동적 검증)
2. **GPU 휠 배포 여부**: CPU-only로 한정하면 NVIDIA proprietary 14종(EULA 동의 필요)을 모두 우회 가능. K-Perf 검증 대상 하드웨어가 NPU/GPU 어느 쪽이냐에 따라 폐쇄망 미러 복제 비용·라이선스 행정 비용이 크게 갈리므로 **빨리 확정**해야 함.
3. **`pycountry` LGPL-2.1-only 처리**: 직접 deps가 아닌 전이 의존성. (a) 정확한 의존 경로 추적(`faker`/`mistral-common`/기타?), (b) 동적 임포트만으로 충분한지 확인, (c) K-Perf NOTICE에 LGPL attribution 명시 — 3가지 후속 작업 필요. 공공조달/SBOM 검증이 엄격하면 priority 상승.
4. **NOTICE 신설 + SPDX 헤더 정책**: 상류는 NOTICE도, SPDX 헤더도 없는 상태. K-Perf 측은 (a) `NOTICE` 신설로 3rd-party attribution 모음, (b) 신규/수정 파일에 `# SPDX-License-Identifier: Apache-2.0` 의무화로 추적성 확보 — 둘 다 1단계에서 정책 결정만 해두면 이후 자동화 가능.
5. **HF Hub 의존 경로의 폐쇄망 차단/우회 전략**: `load_dataset`/`from_pretrained`/`push_to_hub` 총 12+ 호출 지점. 표준 절차로 (a) 사용 모델·데이터셋 화이트리스트 작성, (b) 사전 캐시 워밍 스크립트, (c) `HF_DATASETS_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` 강제 — 3종 세트를 K-Perf 운영 가이드에 명기 필요. (실제 비활성화 가능성은 다음 단계 동적 검증으로 확정)
