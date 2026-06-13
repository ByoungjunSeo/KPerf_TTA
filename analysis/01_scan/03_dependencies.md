# 03. 의존성 인벤토리 · 라이선스 분포

> 분석 대상: `vllm-project/guidellm` @ `fb3e862`
> 출처: `pyproject.toml`(직접 의존성 선언) + `uv.lock`(전이 락) + 로컬 환경 `dist-info/METADATA` 파싱.
> **외부 PyPI 조회는 수행하지 않음.** 락에는 있지만 로컬에 설치되어 있지 않은 패키지는 "확인 필요"로 명시.

## C-1. 의존성 선언 파일 위치

| 파일 | 역할 |
|---|---|
| `pyproject.toml` | PEP 621 메타데이터에 직접 의존성 + extras + dev 그룹 선언 |
| `setup.py` | 동적 버전 결정용 (의존성 선언 없음) |
| `MANIFEST.in` | sdist 포함 자산 — 라이선스만 명시 (의존성 선언 없음) |
| `uv.lock` | 전이 의존성까지 잠긴 락 파일 (165개 unique package) |
| `requirements*.txt` / `requirements*.in` / `Pipfile` / `Pipfile.lock` / `poetry.lock` / `environment.yml` | **모두 없음** |
| `package.json` + `package-lock.json` | 프론트엔드(`src/ui/`) 노드 의존성 — 본 문서 범위 외(별도 단계에서 분석 권장) |

추가 외부 인덱스:
- `pyproject.toml:14-21` 에 `pytorch-cpu` 인덱스(`https://download.pytorch.org/whl/cpu`)가 explicit으로 등록되어 있고, `torch`, `torchcodec`는 해당 인덱스에서 받도록 `[tool.uv.sources]`에 매핑됨. → **폐쇄망 환경에서는 PyPI 이외에 추가로 사내 미러를 통해 위 휠을 가져올 경로를 확보해야 함**.

## C-2. 직접 의존성 (`[project.dependencies]`, 24개)

| # | 패키지 | 버전 제약 | 1줄 용도 추정 (이름 기반, 코드 미열람) |
|---|---|---|---|
| 1 | `click` | `~=8.3.0` | CLI 프레임워크 |
| 2 | `culsans` | `~=0.10.0` | 동기/비동기 큐 라이브러리 |
| 3 | `datasets` | `>=4.1.0` | HuggingFace datasets 로더 |
| 4 | `eval_type_backport` | — | typing.get_type_hints 백포트 |
| 5 | `faker` | — | 합성 데이터 생성 |
| 6 | `ftfy` | `>=6.0.0` | 텍스트 인코딩 보정 |
| 7 | `httpx[http2]` | `<1.0.0` | HTTP/2 지원 비동기 HTTP 클라이언트 |
| 8 | `loguru` | — | 로깅 |
| 9 | `msgpack` | — | 바이너리 직렬화 |
| 10 | `numpy` | `>=2.0.0` | 수치 |
| 11 | `protobuf` | — | gRPC/proto 직렬화 |
| 12 | `pydantic` | `>=2.11.7` | 스키마 검증 |
| 13 | `pydantic-settings` | `>=2.0.0` | 환경변수 기반 설정 |
| 14 | `pyyaml` | `>=6.0.0` | YAML 파싱 |
| 15 | `rich` | — | 콘솔 출력 |
| 16 | `sanic` | — | 비동기 웹 프레임워크(Mock 서버 추정) |
| 17 | `tabulate` | — | 표 포매팅 |
| 18 | `transformers` | — | 토크나이저/모델 메타 |
| 19 | `uvloop` | `>=0.18` | asyncio 이벤트 루프 가속 |
| 20 | `torch` | — | 텐서 연산 (pytorch-cpu 인덱스에서 받음) |
| 21 | `more-itertools` | `>=10.8.0` | iterable 유틸 |

> 24개 선언 — 표에는 21개만 보임. 이는 `httpx[http2]`처럼 extras를 동반한 단일 선언을 1행으로 셌기 때문이며, 락 단계에서는 `h2`, `hpack`, `hyperframe`가 별도 항목으로 분리된다.

## C-3. Optional Extras (`[project.optional-dependencies]`)

| Extra | 패키지 | 비고 |
|---|---|---|
| `perf` | `orjson`, `msgpack`, `msgspec`, `uvloop` | 성능 최적화 |
| `tokenizers` | `tiktoken`, `blobfile`, `mistral-common` | 토크나이저 |
| `audio` | `datasets[audio]>=4.1.0`, `torch==2.11.*`, `torchcodec==0.13.*` | 오디오 멀티모달 |
| `vision` | `datasets[vision]`, `pillow` | 비전 멀티모달 |
| `recommended` | = `[perf, tokenizers]` | 메타 |
| `all` | = `[perf, tokenizers, audio, vision]` | 메타 |
| `dev` | `build`, `setuptools-git-versioning`, `pre-commit`, `scipy`, `sphinx`, `tox`, `lorem`, `pytest*` 7종, `respx`, `mypy`, `ruff`, `mdformat*` 4종, `pandas-stubs`, `types-*` 4종, `mkdocs-linkcheck` | 개발/테스트 도구 |

## C-4. 전이 의존성 규모 (`uv.lock`)

| 지표 | 값 |
|---|---|
| 락된 패키지 수(unique) | **165** |
| 락 포맷 버전 | `version = 1`, `revision = 3` |
| 지원 환경 마커 | python 3.10/3.11/3.12+, sys_platform `linux|darwin` 모두 |

> 운영체제별 휠을 모두 잠그고 있으므로, 폐쇄망 환경에서는 **타겟 OS·Python 버전에 맞는 휠만 사전 다운로드**해 사내 미러에 복제하는 방식으로 좁힐 수 있다.

## C-5. 라이선스 분포 (로컬 `dist-info/METADATA` 기준)

로컬 `/root/miniconda3/envs/guidellm/lib/python3.11/site-packages/` 에서 발견된 `dist-info`와 `uv.lock` 패키지명을 교집합 → **96개 패키지에 대해 라이선스를 직접 확인**. 나머지 69개는 로컬에 설치되지 않아 메타데이터 부재 ("확인 필요").

| 정규화 라이선스 | 패키지 수 |
|---|---|
| MIT (변형 포함: `MIT`, `MIT License`, `The MIT License (MIT)`, `MIT-CMU` 등) | **45** |
| BSD-3-Clause / "BSD License" / `3-Clause BSD License` 등 | **18** |
| Apache-2.0 / "Apache 2.0" / "Apache Software License" 등 | **18** |
| BSD-2-Clause | 2 (`pygments`, `wrapt`) |
| MPL-2.0 (단독 또는 듀얼) | 4 (`certifi`, `orjson`, `tqdm`(MPL+MIT), 외 1) |
| PSF-2.0 | 2 (`aiohappyeyeballs`, `typing-extensions`) |
| ISC | 3 (`aiologic`, `culsans`, `shellingham`) |
| Public Domain | 2 (`blobfile`, `tracerite`) — `pycryptodomex`는 "BSD, Public Domain" 듀얼 |
| Dual License (불명확) | 1 (`python-dateutil`) |
| 비표준 카운트(예: `Copyright (c) 2005-2025, NumPy Developers.`) | 1 (`numpy`) |
| **LGPL-2.1-only** | **1 (`pycountry`)** |

> 위 96개 외 **로컬 미설치 69개 패키지의 라이선스는 "확인 필요"**. 카피레프트/카피레프트-인접 라이선스가 추가로 섞여 있을 가능성은 낮지만, K-Perf 배포 직전에 한 번 더 PyPI 메타데이터 기반 자동 스캔(`pip-licenses --format=markdown`)을 수행해야 한다.

## C-6. 위험 의존성 (별도 검토 권장)

| 패키지 | 라이선스 | 위치 | K-Perf 관점 위험도 / 메모 |
|---|---|---|---|
| `pycountry` | **LGPL-2.1-only** | 전이 의존성(직접 deps 미포함). 추정 경로: `faker` → `pycountry` 또는 `mistral-common` → `pycountry`. **추가 확인 필요** | 🟡 **검토 권장**. 동적 임포트로 사용한다면 LGPL은 일반적으로 허용되나, K-Perf를 정적 링크/번들로 배포하거나 SBOM·라이선스 표기 의무가 엄격한 공공조달 컨텍스트라면 별도 attribution 필요 |
| `numpy` | 메타데이터에 라이선스명 대신 저작권 문구만 출력됨 | direct dep | 🟢 실제로는 BSD-3-Clause. METADATA 파싱 한계일 뿐. 표기 안내 시 BSD-3로 명기 |
| `python-dateutil` | "Dual License" | 전이 | 🟢 실제로는 Apache-2.0 + BSD-3-Clause 듀얼. 안전 |
| `orjson` | `MPL-2.0 AND (Apache-2.0 OR MIT)` | optional `perf` extra | 🟡 MPL-2.0은 **약한 카피레프트(파일 단위)**. orjson 자체를 수정해 재배포할 경우 그 파일만 MPL을 유지해 공개해야 함. 단순 사용(import)은 의무 발생 안 함 |
| `tqdm` | `MPL-2.0 AND MIT` | 전이(transformers 등 경유) | 🟡 위와 동일 |
| `certifi` | MPL-2.0 | 전이 (httpx/requests 경유) | 🟡 위와 동일 |
| `tracerite` | "Public Domain" | 전이 (sanic 경유) | 🟢 표기상 모호하나 일반적으로 제약 없음 |
| `blobfile` | "Public Domain" | optional `tokenizers` extra | 🟢 동일 |
| `cuda-toolkit` (로컬 conda 설치 패키지) | **UNKNOWN** + `nvidia-*` 13종 = `LicenseRef-NVIDIA-Proprietary` | 전이 (torch GPU 빌드 동반) | 🔴 **K-Perf가 GPU 휠을 함께 배포하면 NVIDIA SDK EULA 동의·표기가 별도로 필요**. CPU 전용 빌드(`pytorch-cpu` 인덱스)만 쓰면 회피 가능 |
| `regex` | `Apache-2.0 AND CNRI-Python` | 전이 | 🟢 CNRI-Python은 OSI 승인, Apache와 양립 |
| `ujson` | `BSD-3-Clause AND TCL` | 전이 | 🟢 TCL 라이선스는 BSD 계열, 양립 |

### C-6-1. "확인 필요" 패키지 (로컬 METADATA 없음, 69개)

알파벳 순:

`alabaster, async-timeout, babel, backports-asyncio-runner, build, cachetools, cfgv, chardet, colorama, coverage, distlib, docutils, exceptiongroup, identify, imagesize, importlib-metadata, iniconfig, lorem, mdformat, mdformat-footnote, mdformat-frontmatter, mdformat-gfm, mdit-py-plugins, mkdocs-linkcheck, more-itertools, mypy, mypy-extensions, nodeenv, pandas-stubs, platformdirs, pluggy, pre-commit, pyproject-api, pyproject-hooks, pytest, pytest-asyncio, pytest-cov, pytest-httpx, pytest-mock, pytest-rerunfailures, pytest-timeout, pytz, respx, ruamel-yaml, ruamel-yaml-clib, ruff, scipy, setuptools-git-versioning, sniffio, snowballstemmer, sphinx, sphinxcontrib-applehelp, sphinxcontrib-devhelp, sphinxcontrib-htmlhelp, sphinxcontrib-jsmath, sphinxcontrib-qthelp, sphinxcontrib-serializinghtml, tabulate, tomli, torchcodec, tox, types-pytz, types-pyyaml, types-requests, types-toml, tzdata, virtualenv, win32-setctime, zipp`

> 위 목록은 대부분 dev/도구·테스트 의존성이거나 sphinx 계열·Windows 전용(`win32-setctime`)이라 런타임 영향이 작다. **PyPI 일반 패턴상 모두 MIT/BSD/Apache로 추정되나, K-Perf 정식 배포 직전 라이선스 자동 스캔이 필수**.

## C-7. 액션 아이템(의존성 관점)

1. K-Perf 빌드 전: 폐쇄망 미러에 **PyPI + `download.pytorch.org/whl/cpu`** 두 인덱스를 모두 복제할 것 (`pyproject.toml`의 explicit index 매핑 때문).
2. CI에 `pip-licenses` 또는 `cyclonedx-bom` 추가 → 라이선스 SBOM 자동 생성. `pycountry`(LGPL)와 MPL-2.0 항목은 NOTICE에 attribution 강제.
3. **GPU 휠 배포 여부 결정 필요**. CPU-only로 한정하면 NVIDIA proprietary 14종을 모두 회피할 수 있고, 폐쇄망/공공조달 검토 시 행정 비용이 크게 줄어든다.
4. `dev` extras(특히 `sphinx`, `mkdocs-linkcheck`, `mdformat-*` 등)는 **K-Perf 최소 런타임 배포에서 제외**해 SBOM 면적을 줄일 것.
