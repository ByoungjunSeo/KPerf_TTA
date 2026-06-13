# 04. `OpenAIHTTPBackend.resolve` 경로 줄 단위 해부

> 분석 대상: `vllm-project/guidellm` @ `fb3e862`
> 분석 모드: **본문 줄 단위 열람**. 모든 주장은 `파일:라인` 근거 동반. 추측이 들어간 곳은 "확인 필요"로 표기.
> 이 문서는 K-Perf TTFT / TPOT / E2EL 산출의 코드 근거다.

## 1. httpx.AsyncClient 생성 설정 (http.py:223-248)

```python
async def process_startup(self):                                # http.py:223
    if self._in_process:
        raise RuntimeError("Backend already started up for process.")
    self._async_client = httpx.AsyncClient(                     # http.py:233
        http2=self._args.http2,                                 #   234
        timeout=httpx.Timeout(                                  #   235
            FALLBACK_TIMEOUT,                                   #   236 (= 5.0; http.py:40)
            read=self._args.timeout,                            #   237
            connect=self._args.timeout_connect,                 #   238
        ),
        follow_redirects=self._args.follow_redirects,           #   240
        verify=self._args.verify,                               #   241
        limits=httpx.Limits(                                    #   243
            max_connections=None,                               #   244
            max_keepalive_connections=None,                     #   245
            keepalive_expiry=5.0,                               #   246
        ),
    )
    self._in_process = True                                     # http.py:249
```

| 설정 | 값 | 기본값(코드) | 정의 위치 |
|---|---|---|---|
| `http2` | `_args.http2` | **`True`** | http.py:103-106 |
| `timeout` (default = write/pool) | `FALLBACK_TIMEOUT` (5.0초) | — | http.py:40 |
| `timeout.read` | `_args.timeout` | **`None`** | http.py:95-98 |
| `timeout.connect` | `_args.timeout_connect` | **`FALLBACK_TIMEOUT` = 5.0초** | http.py:99-102 |
| `follow_redirects` | `_args.follow_redirects` | **`True`** | http.py:107-110 |
| `verify` (TLS) | `_args.verify` | **`False`** ⚠️ | http.py:111-114 |
| `limits.max_connections` | `None` (무제한) | (하드코딩) | http.py:244 |
| `limits.max_keepalive_connections` | `None` (무제한) | (하드코딩) | http.py:245 |
| `limits.keepalive_expiry` | `5.0초` | (하드코딩) | http.py:246 |

### 1-1. timeout 의미 (확인 필요 → 추가 검증 가치 있음)

- `httpx.Timeout(default, read=None)`에서 **`None`은 "무제한"을 의미**(httpx 문서 기준). 따라서 `--backend-kwargs '{"timeout": 60}'`처럼 명시하지 않으면 **추론 서버 응답 대기는 사실상 무한대**.
- K-Perf 운영 권장: 폐쇄망에서는 read timeout을 항상 명시할 것. (TTFT/E2EL 측정의 anomaly 시 클라이언트가 영원히 hang하는 것을 막기 위함)

### 1-2. base headers / auth 주입 경로

**AsyncClient 생성 시점에는 base headers를 박지 않는다.** 매 요청마다 `_build_headers`가 동적으로 만든다.

```python
def _build_headers(self, existing_headers=None):                # http.py:536
    headers = {}
    if self._args.api_key:                                      # http.py:551
        token = self._args.api_key.get_secret_value()           #   552
        headers["Authorization"] = f"Bearer {token}"            #   553
    if existing_headers:                                        #   556
        headers = {**headers, **existing_headers}               #   557 ★ 사용자 헤더가 우선
    return headers or None
```

- `api_key`는 `OpenAIHTTPBackendArgs.api_key: SecretStr | None` (http.py:82-85)
- `_prepare_resolve_request` 가 매 요청마다 `self._build_headers(arguments.headers)` 호출 (http.py:421)
- `arguments.headers`는 핸들러의 `format()`이 채울 수 있으나, 본 단계에서 본 4개 핸들러(Text/Chat/Audio/Responses)는 `arguments.headers`를 직접 세팅하지 않는다. `extras` 키워드로 사용자 정의 헤더가 들어올 수는 있음(`arguments.model_combine(kwargs["extras"])`, `format` 내부).

> **결론**: 인증은 단일 `Authorization: Bearer <api_key>` 헤더만 자동 주입된다. 그 외 인증(예: HMAC 서명, mTLS 인증서)은 코드 본문에 없음 → 사내 추론 게이트웨이가 그 외 인증을 요구하면 K-Perf 측 백엔드 fork 필요.

## 2. 엔드포인트 분기

분기 결정 필드는 **`OpenAIHTTPBackendArgs.request_format`** (http.py:70-81). 허용 값:

| `request_format` | 매핑되는 path (`api_routes`) | 핸들러 클래스 | 등록 위치 |
|---|---|---|---|
| `/v1/completions` | `v1/completions` | `TextCompletionsRequestHandler` | request_handlers.py:140-141 |
| **`/v1/chat/completions` (기본값)** | `v1/chat/completions` | `ChatCompletionsRequestHandler` | request_handlers.py:401-402 |
| `/v1/responses` | `v1/responses` | `ResponsesRequestHandler` | request_handlers.py:929-930 |
| `/v1/embeddings` | `v1/embeddings` | `EmbeddingsRequestHandler` | request_handlers.py:1282-1283 |
| `/v1/audio/transcriptions`, `/v1/audio/translations` | (각각) | `AudioRequestHandler` (ChatCompletions 상속) | request_handlers.py:805-808 |
| `/pooling` | `pooling` | `PoolingRequestHandler` (ChatCompletions 상속) | request_handlers.py:1224-1225 |

분기 코드 (`_prepare_resolve_request`, http.py:383-403):

```python
if (request_path := self._args.api_routes.get(self._args.request_format)) is None:  # http.py:383-385
    raise ValueError(f"Unsupported request format '{self._args.request_format}'")
request_handler = OpenAIRequestHandlerFactory.create(self._args.request_format)     # http.py:390-392
arguments: GenerationRequestArguments = request_handler.format(                      # http.py:393
    data=request, history=history, model=(await self.default_model()),
    stream=self._args.stream, extras=self._args.extras,
    max_tokens=self._args.max_tokens, server_history=self._args.server_history,
)
request_url = f"{self._args.target}/{request_path}"                                  # http.py:403
```

- `api_routes`는 `DEFAULT_API_PATHS`(http.py:42-52)와 사용자 오버라이드의 병합 (http.py:157-161의 `merge_api_routes` validator). 따라서 path 자체는 운영자가 재정의 가능.
- `request_format`이 `/v1/responses`이고 `server_history=True`인 경우만 멀티턴 서버측 history 사용 가능(http.py:163-172의 `validate_server_history`). 그 외 경로는 history를 클라이언트가 매 요청에 다시 보낸다.

> 즉 **"무엇을 언제 쓰나"**: 운영자가 CLI 옵션 `--request-format /v1/completions | /v1/chat/completions | /v1/responses | ...` 또는 `--backend-kwargs '{"request_format": "..."}'` 로 명시. 명시 없으면 **`/v1/chat/completions`**.

## 3. 요청 JSON 페이로드 — 필드 출처 표

### 3-1. 모든 핸들러에 공통(httpx에 들어가는 4-인자 + body 골격)

`_prepare_resolve_request` 반환 (http.py:417-425):

```python
request_kwargs = {
    "url": request_url,                                          # http.py:418  ← f"{target}/{request_path}"
    "method": arguments.method or "POST",                        # http.py:419
    "params": arguments.params,                                  # http.py:420
    "headers": self._build_headers(arguments.headers),           # http.py:421
    "json": request_json,                                        # http.py:422  ← arguments.body (파일 없을 때)
    "data": request_data,                                        # http.py:423  ← arguments.body (파일 있을 때)
    "files": request_files,                                      # http.py:424
}
```

- `arguments.body` 안에 `None` 값은 사전에 `deep_filter(... lambda _,v: v is not None)`로 제거 (http.py:413). 즉 최종 JSON에는 `None` 키가 나타나지 않음.

### 3-2. `/v1/chat/completions` (기본값) — 실제 body 예시

코드가 합성하는 body의 **재구성 예시**. 입력 가정:
- `data.columns = {"prefix_column": ["You are a benchmark client."], "text_column": ["Tell me a joke."]}`
- `data.output_metrics.text_tokens = 128`
- `_args.model = "Qwen/Qwen2-7B-Instruct"`, `_args.stream = True`, `_args.max_tokens = None`, `_args.extras = None`
- `data.expects_tool_call = False`, `tools_column` 없음

```jsonc
{
  "model": "Qwen/Qwen2-7B-Instruct",
  "stream": true,
  "stream_options": {
    "include_usage": true,
    "continuous_usage_stats": true
  },
  "max_completion_tokens": 128,
  "stop": null,                                  // deep_filter로 제거됨 (None 키 모두 제거)
  "ignore_eos": true,
  "messages": [
    {"role": "system", "content": "You are a benchmark client."},
    {"role": "user",   "content": [{"type": "text", "text": "Tell me a joke."}]}
  ]
}
```

> **주의**: `deep_filter`(http.py:413)로 `None` 값이 제거되므로 실제 전송 body는 `"stop"` 키 자체가 빠진다. 위 예시에서는 비교 편의를 위해 적었음.

### 3-3. body 필드 출처 표 — `/v1/chat/completions` (ChatCompletionsRequestHandler.format, request_handlers.py:551-654)

| body 키 | 출처 (정확한 라인) | 비고 |
|---|---|---|
| `model` | `kwargs["model"]` = `await self.default_model()` (http.py:396) | OpenAIHTTPBackendArgs.model 또는 `/v1/models`에서 첫 번째 (http.py:310-321) |
| `stream` | `kwargs["stream"]` = `_args.stream` (http.py:397), 기본 `True` (http.py:119-122) | `arguments.stream = True`도 함께 세트(request_handlers.py:583) |
| `stream_options.include_usage` | 하드코딩 `True` | request_handlers.py:586 |
| `stream_options.continuous_usage_stats` | 하드코딩 `True` | request_handlers.py:587 |
| `max_completion_tokens` | (1순위) `data.output_metrics.text_tokens`, (2순위) `kwargs["max_tokens"]` = `_args.max_tokens` | request_handlers.py:591-600 |
| `stop` | (output_metrics.text_tokens 있을 때만) `None` | request_handlers.py:595 |
| `ignore_eos` | (output_metrics.text_tokens 있을 때만) `True` | request_handlers.py:596 |
| `messages` (rolled-up array) | 아래 분해 | request_handlers.py:607-649 |
| ┣ `messages[].role="system"` content | `" ".join(data.columns["prefix_column"])` | request_handlers.py:615-617 |
| ┣ `messages[].role="user"` content | **round-robin** `data.columns["text_column"]` / `image_column` / `video_column` / `audio_column` (각각 `_format_prompts`로 OpenAI content-part 모양 변환) | request_handlers.py:619-626 |
| ┣ (multi-turn) prior `messages` from `history` | `prev_requests[i].body["messages"]` 재귀 호출 결과 | request_handlers.py:567-572, 610-612 |
| ┣ (multi-turn) prior assistant content | `response.text` (또는 tool_calls 시 더 복잡한 구조) | request_handlers.py:632-649 |
| `tools` | `data.columns["tools_column"][0]` (string이면 json.loads) | request_handlers.py:519-529 |
| `tool_choice` | 첫 등장 시 `"required"` (set-default), `data.expects_tool_call=False`면 `"none"`로 override | request_handlers.py:530, 537-538 |
| `extras 머지` | `kwargs["extras"]` = `_args.extras` 가 `GenerationRequestArguments`이면 `arguments.model_combine(kwargs["extras"])`로 합쳐짐 (body/headers/params/files를 deep_update) | request_handlers.py:603-604 + request.py:61-89 |

### 3-4. body 필드 출처 표 — `/v1/completions` (TextCompletionsRequestHandler.format, request_handlers.py:166-235)

위와 동일하되 차이점:

| body 키 | 차이 |
|---|---|
| `max_tokens` | **`max_completion_tokens` 대신 `max_tokens`** (request_handlers.py:205, 209) |
| `prompt` | `messages` 대신 단일 문자열. `" ".join(prev_requests의 prompt + prefix_column + text_column + response.text)` (request_handlers.py:215-233) |
| `tools` / `messages` 관련 키 | 없음 |

### 3-5. body 필드 출처 표 — `/v1/responses` (ResponsesRequestHandler.format, request_handlers.py:1003-1057)

| body 키 | 출처 |
|---|---|
| `model` | 동일 |
| `stream` | 동일. **`stream_options` 미전송** (vLLM 동작에 위임, request_handlers.py:1032-1036 주석) |
| `max_output_tokens` | `data.output_metrics.text_tokens` OR `kwargs["max_tokens"]` (request_handlers.py:1038-1046) |
| `stop`, `ignore_eos` | 동일 (output_metrics.text_tokens 있을 때만) |
| `previous_response_id` | `kwargs["server_history"]=True` + `history`가 있을 때 마지막 응답의 `response_id` (request_handlers.py:1010, 1021-1024) |
| `instructions` | `" ".join(data.columns["prefix_column"])` (request_handlers.py:1051-1053) |
| `input` (array) | text/image/video/audio columns → round-robin → `{"role": "user", "content": [...]}` 형태. prior assistant response가 있으면 추가. (`_build_input_items`, request_handlers.py:970-1001) |

### 3-6. GenerationRequest.columns 키 ↔ body 키 매핑 한눈에

| `data.columns` 키 | `/v1/chat/completions` 위치 | `/v1/completions` 위치 | `/v1/responses` 위치 |
|---|---|---|---|
| `prefix_column` | system message content | prompt prefix | `instructions` |
| `text_column` | user message text part | prompt 본문 | input의 text part |
| `image_column` | user message image_url part | (미지원) | input의 image part |
| `video_column` | user message video_url part | (미지원) | (확인 필요 — `_format_prompts`는 audio만 처리, request_handlers.py:945-968) |
| `audio_column` | user message input_audio part (base64) | (미지원) | input의 file part (base64) |
| `tools_column` | `tools` + `tool_choice` | (미지원) | (확인 필요) |
| `tool_response_column` | role=tool messages (multi-turn) | — | — |

> `GenerationRequest`의 `columns: dict[str, list[Any]]` 스키마는 `schemas/request.py:234-240`. 실제 키 이름은 데이터 파이프라인 측 `GenerativeColumnMapper` 등이 정한다(데이터 파이프라인은 본 단계 범위 외, 다음 단계 후보).

## 4. 스트리밍 수신 루프 — RequestTimings 채우는 줄

### 4-1. 본문 (http.py:459-518)

```python
async def _resolve_streaming(self, request, request_info, request_handler, arguments, request_kwargs):
    ...
    try:
        request_info.timings.request_start = time.time()                          # http.py:481  ★ A
        async with self._async_client.stream(**request_kwargs) as stream:         # http.py:483
            stream.raise_for_status()                                             # http.py:484
            end_reached = False
            async for chunk in self._aiter_lines(stream):                         # http.py:487  ← (i) SSE 라인 반복
                stream.raise_for_status()
                iter_time = time.time()                                           # http.py:489  ★ B
                if request_info.timings.first_request_iteration is None:          # http.py:491
                    request_info.timings.first_request_iteration = iter_time      # http.py:492  ★ C
                request_info.timings.last_request_iteration = iter_time           # http.py:493  ★ D
                request_info.timings.request_iterations += 1                     # http.py:494  ★ E
                iterations = request_handler.add_streaming_line(chunk)           # http.py:496  ← 핸들러가 SSE 1줄 파싱
                if iterations is None or iterations <= 0 or end_reached:         # http.py:497
                    end_reached = end_reached or iterations is None              # http.py:498
                    if end_reached:
                        break                                                   # http.py:504
                    continue
                if request_info.timings.first_token_iteration is None:          # http.py:507
                    request_info.timings.first_token_iteration = iter_time      # http.py:508  ★ F (= TTFT 원점)
                    request_info.timings.token_iterations = 0                   # http.py:509
                    yield None, request_info                                    # http.py:510  ← 워커 "first_token" 게이트
                request_info.timings.last_token_iteration = iter_time           # http.py:512  ★ G
                request_info.timings.token_iterations += iterations             # http.py:513  ★ H
        request_info.timings.request_end = time.time()                          # http.py:515  ★ I
        gen_response = request_handler.compile_streaming(request, arguments)    # http.py:516
        self._check_tool_call_expectations(request, gen_response)               # http.py:517
        yield gen_response, request_info                                        # http.py:518
    except asyncio.CancelledError as err:                                       # http.py:519
        yield request_handler.compile_streaming(request, arguments), request_info  # http.py:521
        raise err
```

추가 보조 메서드 (http.py:524-534):

```python
async def _aiter_lines(self, stream):
    async for line in stream.aiter_lines():                                     # http.py:531  ← httpx의 라인 단위 SSE 디코더
        if not line.strip():
            continue
        yield line
```

> `httpx.Response.aiter_lines()` 는 SSE의 텍스트 라인을 1줄씩 yield. **즉 빈 줄(이벤트 구분자)은 스킵하므로 1 chunk = SSE 1줄 = `data: <json>` 또는 `event: <type>` 1줄.**

### 4-2. 핸들러 측 SSE 파싱

`/v1/chat/completions`의 `add_streaming_line` (request_handlers.py:701-736):

- `extract_line_data(line)`이 `"data: [DONE]"` → `None`, `data:` 접두가 없으면 `{}`, 그 외엔 JSON 디코드.
- 디코드된 `choice.delta.content`가 있으면 `streaming_texts.append(content)` → `updated=True`
- `delta.tool_calls`가 있으면 누적 → `updated=True`
- `usage`가 있으면 `streaming_usage`에 저장
- 반환: `1`(updated), `0`(ignored), `None`(`[DONE]`)

`/v1/responses`의 `add_streaming_line` (request_handlers.py:1133-1176):

- 이벤트 타입에 따라 분기.
- `response.output_text.delta` → text append → `1`
- `response.completed` / `response.failed` / `response.incomplete` → 최종 usage 저장, **`None` 반환 → 스트림 종료**.
- 기타 → `0`

### 4-3. 측정지점 표 — "지표 → 채워지는 timings 필드 → 파일:라인"

| 지표 (의미) | timings 필드 | 채워지는 줄 (파일:라인) | 단위 | 채우는 함수 |
|---|---|---|---|---|
| 워커가 요청을 큐에 넣은 시각 | `timings.queued` | `worker_group.py:583` | Unix 초(float) | `time.time()` |
| 워커가 큐에서 꺼낸 시각 | `timings.dequeued` | `worker.py:464` (`_dequeue_next_conversation`) | Unix 초 | `time.time()` |
| 워커가 strategy로 잡은 목표 시각 | `timings.targeted_start` | `worker.py:466` | Unix 초 | strategy가 정한 값 |
| 스케줄링 확정 시각 | `timings.scheduled_at` | `worker.py:487, 491` (`_schedule_request`) | Unix 초 | `time.time()` 또는 `target_start` |
| 백엔드 resolve 진입 직전 | `timings.resolve_start` | `worker.py:494` | Unix 초 | `time.time()` |
| **요청 전송 직전 (스트리밍)** | `timings.request_start` | **`http.py:481`** ★A | **Unix 초** | **`time.time()`** |
| 요청 전송 직전 (비스트리밍) | `timings.request_start` | `http.py:450` | Unix 초 | `time.time()` |
| SSE 첫 청크 도착 시각 (의미와 무관) | `timings.first_request_iteration` | `http.py:492` ★C | Unix 초 | `time.time()` (iter_time) |
| SSE 마지막 청크 도착 시각 (의미와 무관) | `timings.last_request_iteration` | `http.py:493` ★D | Unix 초 | iter_time |
| SSE 청크 총 횟수 (의미와 무관) | `timings.request_iterations` | `http.py:494` ★E | 개수(int) | `+= 1` |
| **첫 token-bearing 청크 도착 (= TTFT 원점)** | **`timings.first_token_iteration`** | **`http.py:508`** ★F | **Unix 초** | **iter_time = `time.time()` at http.py:489** |
| 마지막 token-bearing 청크 도착 | `timings.last_token_iteration` | `http.py:512` ★G | Unix 초 | iter_time |
| 스트림 동안 누적 토큰 수 (핸들러가 반환한 iterations 합) | `timings.token_iterations` | `http.py:509, 513` ★H | 개수(int) | `+= iterations` |
| **요청 전송 완료 (스트리밍: 본문 모두 수신 + 핸들러 compile 직전)** | **`timings.request_end`** | **`http.py:515`** ★I | **Unix 초** | **`time.time()`** |
| 요청 전송 완료 (비스트리밍) | `timings.request_end` | `http.py:452` | Unix 초 | `time.time()` |
| 워커에서 resolve 전체 종료 / 다음 update 발송 직전 | `timings.resolve_end` | `worker.py:413` (정상), `worker.py:425/433/444` (취소/에러/conversation 잔여) | Unix 초 | `time.time()` |
| 워커 그룹의 received_callback 단계 | `timings.finalized` | (정의는 `schemas/info.py:82-85`; 채우는 위치는 본 단계 범위 외, **확인 필요** — `worker_group.py:618-` 의 `received_callback`이 후보) | Unix 초 | (확인 필요) |

> 모든 타임스탬프는 **`time.time()`** 한 함수로 일관되게 채워진다 — `perf_counter()`, `monotonic()` 사용 없음 (`grep -n 'perf_counter\|monotonic' src/guidellm/backends/openai/http.py src/guidellm/scheduler/worker*.py` 결과 0건). 단위는 **Unix epoch seconds (float)**.

### 4-4. 스트리밍 응답 예시 1개 (chat/completions, vLLM)

서버가 보내는 SSE (간략):

```
data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1734000000,"model":"Qwen/Qwen2-7B-Instruct","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}],"usage":{"prompt_tokens":24,"completion_tokens":0,"total_tokens":24}}

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"Sure"}}],"usage":{"prompt_tokens":24,"completion_tokens":1,"total_tokens":25}}

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":", here"}}],"usage":{"prompt_tokens":24,"completion_tokens":2,"total_tokens":26}}

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"'s one."}}],"usage":{"prompt_tokens":24,"completion_tokens":4,"total_tokens":28}}

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":24,"completion_tokens":4,"total_tokens":28}}

data: [DONE]
```

이 6개 라인에서 timings가 채워지는 순서:

| # | 라인 | `add_streaming_line` 반환 | http.py 루프 효과 | 갱신되는 timings |
|---|---|---|---|---|
| 1 | role 청크 (`"content":""`) | `0` (content 비어 있어 updated=False) | first_request_iteration ✔, last_request_iteration ✔, request_iterations += 1, **first_token_iteration 미설정** (iterations≤0이라 continue) | `first_request_iteration`, `last_request_iteration`, `request_iterations` |
| 2 | "Sure" | `1` | iter_time이 token 청크 첫 등장 → **`first_token_iteration = iter_time`**, `token_iterations = 0`, yield(None, info), 이어서 `last_token_iteration = iter_time`, `token_iterations += 1` | `first_token_iteration`, `last_token_iteration`, `token_iterations` |
| 3 | ", here" | `1` | last_token_iteration 갱신, `token_iterations += 1` | `last_token_iteration`, `token_iterations` |
| 4 | "'s one." | `1` | 동일 | `last_token_iteration`, `token_iterations` |
| 5 | finish_reason 청크 (`"delta":{}`) | `0` | continue | `last_request_iteration`, `request_iterations` |
| 6 | `data: [DONE]` | `None` | end_reached=True → break | (없음) |

루프 종료 직후 `request_end = time.time()` 한 번 더 박힘. 워커가 `compile_streaming` 결과를 받고 나서 `resolve_end = time.time()`를 추가로 박는다(worker.py:413).

⚠️ **주의**: `token_iterations += iterations`에서 `iterations`는 **핸들러가 그 라인에서 본 "업데이트 단위 수"**(text content 있으면 1, tool_call delta 있으면 +1) — `usage.completion_tokens` 같은 서버측 토큰 카운트와는 다르다. K-Perf TPOT 계산 시 어떤 카운트를 쓸지 명시적으로 선택해야 한다(아래 6-1절).

## 5. 타임스탬프 단위 — 한 번 더 정리

| 항목 | 단위 | 출처 |
|---|---|---|
| 모든 `timings.*` (request_start, first_token_iteration, …, finalized) | **Unix epoch seconds (float, time.time())** | `schemas/info.py:22-85` 모든 필드 docstring + 실측 호출 위치 |
| `timings.request_iterations`, `timings.token_iterations` | **개수 (int)** | `schemas/info.py:68-73` |
| `httpx.Timeout(read=...)` | 초 (float) | http.py:235-239 |
| `scheduler_start_time` (RequestInfo) | Unix epoch seconds (float) | `worker_group.py:580` 가 `self.start_time` (WorkerProcessGroup.start의 `start_time` 인자) 를 전달 |

> **K-Perf 측 정규화 권고**: 출력 시 ms로 변환할지 µs로 변환할지는 K-Perf 통합 산출물 표준에 맞춰 정의해야 함. **GuideLLM 내부에서는 초(float)로 통일**되어 있다.

## 6. K-Perf TTFT / TPOT / E2EL — timings 필드로의 계산 매핑

### 6-1. 계산식 매핑

| K-Perf 지표 | GuideLLM timings 기반 계산식 (자료에서 유도) | 단위 | 근거 |
|---|---|---|---|
| **TTFT (Time To First Token)** | `timings.first_token_iteration - timings.request_start` | 초 | http.py:481, 508 |
| **E2EL (End-to-End Latency)** | (스트리밍) `timings.request_end - timings.request_start` 또는 더 외측의 `timings.resolve_end - timings.resolve_start` | 초 | http.py:481, 515; worker.py:494, 413 |
| **TPOT (Time Per Output Token)** | "자료에 명시되지 않음 — K-Perf 정의 필요". **후보 정의 3가지**: <br>① `(last_token_iteration - first_token_iteration) / max(token_iterations - 1, 1)` — `token_iterations`는 핸들러가 카운트한 업데이트 수. <br>② `(last_token_iteration - first_token_iteration) / max(output_metrics.text_tokens - 1, 1)` — 서버측 토큰 카운트(`usage.completion_tokens`)를 분모로. <br>③ `(request_end - first_token_iteration) / max(output_metrics.text_tokens, 1)` — 종료 시각 포함. | 초/token | timings 정의는 정확하나 **TPOT 정의식 자체가 코드에 없음** |
| **ITL (Inter-Token Latency, 분포)** | "자료에 명시되지 않음 — K-Perf 정의 필요". GuideLLM은 청크 단위 시각만 보관하므로 **개별 토큰 사이 간격은 직접 측정하지 않는다**. 보유 정보로 추정 가능한 것은 청크 간 간격(`last_token_iteration` 1회만 마지막 값으로 덮어씀)인데 중간값 보관이 없으므로 ITL 분포는 산출 불가. K-Perf가 ITL 분포를 산출하려면 GuideLLM 측에 청크 시각 리스트를 추가 저장하거나, 분포 대신 평균 TPOT만 산출하도록 한정해야 함. | 초/token | (코드에 보관 안 됨) |

> **확정 권고**:
> - TTFT, E2EL은 식 그대로 사용 가능. **확인됨**.
> - TPOT은 K-Perf 사양에서 "분모로 무엇을 쓸지(`token_iterations` vs `usage.completion_tokens`)"를 명시 결정해야 함. 산출물 검증성을 우선시한다면 **서버측 `usage.completion_tokens`(GenerationResponse.output_metrics.text_tokens)** 가 정확. 통신 청크 기반(`token_iterations`)은 핸들러 구현(특히 tool_call이 섞일 때 `1` 증가)에 영향을 받아 모델 출력 토큰 수와 다를 수 있다.
> - **ITL 분포가 필요하면 GuideLLM upstream 패치 또는 K-Perf fork 필요**.

### 6-2. 검증 가능한 계산 — `schemas/info.py:174-191` computed_field

`RequestInfo` 가 다음 둘을 computed_field로 제공:

```python
@computed_field
@property
def started_at(self) -> float | None:                          # schemas/info.py:172-180
    return self.timings.request_start or self.timings.resolve_start

@computed_field
@property
def completed_at(self) -> float | None:                        # schemas/info.py:182-190
    return self.timings.request_end or self.timings.resolve_end
```

→ K-Perf E2EL의 안전한 계산 후보:

```
E2EL = info.completed_at - info.started_at
     = (info.timings.request_end or resolve_end) - (info.timings.request_start or resolve_start)
```

이 값은 **스트리밍/비스트리밍 모두에 안전**. 비스트리밍에서는 `request_start`/`request_end`가 http.py:450/452에서 박히고, 워커가 `resolve_end`(worker.py:413)을 별도로 박으므로 둘 다 사용 가능.

## 7. 실패 / 타임아웃 / 비스트리밍 응답 처리 경로

### 7-1. 비스트리밍 (`_resolve_non_streaming`, http.py:429-457)

```python
request_info.timings.request_start = time.time()                # http.py:450
response = await self._async_client.request(**request_kwargs)   # http.py:451 ★ 단일 await — read timeout 적용
request_info.timings.request_end = time.time()                  # http.py:452
response.raise_for_status()                                     # http.py:453  ★ 4xx/5xx → HTTPStatusError raise
data = response.json()                                          # http.py:454
gen_response = request_handler.compile_non_streaming(request, arguments, data)  # http.py:455
yield gen_response, request_info                                # http.py:456
self._check_tool_call_expectations(request, gen_response)       # http.py:457
```

- `first_token_iteration`, `last_token_iteration`, `request_iterations`, `token_iterations`는 **전혀 채워지지 않는다**. TTFT 측정 불가.
- 4xx/5xx면 `httpx.HTTPStatusError` 발생 → 호출자(`resolve`)는 이를 catch 안 함 → 워커(`worker.py:428-437`)가 `except Exception`으로 잡아서 `"errored"` 업데이트 발송.
- read timeout이면 `httpx.ReadTimeout` (httpx 라이브러리 동작) → 동일 경로로 errored.

### 7-2. 스트리밍 (`_resolve_streaming`, http.py:459-522)

- `try/except asyncio.CancelledError` 만 명시 처리 (http.py:519-522).
  - 취소 시점에 핸들러로 모은 부분 결과를 `compile_streaming`으로 생성해 yield 후 raise → 워커가 partial response를 받아 `_send_update("cancelled", ...)` 까지 마무리할 수 있게 함 (worker.py:420-427).
- HTTPStatusError / ReadTimeout / 기타 모든 예외는 catch 없음 → 워커가 `except Exception` (worker.py:428-437)으로 잡아 `errored` 발송.
- `stream.raise_for_status()`는 두 번 호출됨 (http.py:484, 488) — 진입 직후 + 매 청크 직후. 서버가 도중에 5xx를 흘려보내도 다음 청크에서 raise.

### 7-3. 타임아웃 동작 종합

| 시나리오 | httpx 동작 | timings 상태 | 워커 처리 |
|---|---|---|---|
| connect timeout (`timeout_connect` 초과, 기본 5s) | `httpx.ConnectTimeout` | `request_start` 박힘, `request_end` 미설정 (스트리밍) / 미설정 (비스트리밍, http.py:451에서 raise) | "errored" |
| read timeout (`timeout`, 기본 **None=무한**) | `httpx.ReadTimeout` (timeout 명시 시) | 위와 유사. 스트리밍이면 마지막 chunk 시각까지는 박힘 | "errored" |
| 4xx/5xx | `httpx.HTTPStatusError` (raise_for_status) | 스트리밍: 진입 직후 raise면 timings는 request_start만; 매 청크 검증에서 raise면 first_token_iteration까지 박힐 수 있음 | "errored" |
| 정상 스트림 종료(`[DONE]`) | 정상 | 모든 timings 정상 | "completed" |
| 클라이언트 취소(`asyncio.CancelledError`) | 부분 결과로 compile 후 raise | 부분만 박힘 | "cancelled" |

### 7-4. `tool_call` 미생성 시 (http.py:561-592)

벤치마크 정합성 검사용. `request.expects_tool_call=True`인데 `response.tool_calls`가 비면:
- `ignore_continue`: no-op
- `ignore_stop`: `asyncio.CancelledError` raise → 워커가 "cancelled"
- **`error_stop`** (기본값, http.py:142): `ValueError` raise → 워커가 "errored"

성능 측정에는 부수적이지만, 데이터셋이 tool_call 강제일 때 errored 카운트가 부풀어 K-Perf의 `max_error_rate` 종료 조건을 트리거할 수 있음. **K-Perf 가이드에 명시 권고**.

## 8. 본 단계에서 의도적으로 미열람한 곁가지

- `EmbeddingsRequestHandler`, `PoolingRequestHandler`의 `format`/`compile_*` 본문 (벤치마크 측정 지점은 동일).
- `GenerationRequestArguments.model_combine`의 `deep_update` 내부 동작 (`utils/dict.py`).
- `worker_group.received_callback`이 `timings.finalized`를 채우는지 (★ 확인 필요).
- `stream.aiter_lines()` 의 내부 디코딩(특히 chunked transfer + HTTP/2 SETTINGS 동안의 idle 처리) — httpx 라이브러리 책임.
- 스트리밍 응답에서 vLLM이 보내는 `usage.completion_tokens` 의 정확한 증분 의미.

위 항목 중 ★는 K-Perf E2EL 정확도에 영향이 있을 수 있으므로 다음 단계 후보.
