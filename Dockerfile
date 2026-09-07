# ROBO Data Analyzer — LangGraph 분석 파이프라인 (FastAPI / uvicorn :5502)
#
# 입구 통일: antlr 가 채운 공유 data/ 를 읽어 분석한다.
#   - data/ 는 antlr 컨테이너와 **공유 볼륨**으로 마운트해야 한다(antlr write → analyzer read).
#   - 비밀값(ROBO_LLM_API_KEY 등)은 런타임 env/Secret 으로 주입(이미지에 넣지 않는다).
FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저(레이어 캐시).
COPY requirements.lock .
# requirements.lock pins the official CPU-only Torch build and every transitive hash.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

EXPOSE 5502

# 게이트웨이 docker 라우팅(robo-analyzer:5502)과 일치.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5502"]
