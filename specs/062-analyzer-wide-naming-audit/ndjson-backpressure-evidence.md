# NDJSON 대형 그래프 백프레셔 검증

## 결함과 기준선

기존 `NDJSONStream`은 무제한 `asyncio.Queue`에 모든 이벤트를 즉시 적재했다. logger mirror를
제외하고 소비자를 붙이지 않은 합성 `node_event` burst를 `tracemalloc`로 측정한 결과는 다음과 같다.

| 이벤트 수 | retained memory | 이벤트당 평균 |
|---:|---:|---:|
| 14,128 | 13.21 MiB | 약 981 bytes |
| 50,000 | 46.85 MiB | 약 983 bytes |
| 100,000 | 93.76 MiB | 약 983 bytes |

따라서 그래프 크기에 비례해 큐 메모리가 선형 증가하는 결함이 실제로 존재했다.

## 채택한 계약

- 대량 그래프 생산자는 `await stream.emit_async(event)`만 사용한다.
- high-water mark는 1,024건이다. 기준선의 이벤트당 retained memory로 환산하면 약 1 MiB 분량이다.
- 큐가 1,024건에 도달하면 생산자가 소비자의 drain을 기다린다. 이벤트를 버리거나 순서를 바꾸지 않는다.
- 동기 `emit`은 Agent middleware와 제어 이벤트의 호출 계약을 보존한다. 물리 큐를 bounded queue로
  바꾸어 `QueueFull`에서 이 이벤트를 버리는 방식은 채택하지 않았다.
- 응답 iterator가 먼저 닫히면 producer를 취소·수거한다. producer의 `finally: await close()`는 소비자가
  사라진 큐의 drain을 기다리지 않으므로 연결 종료 시 교착하지 않는다.

## 검증 결과

100,000건을 생산자와 소비자로 동시에 흘리고 각 sequence를 전량 대조했다.

```text
sent=100000
received=100000
peak_queue=1024
peak_memory=0.27 MiB
current_memory=0.03 MiB
elapsed=9.50 s
```

단위 회귀는 다음 반대 사례를 고정한다.

- 소비자가 없으면 2,048건 생산자가 정확히 1,024건에서 대기한다.
- 소비가 시작되면 2,048건과 `stream_end`가 원래 순서로 전량 전달된다.
- 큐가 가득 찬 동안 응답 소비자가 이탈해도 producer cancellation과 `close()`가 1초 안에 끝난다.
- 분석·검색 라우터 모두 `iter_with_producer(producer)` 수명주기 경계를 사용한다.

검증 명령:

```text
python -m unittest tests.runtime_events.test_ndjson_event_stream tests.pipeline.execution.test_graph_delta_contract tests.graph.test_calls -v
python -m compileall -q runtime_events graph pipeline api tests
```

결과: 국소 17/17, 전체 142/142, 구조 계약 16/16, prompt byte-identical 26/26,
compile과 `git diff --check` PASS.

## 범위와 남은 위험

이번 경계는 실제 대량 burst 생산자인 그래프 node/relationship/CALLS 직렬화 전체에 적용한다. 동기
middleware·제어 이벤트는 기존 무손실 호출 계약 때문에 물리 큐의 hard cap 대상이 아니다. 앞으로 새로운
대량 이벤트 생산자를 추가할 때 동기 `emit`을 사용하면 이 보장이 성립하지 않으므로, 구조 테스트와 코드
리뷰에서 반드시 `emit_async` 사용 여부를 확인해야 한다.
