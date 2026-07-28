# ROBO Data Analyzer — LangGraph 분석 파이프라인 (FastAPI / uvicorn :5502)
#
# 입구 통일(spec 006/014): antlr 가 채운 공유 data/ 를 읽어 분석한다.
#   - data/ 는 antlr 컨테이너와 **공유 볼륨**으로 마운트해야 한다(antlr write → analyzer read).
#   - leidenalg/igraph(Leiden 커뮤니티 탐지)는 C 확장이라 build-essential 필요.
#   - 비밀값(ROBO_LLM_API_KEY 등)은 런타임 env/Secret 으로 주입(이미지에 넣지 않는다).
FROM python:3.12-slim

WORKDIR /app

# C 확장(leidenalg/igraph) 빌드용 — 설치 후 캐시 정리.
# 의존성 먼저(레이어 캐시).
COPY requirements.txt .
# The default Linux Torch wheel bundles CUDA libraries that this CPU-only
# desktop stack cannot use. Pin the official CPU wheel first so
# sentence-transformers reuses it instead of adding several gigabytes.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.13.0+cpu" \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5502

# 게이트웨이 docker 라우팅(robo-analyzer:5502)과 일치.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5502"]
