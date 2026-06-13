# 02. 라이선스 감사 (Apache-2.0 준수 점검)

> 분석 대상: `vllm-project/guidellm` @ `fb3e862`
> 본 문서의 결론은 변호사 검토를 대체하지 않는다. K-Perf 도구가 GuideLLM을 fork/재배포할 때의 행정·표기 의무를 사전 점검하는 자료다.

## B-1. 저장소 내 라이선스 파일

| 파일 | 위치 | 비고 |
|---|---|---|
| `LICENSE` | `/LICENSE` | 표준 **Apache License 2.0** 전문 (11,357 byte). 첫 줄 `Apache License Version 2.0, January 2004`, 말미에 boilerplate 적용 안내 포함 |
| `NOTICE` | **없음** | 검색 결과 0건 |
| `COPYING*` | **없음** | 검색 결과 0건 |

`find . -type f \( -iname "LICENSE*" -o -iname "NOTICE*" -o -iname "COPYING*" \)` 결과 — **단 1건**: `./LICENSE`.

- `pyproject.toml` 선언: `license = { text = "Apache-2.0" }` (저장소 자체 라이선스와 일치)
- `MANIFEST.in`: `include LICENSE` (sdist에 LICENSE 동봉됨)
- `README.md` 말미: "GuideLLM is licensed under the [Apache License 2.0](.../LICENSE)" 명시 (`README.md:270`)

## B-2. SPDX 식별자 분포

소스 헤더의 `SPDX-License-Identifier:` 토큰을 `.py/.ts/.tsx/.js/.cjs/.yaml/.yml/.toml/.json/.md/.sh` 전 범위로 검색:

| 결과 | 건수 |
|---|---|
| 매치 파일 | **0** |
| 매치 라인 | **0** |

> **소스 헤더에 SPDX 라이선스 식별자가 전혀 박혀 있지 않다.** 저장소 전체 라이선스는 루트 `LICENSE`에 의존한다. K-Perf 도구가 부분 발췌·재배포하면 파일 단위 라이선스 추적이 어려워질 수 있으므로 우리 산출물 측에서는 SPDX 헤더 정책을 명문화하는 편이 안전하다.

## B-3. 혼재 라이선스 점검

### B-3-1. 외부 라이선스가 명시된 파일

| 파일 | 라이선스 | 출처 표기 | 비고 |
|---|---|---|---|
| `src/guidellm/utils/default_group.py` (앞 1~31줄) | **BSD-3-Clause** 변종 (BSD 3-clause "New BSD License" 본문 인용) | `Copyright (c) 2015-2023, Heungsub Lee` | `click-default-group` 라이브러리의 코드를 채택(adapted)했음을 헤더 docstring에 명시 |

> `grep -rIn 'Copyright' src/guidellm/` 결과로 발견된 유일한 외부 저작권 표기. **GPL/AGPL/LGPL 등 카피레프트 라이선스가 소스 트리에 임베드된 흔적은 0건**.

### B-3-2. 외부 자산 의존 (저장소 안에는 없지만 런타임에 끌어오는 자산)

- `src/guidellm/settings.py:61` — 기본 리포트 HTML 소스 `https://vllm-project.github.io/guidellm/ui/v0.5.4/index.html` (해당 자산도 동일 저장소의 산출물로 추정되나 GitHub Pages 정적 호스팅).
- `src/ui/.env.production` / `.env.staging` / `.env.development` — `ASSET_PREFIX=https://vllm-project.github.io/guidellm/ui/...` (UI 빌드 시 외부 호스팅 자산 사용)
- 위 두 항목은 라이선스 자체보다는 "폐쇄망 적합성" 관점에서 중요. 자세한 내용은 `04_network_and_airgap.md` 참조.

## B-4. Apache-2.0 4대 의무 체크리스트 (재배포 시점 기준)

Apache License 2.0의 재배포 의무는 Section 4(`Redistribution`) 4개 항으로 정리된다. K-Perf 도구가 GuideLLM의 소스 또는 일부를 가져와 재배포한다고 가정했을 때의 체크리스트:

| # | 의무(요약) | GuideLLM 상류(upstream) 현재 상태 | K-Perf 측 대응 상태 |
|---|---|---|---|
| 1 | **LICENSE 사본 동봉**: 수령자에게 Apache-2.0 사본을 제공 | ✅ 루트 `LICENSE` 존재 + `MANIFEST.in`에 `include LICENSE` | ⏳ 미정 — K-Perf 배포물에 동봉 필요 |
| 2 | **변경 사항 표시**(prominent notices of modifications): 수정한 파일에 변경 사실 명시 | ➖ 해당 없음(상류는 자체 코드) | ⏳ 미정 — fork 시 수정 파일 헤더/CHANGELOG로 표시 필요 |
| 3 | **모든 저작권/특허/상표/귀속(attribution) notice 보존**: 원본 소스에 있는 표기 유지 | ⚠️ `NOTICE` 파일은 없음. `default_group.py` 내부 BSD 헤더는 보존되어 있음 | ⏳ 미정 — fork 시 기존 docstring/copyright 표기는 그대로 유지해야 함 |
| 4 | **NOTICE 파일 있으면 재배포물에 동봉**: 수령자에게 NOTICE 전달 | ✅ 의무 없음 (NOTICE 자체가 없으므로 동봉 대상도 없음) | ⏳ 우리 측에서 NOTICE를 신설할지 결정 필요 (3rd-party 의존성 attribution 모음 용도) |

> 의무 #4 보충 — Apache-2.0은 "If the Work itself contains a NOTICE file…"이라는 **조건문** 의무이므로, GuideLLM 상류가 NOTICE를 만들지 않은 점은 라이선스 위반이 아니다. 다만 K-Perf 산출물은 다수 3rd-party 라이브러리의 attribution을 한곳에 모으는 NOTICE 파일을 신설하는 것이 관행적으로 권장된다.

## B-5. 경고 섹션 — 카피레프트/비표준 라이선스

저장소 트리(상류 소스)에서는 **카피레프트(GPL/AGPL/LGPL) 또는 비표준 라이선스가 임베드된 흔적이 발견되지 않았다.** 단,

- **의존성 측면(설치 시점에 끌어오는 패키지)** 에서는 LGPL-2.1-only(`pycountry`) 1건, NVIDIA proprietary CUDA 패키지 다수 등이 관찰됨. → `03_dependencies.md` 참조.
- `default_group.py`의 BSD-3-Clause는 Apache-2.0과 양립 가능(소스 헤더 보존만 하면 됨). 다만 K-Perf가 이 파일을 직접 수정/배포할 때는 해당 BSD 헤더의 3개 조건(저작권 표기 보존·바이너리 배포 시 문서에 동일 표기·기여자 이름 무단 사용 금지)도 함께 만족시켜야 한다.

## B-6. 액션 아이템(라이선스 관점)

1. K-Perf 저장소 fork 시 `LICENSE`를 그대로 보존하고, **수정한 파일 헤더에 "Modified by K-Perf, YYYY-MM-DD" 표기 정책**을 도입할 것.
2. 우리 측 `NOTICE` 파일을 신설해 (a) "Based on vllm-project/guidellm (Apache-2.0)" 한 줄, (b) `default_group.py`의 BSD-3-Clause attribution, (c) 추후 추가 3rd-party 라이선스 항목을 모으는 정책을 채택할 것.
3. 우리 측 신규 코드에는 **SPDX 헤더(`# SPDX-License-Identifier: Apache-2.0`)를 의무화**하여 상류와 구분되게 표기한다(상류가 SPDX를 안 쓰는 점이 명확하므로 혼동을 줄임).
