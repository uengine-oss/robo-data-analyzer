# Analyzer 제품 CLI

공식 단발·일괄 분석 진입점은 `analyze_data_dir.py` 하나입니다.

```powershell
.\.venv\Scripts\python.exe cli\analyze_data_dir.py --data-dir D:\path\to\data --database test
```

API와 같은 분석 파이프라인을 사용하며, 실행·실험 산출물은 제품 저장소 밖으로 지정해야 합니다.
