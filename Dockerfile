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

COPY . .

EXPOSE 5502

# 게이트웨이 docker 라우팅(robo-analyzer:5502)과 일치.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5502"]
