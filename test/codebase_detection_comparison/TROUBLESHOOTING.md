# Deep Agent 무한 대기 (Hang) 이슈

## 증상

특정 프로젝트에서 분석이 **영원히 끝나지 않음**. 프로세스는 살아있고 에러도 없음.
LLM POST 호출 1~3 회 후 새 호출이 멈춤.

## 원인

LLM 이 **도구 호출을 자연어 텍스트로 서술**해서 응답함.

정상:

```
tool_calls: [{name: "classify_file", args: {...}}]
```

문제:

```
content: [{type: "text", text: 'classify_file("x.xml", "SQL")\n...'}]
```

Deep Agent 는 `tool_call` 타입만 실행 대상으로 인식. 텍스트로 "묘사" 된 도구 호출은:

- 도구 실행 안 됨
- 다음 LLM 턴도 트리거 안 됨
- → **무한 대기**

`gpt-5.4` + `reasoning_effort=none` 조합이 사내/비공개 프레임워크 · 한국어 혼재 ·
대용량 XML 미리보기에서 종종 tool_call 대신 텍스트를 반환.

## 해결

**SYSTEM_PROMPT 첫머리에 형식 강제**:

```
## 응답 규칙 (무한 hang 방지)
- 탐색 중: 반드시 tool_call (도구 호출 API) 로만. 도구명·인자를 텍스트로 적지 말 것.
- 최종 결과만 tool_call 없는 텍스트 메시지로 출력.
```

탐색 중에는 도구 호출 아니면 모두 금지. 최종 요약만 텍스트 허용.

## 진단법

hang 의심되면 `get_llm()` 으로 Deep Agent 밖에서 같은 입력을 직접 호출. 응답의
`'type': 'text'` 안에 도구 함수명 문자열이 보이면 본 이슈 확정.
