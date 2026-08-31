# Analyzer 제품 CLI

공식 단발·일괄 분석 진입점은 `analyze_data_dir.py`입니다.

```powershell
.\.venv\Scripts\python.exe cli\analyze_data_dir.py --data-dir D:\path\to\data --database test
```

API와 같은 분석 파이프라인을 사용하며, 실행·실험 산출물은 제품 저장소 밖으로 지정해야 합니다.

`seed_recorded_responses.py`는 실패 run에 남은 exact prompt evidence를 content hash로
검증해 봉인 response cache로 가져오는 복구 전용 CLI입니다. 이후 실행은
`ROBO_LLM_RESPONSE_CACHE_REQUIRE_HIT=true`로 fail closed해야 하며, 한 건이라도 exact
cache가 없으면 새 provider 호출을 하지 않습니다.
