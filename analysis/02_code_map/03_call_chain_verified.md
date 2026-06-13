# 03. `guidellm benchmark run` 호출 체인 — 본문 검증

> 분석 대상: `vllm-project/guidellm` @ `fb3e862`
> 분석 모드: **함수 본문 실제 열람**. 추적 범위는 `cli.run → benchmark_generative_text → resolve_backend / resolve_request_loader → Benchmarker.run → Scheduler.run → WorkerProcess.run_async → backend.resolve` 만. `_resolve_non_streaming` / `_resolve_streaming` 본문은 다음 단계로 미룸.
> 모든 주장은 `파일:라인` 근거 동반.

## 1. 검증된 호출 순서

### 1-1. 상위 → 중위 (entrypoints 진입까지)

| # | 호출 위치 | 호출 대상 | 본문 검증 결과 |
|---|---|---|---|
| 1 | `cli/benchmark/run.py:407-409` | `BenchmarkGenerativeTextArgs.create(scenario=..., **kwargs)` | ✅ 확인됨. `create`는 시나리오 파일(json/yaml/builtin)을 로드한 뒤 kwargs로 오버라이드하고 `cls.model_validate`로 pydantic 인스턴스를 만든다 (`benchmark/schemas/generative/entrypoints.py:78-124`) |
| 2 | `cli/benchmark/run.py:418-428` | `asyncio.run(benchmark_generative_text(args=args, progress=..., console=...))` | ✅ 확인됨 |
| 3 | `benchmark/entrypoints.py:453` | `resolve_backend(backend_args=args.backend_kwargs, console=console)` | ✅ 확인됨 |
| 4 | `benchmark/entrypoints.py:457-459` | `resolve_processor(processor=args.processor, model=model, console=console)` | ✅ 확인됨. 반환은 단순히 `processor or model` (entrypoints.py:161-177) — **실제 `from_pretrained` 호출은 여기서 일어나지 않고 지연됨** |
| 5 | `benchmark/entrypoints.py:460-472` | `resolve_request_loader(data, loader, model, processor, processor_args, data_column_mapper, data_preprocessors, data_finalizer, data_collator, random_seed, console)` | ✅ 확인됨 |
| 6 | `benchmark/entrypoints.py:474-475` | `TransientPhaseConfig.create_from_value(args.warmup/cooldown)` | (범위 밖, 통과) |
| 7 | `benchmark/entrypoints.py:489-504` | `resolve_profile(...)` | (범위 밖, 통과) |
| 8 | `benchmark/entrypoints.py:505-507` | `resolve_output_formats(args.outputs, args.output_dir, console)` | (범위 밖, 통과) |
| 9 | `benchmark/entrypoints.py:516-518` | `Benchmarker()` 인스턴스화 | ✅ 확인됨. `ThreadSafeSingletonMixin`이므로 같은 프로세스에서 항상 동일 인스턴스 (`benchmark/benchmarker.py:43-47`) |
| 10 | `benchmark/entrypoints.py:519-531` | `benchmarker.run(accumulator_class=GenerativeBenchmarkAccumulator, benchmark_class=GenerativeBenchmark, requests=request_loader, backend=backend, profile=profile, environment=NonDistributedEnvironment(), progress=progress, sample_requests=args.sample_requests, warmup=warmup, cooldown=cooldown, prefer_response_metrics=args.prefer_response_metrics)` | ✅ 확인됨. 인자 순서·키워드까지 확인 완료 |

> **2단계 추정 ↔ 실제 차이**: 2단계는 "resolve_processor가 `AutoTokenizer.from_pretrained`를 호출한다"고 추정했으나 본문상 그 함수는 **단순 식별자 정규화만 수행**하고 토크나이저는 나중에 `ProcessorFactory`가 lazy하게 로드한다 (`benchmark/entrypoints.py:161-177`, 호출은 `resolve_request_loader` 내부에서 `ProcessorFactory(processor=..., processor_args=...)` 생성 시점에 잠재됨). **수정**.

### 1-2. resolve_backend 본문 (entrypoints.py:81-139)

```
resolve_backend(backend_args, console)
  ├─ backend_instance = Backend.create(backend_args)              entrypoints.py:110
  │     └─ backends/backend.py:102-122
  │         └─ cls.get_registered_object(args.kind) → 등록된 백엔드 클래스 조회 → backend(args) 생성
  ├─ await backend_instance.process_startup()                     entrypoints.py:117
  ├─ await backend_instance.validate()                            entrypoints.py:118
  ├─ model = await backend_instance.default_model()               entrypoints.py:125
  ├─ await backend_instance.process_shutdown()                    entrypoints.py:127
  └─ return (backend_instance, model)
```

⚠️ **중요한 발견**: 메인 프로세스에서 `process_startup → validate → default_model → process_shutdown`을 **한 번 돌린다**. 이는 모델 ID 확인용 1회 셰이크다운이며, **각 워커 프로세스에서 다시 `process_startup`을 호출**한다(다음 절). 즉 사내 추론 서버 입장에서 보면 헬스/모델 조회 트래픽이 (1 + 워커 수)회 발생한다. **2단계 추정 확인됨 + 수치 명확화**.

### 1-3. resolve_request_loader 본문 (entrypoints.py:229-306)

```
resolve_request_loader(...)
  ├─ pre_list = [data_column_mapper] + data_preprocessors          entrypoints.py:276
  ├─ config = DataEntrypointArgs(
  │       loader=loader,
  │       data=data,
  │       preprocessors=pre_list,
  │       finalizer=data_finalizer,
  │   )                                                            entrypoints.py:277-282
  └─ request_loader = DataLoaderRegistry.create(
        config=config,
        processor_factory=ProcessorFactory(
            processor=processor if processor is not None else model,
            processor_args=processor_args,
        ),
        collator=(
            data_collator if callable(data_collator) else GenerativeRequestCollator()
        ),
        random_seed=random_seed,
    )                                                              entrypoints.py:283-293
```

- `DataLoaderRegistry.create(config, **kwargs)` 는 registry에서 `config.loader.kind`(`run.py`에서 `"pytorch"`로 강제됨)를 키로 `TorchDataLoader` 클래스를 찾아 인스턴스화 (`data/loaders/loader.py:21-23`)
- `TorchDataLoader`는 PyTorch `DataLoader(batch_size=1, collate_fn=collator, ...)` 를 상속 (`data/loaders/torch.py:177-217`)
- `__iter__` 1회 호출 시 `DatasetsIterator` 안에서 deserializer→preprocessor→finalizer 파이프라인이 동작 — **본 단계에서는 그 내부 로직 미열람**

> **2단계 추정 ↔ 실제 차이**: 2단계는 "TorchDataLoader → DatasetDeserializerFactory → load_dataset"의 흐름이라 적었다. 실제로는 `DataEntrypointArgs` + `DataLoaderRegistry.create(config, processor_factory, collator, random_seed)` 라는 한 단계가 더 있고, **deserializer/preprocessor/finalizer가 config 안에 묶여 함께 전달된다**. 흐름 자체는 같으나 데이터 객체의 모양이 더 정확해졌다.

### 1-4. Benchmarker.run 본문 (benchmarker.py:57-178)

```
Benchmarker.run(accumulator_class, benchmark_class, requests, backend, profile, environment, warmup, cooldown, sample_requests, prefer_response_metrics, progress)
  with self.thread_lock:                                          benchmarker.py:92
    run_id = uuid.uuid4()
    strategies_generator = profile.strategies_generator()          benchmarker.py:97
    strategy, constraints = next(strategies_generator)             benchmarker.py:100
    while strategy is not None:
      config = BenchmarkConfig(run_id, run_index, strategy, constraints, ...)  benchmarker.py:106-128
      accumulator = accumulator_class(config=config)               benchmarker.py:129
      scheduler = Scheduler()                                      benchmarker.py:131 ★ Singleton
      async for (response, request, request_info, scheduler_state) in scheduler.run(
          requests=requests,
          backend=backend,
          strategy=strategy,
          env=environment,
          **constraints or {},
      ):                                                           benchmarker.py:133-144
        accumulator.update_estimate(response, request, request_info, scheduler_state)  benchmarker.py:146-151
        if progress: await progress.on_benchmark_update(accumulator, scheduler_state)
      benchmark = benchmark_class.compile(accumulator=accumulator, scheduler_state=scheduler_state)  benchmarker.py:161-164
      yield benchmark
      try: strategy, constraints = strategies_generator.send(benchmark)       benchmarker.py:172
      except StopIteration: strategy = None
```

> **2단계 추정 ↔ 실제 차이**: 2단계는 "Benchmarker가 Scheduler.run을 호출한다"까지만 추정했다. 본문에서 확인된 추가 사실:
> 1. **`Scheduler.run`은 매 strategy마다 호출되며 결과 스트림에서 매 이벤트마다 `accumulator.update_estimate(response, request, request_info, scheduler_state)`로 누적**한다. 이게 응답이 측정 결과로 변환되는 합류 지점.
> 2. **`profile.strategies_generator()`는 generator + `.send(benchmark)`** 기반 — 직전 벤치마크 결과를 다음 strategy 결정에 피드백한다(예: sweep). 2단계는 "프로파일이 여러 strategy를 생성한다"고만 적었음. **확인됨 + 매커니즘 명확화**.
> 3. `Scheduler.run`의 yield 모양은 **4-tuple `(response, request, request_info, scheduler_state)`** — 단순 response가 아니다. 2단계에서는 yield 모양을 명시하지 않았음. **추가 사항**.

### 1-5. Scheduler.run 본문 (scheduler.py:61-159)

```
Scheduler.run(requests, backend, strategy, env, **constraints)
  with self.thread_lock:                                          scheduler.py:99
    if env is None: env = NonDistributedEnvironment()
    try:
      resolved_constraints = ConstraintsInitializerFactory.resolve_constraints(constraints)   scheduler.py:110-112
      (local_requests, local_strategy, local_constraints) = await env.sync_run_params(requests, strategy, resolved_constraints)   scheduler.py:113-117
      worker_group = WorkerProcessGroup(
          requests=local_requests, backend=backend,
          strategy=local_strategy, **local_constraints,
      )                                                            scheduler.py:120-125
      await worker_group.create_processes()                        scheduler.py:126
      local_start_time = await env.sync_run_start()                scheduler.py:127
      await worker_group.start(local_start_time)                   scheduler.py:128
      async for (response, request, request_info, state) in worker_group.request_updates():   scheduler.py:131-136
        await env.update_run_iteration(response, request, request_info, state)
        yield response, request, request_info, state              scheduler.py:140
    except Exception as err:
      await env.sync_run_error(err); raise err
    finally:
      if worker_group is not None:
        err = await worker_group.shutdown()
        if err is not None: await env.sync_run_error(err)
    async for (...) in env.sync_run_end():                        scheduler.py:153-159
      yield ...
```

> **2단계 추정 ↔ 실제 차이**:
> - 2단계는 `Scheduler.run → WorkerProcessGroup → WorkerProcess` 라고 적었는데, 실제로는 `Environment` 라는 **분산 동기화 계층**이 한 번 더 끼어 있다 — `env.sync_run_params / sync_run_start / update_run_iteration / sync_run_error / sync_run_end`. 단일 노드(`NonDistributedEnvironment`)에서는 대부분 no-op이지만 멀티 노드 환경 가능성이 호출 그래프에 명시되어 있음. **2단계 흐름은 유효, 1 계층 누락**.
> - **`Scheduler.run`은 두 번 yield**한다: 워커 그룹이 살아 있을 동안의 `worker_group.request_updates()` 루프(140), 그리고 종료 후 `env.sync_run_end()`로부터의 분산 동기 루프(153-159). 단일 노드에서는 후자는 비어 있을 가능성. **확인 필요(다음 단계에서 NonDistributedEnvironment 실체 확인)**.

### 1-6. WorkerProcessGroup.create_processes / start (worker_group.py:142-360)

핵심 단계:
1. **동시성 한도 계산**(worker_group.py:154-181)
   - `max_conc = min(strategy.requests_limit, backend.requests_limit) or settings.max_concurrency`
   - `num_processes = min(max_conc, strategy.processes_limit, backend.processes_limit, settings.max_worker_processes)`
   - `per_proc_max_conc = max_conc // num_processes`
2. **IPC 채널 초기화**(worker_group.py:187-224)
   - `mp_context = get_context(settings.mp_context_type)` (spawn/fork 등)
   - `mp_manager = mp_context.Manager()`
   - `startup_barrier = mp_context.Barrier(num_processes + 1)`
   - 4 종 Event: `requests_generated_event`, `constraint_reached_event`, `shutdown_event`, `error_event`
   - `messaging`은 `settings.mp_messaging_object`에 따라 `Queue / ManagerQueue / Pipe` 중 하나로 분기
3. **워커 프로세스 N개 생성**(worker_group.py:227-258)
   ```python
   worker = WorkerProcess(
       worker_index=rank,
       messaging=self.messaging.create_worker_copy(worker_index=rank, ...),
       backend=self.backend,                       ★ backend 객체가 pickle되어 워커로 전달
       strategy=self.strategy,
       async_limit=async_limit,                    ★ 워커당 동시 요청 수
       fut_scheduling_time_limit=0.0,
       startup_barrier=..., requests_generated_event=...,
       constraint_reached_event=..., shutdown_event=..., error_event=...,
   )
   proc = self.mp_context.Process(target=worker.run, daemon=False)
   proc.start()
   ```
4. **`_process_health_monitor`** 백그라운드 태스크가 워커 SIGSEGV/OOM 감시 (worker_group.py:275-306)
5. **`start(start_time)`** — `WorkerGroupState` 생성, `messaging.start(send_items=state.requests_generator(self.requests), receive_callback=state.received_callback, ...)` (worker_group.py:308-360)

> **2단계 추정 ↔ 실제 차이**:
> - 2단계는 "워커가 backend.process_startup을 호출한다"고만 적었다. **백엔드 인스턴스 자체가 pickle을 거쳐 워커 프로세스로 복제**된다는 점이 확인됨 (`worker_group.py:246, 256`의 `target=worker.run`은 forking/spawning 동작). httpx AsyncClient는 그 자체로 pickle 불가능이므로 `process_startup` 안에서 클라이언트를 새로 생성한다 — 이는 `http.py:212`(`self._async_client: httpx.AsyncClient | None = None` 초기값)와 `http.py:233`(`process_startup` 안에서 클라이언트 생성)로 뒷받침된다. **확인됨 + 메커니즘 명확화**.
> - **워커 동시성 제한**(`async_limit`)은 워커 인스턴스 자체의 멤버이며, 각 워커는 내부에서 `asyncio.Semaphore(async_limit)`로 in-flight 요청을 제한한다(`worker.py:304`). **2단계 미언급**.

### 1-7. WorkerGroupState.requests_generator (worker_group.py:545-616)

이 메서드가 **`RequestInfo` 객체를 만들어 모든 요청에 부착**하는 진입점:

```python
def _turn_iter(requests_chain):
    conv_id = uuid.uuid4()
    for i, request in enumerate(requests_chain):
        request_info = RequestInfo(
            request_id=self._find_request_id(request),
            conversation_id=conv_id, turn_index=i,
            status="queued", scheduler_process_id=0,
            scheduler_start_time=self.start_time,
        )
        state_update = self._locked_update(request_info)
        request_info.timings.queued = time.time()                  worker_group.py:583
        self.messaging.buffer_receive_queue.sync_put(
            (None, request, request_info, state_update.state)      worker_group.py:586-588 ★ 메인 측 즉시 "queued" 업데이트
        )
        yield request, request_info

for request_chain in requests:
    yield list(_turn_iter(request_chain))                          worker_group.py:597 ★ 워커에 들어가는 메시지 = 한 conversation
```

> **2단계 추정 ↔ 실제 차이 / 새 사실**:
> - **`RequestInfo`는 메인 프로세스의 `requests_generator`에서 만들어진다**(워커가 만드는 게 아님). 워커에는 `(request, request_info)` 페어가 묶인 **conversation 단위 리스트**로 전달된다.
> - **워커로 가는 메시지의 정확한 모양**: `ConversationT[RequestT] = list[tuple[GenerationRequest, RequestInfo]]` (해석은 `scheduler/schemas.py:50-54`).
> - 메인 측은 워커 응답을 기다리기도 전에 이미 **"queued" 상태 업데이트를 자가 publish**한다 (worker_group.py:586-588). 그래서 같은 request_id에 대해 status가 queued → pending → in_progress → first_token → completed/errored/cancelled 의 순서로 흘러나간다.

### 1-8. WorkerProcess.run_async → backend.resolve (worker.py:165-449)

```
WorkerProcess.run() → asyncio.run(self.run_async())                worker.py:145-163
  └─ run_async()                                                   worker.py:165-214
     ├─ stop_task = asyncio.create_task(self._stop_monitor())      ★ error/shutdown 이벤트 감시
     └─ request_proc_task = asyncio.create_task(self._process_requests())
         └─ _process_requests()                                    worker.py:236-261
            ├─ await self._processing_startup()                    worker.py:246
            │   └─ await self.backend.process_startup()           worker.py:266 ★★ 워커별 httpx AsyncClient 생성
            │   └─ await self.backend.validate()                   worker.py:268 ★★ 워커별 헬스체크 1회
            │   └─ await self.messaging.start(...)                 worker.py:271-273
            │   └─ await wait_for_sync_barrier(startup_barrier)    worker.py:277-280
            ├─ processing_task = asyncio.create_task(self._process_requests_loop())  worker.py:249
            ├─ await wait_for_sync_event(constraint_reached_event) worker.py:250-253
            ├─ processing_task.cancel()                            worker.py:254
            └─ await self._cancel_requests_loop()                  worker.py:257
            └─ finally: await self._processing_shutdown()          worker.py:261
                └─ await self.backend.process_shutdown()           worker.py:286

_process_requests_loop():                                          worker.py:295-348
  async_semaphore = asyncio.Semaphore(self.async_limit)            worker.py:304
  while True:
    await async_semaphore.acquire()                                worker.py:328
    request_time = await self.strategy.next_request_time(worker_index=self.worker_index)  worker.py:329-331
    if (time_until := request_time - time.time()) >= fut_scheduling_time_limit:
      await asyncio.sleep(time_until - fut_scheduling_time_limit)  worker.py:333-336
    request_task = asyncio.create_task(
        self._process_next_request(target_start=request_time))     worker.py:338-340
    request_task.add_done_callback(_task_done)
    # _task_done은 conversation에 다음 turn이 남아 있으면 _wait_then_requeue로 재큐잉

_process_next_request(target_start):                               worker.py:371-449
  history, conversation = await self._dequeue_next_conversation(target_start)  worker.py:391
    └─ _dequeue_next_conversation: 메시지 큐에서 conversation 꺼내고
       conversation[0]의 (request, request_info)를 꺼내 timings.dequeued/targeted_start/scheduler_node_id 채움 + "pending" 송신
  request, request_info = conversation.pop(0)                      worker.py:392
  await self._schedule_request(request, request_info, target_start)  worker.py:395
    └─ _schedule_request: timings.scheduled_at 설정 + 필요 시 sleep + timings.resolve_start = time.time() + "in_progress" 송신

  async for resp, info in self.backend.resolve(                    worker.py:397-399  ★★★★
      request,            # GenerationRequest
      request_info,       # RequestInfo (timings 포함)
      history or None,    # list[(GenerationRequest, GenerationResponse|None)] | None
  ):
      request_info = info  # backend가 반환한 (mutated) RequestInfo로 갱신
      if resp is None and request_info.timings.first_token_iteration is not None:
          self._send_update("first_token", None, request, request_info)
      response = resp

  # 루프 후
  request_info.timings.resolve_end = time.time()                   worker.py:413
  self._send_update("completed", response, request, request_info)  worker.py:414
  history.append((request, response))                              worker.py:417
  return history, conversation, request_info
```

## 2. 단계 간 데이터 전달 표

> "요청 1건"이 시간순으로 어떤 자료구조로 표현되는지.

| 단계 | 넘기는 자료구조 (클래스명/타입) | 핵심 필드 | 정의된 파일:라인 |
|---|---|---|---|
| (a) CLI 파싱 후 args | `BenchmarkGenerativeTextArgs` (pydantic) | `backend_kwargs: BackendArgs`, `data: list[DataArgs]`, `data_loader: DataLoaderArgs`, `data_column_mapper/preprocessors/finalizer`, `processor`, `processor_args`, `profile`, `rate`, `random_seed`, `warmup/cooldown/rampup`, `max_*`, `sample_requests`, `over_saturation`, `outputs`, `output_dir` | `benchmark/schemas/generative/entrypoints.py:51-275` |
| (b) Backend args 분리 | `BackendArgs` 서브클래스(예: `OpenAIHTTPBackendArgs`) | `kind: str` + 구현체별 필드(`target`, `model`, `request_format`, …) | `backends/backend.py:30-65` + `backends/openai/http.py` (별도 파일) |
| (c) Backend 인스턴스 | `Backend` 서브클래스(예: `OpenAIHTTPBackend`) — `BackendInterface[GenerationRequest, GenerationResponse]` 구현 | `process_startup/validate/default_model/process_shutdown/resolve`, `_async_client: httpx.AsyncClient \| None` | `backends/backend.py:68-152`, `scheduler/schemas.py:82-156`, `backends/openai/http.py:323-329` |
| (d) Data 파이프라인 설정 | `DataEntrypointArgs` (loader+data+preprocessors+finalizer 묶음) | `loader`, `data`, `preprocessors`, `finalizer` | `data/schemas/entrypoints.py` (정의 위치는 `DataEntrypointArgs` 심볼; 본 단계에서 위치만 식별) |
| (e) Request 데이터로더 | `DataLoader[GenerationRequest]` (Protocol) — 실제로는 `TorchDataLoader` (PyTorch `DataLoader` 상속, `batch_size=1`, `collate_fn=collator`) | `__iter__() -> Iterator[GenerationRequest]` | `data/loaders/loader.py:14-19`, `data/loaders/torch.py:177-217` |
| (f) Scheduler 입력 단위 | `DatasetIterT[GenerationRequest] = Iterable[Iterable[GenerationRequest]]` | 외부 = conversations, 내부 = turns | `scheduler/schemas.py:64-70` |
| (g) **요청 1건 본체** | **`GenerationRequest`** (pydantic) | `request_id: str`(uuid4 자동), `columns: dict[str, list[Any]]`, `expects_tool_call: bool`, `input_metrics: UsageMetrics`, `output_metrics: UsageMetrics` | `schemas/request.py:212-254` |
| (h) **요청 1건 메타데이터/타이밍** | **`RequestInfo`** (pydantic) | `request_id`, `conversation_id`, `turn_index`, `status` ∈ {queued, pending, in_progress, first_token, completed, errored, cancelled}, `scheduler_node_id`, `scheduler_process_id`, `scheduler_start_time`, **`timings: RequestTimings`**, `error`, `traceback` | `schemas/info.py:107-204` |
| (i) **요청 1건 타이밍 컨테이너** | **`RequestTimings`** (pydantic dict) | `targeted_start`, `queued`, `dequeued`, `scheduled_at`, `resolve_start`, `request_start`, `first_request_iteration`, `first_token_iteration`, `last_token_iteration`, `last_request_iteration`, `request_iterations`, `token_iterations`, `request_end`, `resolve_end`, `finalized` | `schemas/info.py:22-85` |
| (j) 워커로 전달되는 메시지 단위 | `ConversationT[GenerationRequest] = list[tuple[GenerationRequest, RequestInfo]]` | turn 단위 (request, request_info) 페어 리스트 | `scheduler/schemas.py:50-54` |
| (k) backend.resolve **입력** | `(request: GenerationRequest, request_info: RequestInfo, history: HistoryT \| None)` | `HistoryT = list[tuple[GenerationRequest, GenerationResponse \| None]]` | `scheduler/schemas.py:56-61, 141-156`; 호출부 `scheduler/worker.py:397-399` |
| (l) backend.resolve **yield 단위** | `tuple[GenerationResponse \| None, RequestInfo]` | response는 진행 중에 None일 수 있음(첫 토큰 도착 신호 등); RequestInfo는 backend가 timings 갱신해 다시 반환 | `scheduler/schemas.py:141-156`, `backends/openai/http.py:329` |
| (m) Worker→메인 IPC 단위 | `tuple[GenerationResponse \| None, GenerationRequest, RequestInfo]` | `_send_update`에서 `messaging.put_sync((response, request, request_info), ...)` | `scheduler/worker.py:536-539` |
| (n) Scheduler.run **yield 단위** | `tuple[GenerationResponse \| None, GenerationRequest, RequestInfo, SchedulerState]` | 메인이 받은 워커 업데이트에 `received_callback`이 현재 SchedulerState를 덧붙임 | `scheduler/scheduler.py:68-75, 131-140`; 콜백 `worker_group.py:618-630` |
| (o) Benchmarker.run **yield 단위** | `BenchmarkT`(여기서는 `GenerativeBenchmark`) | strategy 1회분 모두 모은 컴파일 결과 | `benchmark/benchmarker.py:57-72, 161-169` |

> **"요청 1건"의 핵심**: (g)`GenerationRequest` + (h)`RequestInfo`(+(i)`RequestTimings`)가 한 쌍으로 묶여 (j) → (k) → (l) → (m) → (n)을 거쳐 시스템 전체를 통과한다. 이 두 객체는 **stage마다 (특히 RequestInfo의 timings/status가) 갱신되며** 마지막 (n)에서 accumulator가 둘 다 읽어 메트릭을 적재한다.

## 3. `backend.resolve` 호출 인자 — 정확한 모양

**호출 위치**: `src/guidellm/scheduler/worker.py:397-399`

```python
async for resp, info in self.backend.resolve(  # type: ignore[attr-defined]
    request,            # arg 1: GenerationRequest
    request_info,       # arg 2: RequestInfo  ← 측정용 타임스탬프 컨테이너 RequestTimings를 포함
    history or None,    # arg 3: list[tuple[GenerationRequest, GenerationResponse | None]] | None
):
    request_info = info
    if resp is None and request_info.timings.first_token_iteration is not None:
        self._send_update("first_token", None, request, request_info)
    response = resp
```

대응 타입 시그니처: `scheduler/schemas.py:141-146`:

```python
async def resolve(
    self,
    request: RequestT,
    request_info: RequestInfo,
    history: HistoryT[RequestT, ResponseT] | None = None,
) -> AsyncIterator[tuple[ResponseT | None, RequestInfo]]: ...
```

OpenAIHTTPBackend 측의 동일 시그니처: `backends/openai/http.py:323-329` (동일 3-인자, 같은 yield 모양).

### 사용자 질문에 대한 직답: "요청 객체 + 무엇? 측정용 타임스탬프 컨테이너가 같이 넘어가나?"

✅ **그렇다.** 정확히 3개가 함께 넘어간다:

1. **`request: GenerationRequest`** — 요청 본체(컬럼 데이터, 토큰 메트릭 등)
2. **`request_info: RequestInfo`** — **측정용 타임스탬프 컨테이너 `RequestTimings`를 멤버로 포함**. backend는 처리 도중 이 객체의 `timings.request_start`, `timings.first_token_iteration`, `timings.last_token_iteration`, `timings.request_end`, `timings.token_iterations` 등을 채워 넣고 매 yield마다 (mutated) `RequestInfo`를 반환한다.
3. **`history: list[tuple[GenerationRequest, GenerationResponse | None]] | None`** — 멀티턴 대화 히스토리. 단일턴이면 `None`. OpenAIHTTPBackend는 docstring에 "(currently not supported)"라 적혀 있음 (`http.py:339`).

> 단, backend가 yield하는 `RequestInfo`는 매번 **새 객체**일 수도 있다. 워커 본문이 `request_info = info`로 항상 덮어쓰므로 (worker.py:400), backend가 in-place 변경하든 신규 객체를 만들든 후속 코드는 동작한다. **인스턴스 동일성에 의존하지 말 것**.

## 4. 2단계 호출 체인 추정 vs. 실측 — 차이 요약

| 항목 | 2단계 추정 | 실측 결과 | 판정 |
|---|---|---|---|
| `cli/run.py → benchmark_generative_text → resolve_backend/processor/request_loader/profile/output_formats → Benchmarker.run → Scheduler.run → WorkerProcessGroup → WorkerProcess` | (상동) | 동일 | ✅ 확인됨 |
| `resolve_processor` 안에서 `AutoTokenizer.from_pretrained` 호출 | 호출 | **실제는 호출 없음**(식별자 정규화만). 호출은 후속의 `ProcessorFactory` 안에서 lazy | ⚠️ **수정**. 단 외부 통신 결과는 변함없음(어딘가에서는 결국 from_pretrained가 도는 것은 동일) |
| `Scheduler.run`이 yield하는 단위 | 명시 안 함 | `(GenerationResponse \| None, GenerationRequest, RequestInfo, SchedulerState)` 4-tuple | ➕ **추가** |
| `backend.resolve` 인자 | "request 객체" | `(request, request_info, history\|None)` — **request_info는 RequestTimings를 동반** | ➕ **추가/구체화** |
| Worker→메인 IPC 메시지 | 명시 안 함 | `(response\|None, request, request_info)` 3-tuple. 메인이 SchedulerState를 덧붙여 4-tuple로 만듦 | ➕ **추가** |
| 워커가 받는 입력 단위 | 단일 요청으로 가정 | `ConversationT = list[(GenerationRequest, RequestInfo)]` — **conversation(=turns)** 단위 | ⚠️ **수정**. 단일턴 데이터에서는 길이 1 리스트라서 결과는 같지만 멀티턴이면 차이가 큼 |
| `RequestInfo`가 생성되는 위치 | (암묵적으로 워커?) | **메인 프로세스 `WorkerGroupState._turn_iter`** (worker_group.py:574-581) | ⚠️ **명확화** |
| 워커별 동시 요청 한도 | 명시 안 함 | `async_limit = max_conc // num_processes` (+ remainder), 워커 내부 `asyncio.Semaphore(async_limit)`로 강제 | ➕ **추가** |
| 백엔드가 워커 프로세스에 어떻게 들어가나 | 명시 안 함 | `mp_context.Process(target=worker.run)` 시 **백엔드 인스턴스가 pickle을 거쳐 워커로 복제**됨. 그래서 httpx 클라이언트는 워커별 `process_startup`에서 재생성 | ➕ **추가** |
| `Environment.sync_*` 계층 | 명시 안 함 | Scheduler.run이 시작/매 이벤트/종료에 `env.sync_run_params / sync_run_start / update_run_iteration / sync_run_end`를 호출 — 멀티노드 동기화 훅 | ➕ **추가**(단일 노드에서는 대부분 no-op으로 추정, 다음 단계 확인 대상) |
| `profile.strategies_generator()`의 동작 | "여러 strategy를 생성" | **`generator.send(benchmark)`** 기반으로 직전 결과를 다음 strategy에 피드백 (sweep 등) | ➕ **명확화** |

## 5. 본 단계에서 의도적으로 확인하지 않은 것

다음 단계로 넘기는 항목:

- `backends/openai/http.py:_resolve_non_streaming` / `_resolve_streaming` 본문 — 즉 `httpx.AsyncClient.request` / `.stream`을 실제로 어떻게 호출하고, SSE 청크에서 어떻게 timings를 채우는지.
- `OpenAIRequestHandler.format(...)` 가 `GenerationRequest.columns`로부터 어떻게 OpenAI HTTP body를 만드는지.
- `DataEntrypointArgs` / `DataLoaderArgs` 의 정확한 필드.
- `DatasetsIterator.__iter__` 안의 deserializer → preprocessor → finalizer 실제 흐름.
- `SchedulerState`, `WorkerGroupState._locked_update`, `received_callback`의 상세 로직.
- `profile.strategies_generator()` 의 sweep/concurrent 등 모드별 로직.
- `NonDistributedEnvironment.sync_*` 메서드의 실제 본문(단일 노드에서 정말 no-op인지).

위 항목은 모두 **다음 단계(프롬프트 3: HTTP 호출 현미경 / 데이터 파이프라인 내부)** 에서 다룬다.
