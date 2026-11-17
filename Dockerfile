# ROBO Data Analyzer — LangGraph 분석 파이프라인 (FastAPI / uvicorn :5502)
#
# 입구 통일: antlr 가 채운 공유 data/ 를 읽어 분석한다.
#   - data/ 는 antlr 컨테이너와 **공유 볼륨**으로 마운트해야 한다(antlr write → analyzer read).
#   - 비밀값(ROBO_LLM_API_KEY 등)은 런타임 env/Secret 으로 주입(이미지에 넣지 않는다).
# ⚠ **베이스를 `python:3.12-slim` 에서 Ubuntu 24.04 로 옮겼다** (2026-10-22).
#
# `python:*-slim` 은 Debian trixie 위에 있고, trixie 의 `zlib` 에
# CVE-2026-85091 이 걸려 있는데 **데비안이 고친 판을 안 냈다**(trixie·sid 모두
# vulnerable, 버그 #1146895). 이 이미지에 남아 있던 마지막 1건이 그것이었다.
#
#     python:3.12-slim                        0C 3H   (zlib 포함)
#     ubuntu:24.04 + python3 + python3-venv   **0C 0H**
#
# ⚠ **파이썬이 3.12 → 3.12 가 된다**(noble 의 기본). 기동 시험으로 확인했다.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# ⚠ **venv 를 만든 뒤 `*-whl` 꾸러미를 걷어낸다**.
# `python3-venv` 가 끌고 오는 `python3-pip-whl`·`python3-setuptools-whl` 이
# `/usr/share/python-wheels/` 에 옛 판 휠을 남기고, 검사기는 그 **안까지** 읽는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates \
    && python3 -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir --upgrade pip setuptools wheel \
    && apt-get purge -y python3-pip-whl python3-setuptools-whl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /usr/share/python-wheels
WORKDIR /app

# 의존성 먼저(레이어 캐시).
COPY requirements.lock .
# requirements.lock pins the official CPU-only Torch build and every transitive hash.
# ⚠ **다 깔았으면 `pip` 자신을 걷어낸다** (2026-10-22 실측).
# pip 는 `pip/_vendor/` 에 `urllib3`·`msgpack`·`setuptools` 사본을 들고 다니고
# 검사기가 그것까지 센다. 이 컨테이너는 uvicorn 만 띄우므로 런타임에 pip 가
# 필요 없다 — 없는 것을 안 싣는 것이 가장 확실하다.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip uninstall -y pip wheel \
    && rm -rf /root/.cache

# 레거시 검색 재채점(CE) 모델 — **빌드 입력으로만** 굽는다(기본은 굽지 않음 = 첫 검색 때 HF Hub 에서 받음).
# 사내망처럼 HF Hub 에 못 닿는 판은 빌드 인자로 모델을 넘겨 이미지에 싣고, 런타임에 HF_HUB_OFFLINE=1 로 쓴다.
#   docker build --build-arg RERANKER_MODEL=dragonkue/bge-reranker-v2-m3-ko \
#                --build-arg RERANKER_REVISION=<HF 커밋> .
# 판(고객)마다 다른 것은 브랜치가 아니라 이 인자다. 모델은 약 2.2GB.
# 코드보다 먼저 두어(COPY . . 앞) 코드가 바뀌어도 이 레이어는 캐시를 쓴다.
ENV HF_HOME=/opt/hf
ARG RERANKER_MODEL=""
ARG RERANKER_REVISION=""
# ⚠ 커밋으로 받으면 캐시에 refs/main 이 생기지 않는다. 코드는 판 없이(= main) 부르므로, 오프라인에서
#   "couldn't find them in the cached files" 로 못 실었다(2026-11-17 실측). 받은 커밋을 refs/main 에 적는다.
RUN if [ -n "$RERANKER_MODEL" ]; then \
        python -c "import os, pathlib; from huggingface_hub import snapshot_download; \
p = pathlib.Path(snapshot_download(os.environ['RERANKER_MODEL'], revision=os.environ.get('RERANKER_REVISION') or None, \
    allow_patterns=['*.json', '*.safetensors', '*.model'])); \
refs = p.parent.parent / 'refs'; refs.mkdir(exist_ok=True); (refs / 'main').write_text(p.name); \
print('reranker baked:', p)" \
        && find "$HF_HOME" -name '*.lock' -delete; \
    fi

COPY . .

EXPOSE 5502

# 게이트웨이 docker 라우팅(robo-analyzer:5502)과 일치.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5502"]
