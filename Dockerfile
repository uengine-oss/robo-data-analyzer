# ROBO 분석기 — 컨테이너 이미지.
#
# ## 왜 컨테이너인가
#
# 호스트 프로세스로 돌리던 것을 옮겼다. 이유는 의존성이다 — 번들 런타임을 만들 때마다
# 빌드 머신에서 의존성을 다시 풀어야 했고, `requirements.txt` 가 **실제로 돌아가는 조합과
# 달라서** 그 해결이 실패했다(2026-08-09 실측: `langchain-openai==0.3.45` 는 존재하지
# 않는 버전인데 실제 venv 에는 1.3.5 가 깔려 있었다). 이미지에 구워 두면 그 단계가 사라진다.
#
# ## 파일 접근은 바인드 마운트로 이어진다
#
# 분석기는 ANTLR 파서가 떨군 AST JSON 을 **파일시스템으로** 받는다(HTTP 아님).
# 그래서 파서와 같은 디렉터리(`<userData>/robo-data`)를 마운트한다. 사용자가 고른 임의
# 폴더는 컨테이너가 볼 필요가 없다 — 메인 프로세스가 읽어서 파서에 HTTP 로 반입하고,
# 파서가 공유 디렉터리에 쓴다.
#
# ## 의존성은 `requirements.lock.txt` 를 쓴다
#
# `requirements.txt` 가 아니다. 그 파일은 실제 환경과 갈라진 지 오래고, 그대로 설치하면
# 이미지가 만들어지지 않는다. lock 은 **돌아가는 venv 에서 뽑은 것**이라 재현이 보장된다.
FROM python:3.12-slim

# 빌드 도구는 일부 휠이 소스 배포만 제공할 때 필요하다. 설치 뒤 지워 이미지에 남기지 않는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성을 먼저 넣어 레이어 캐시를 살린다 — 소스만 바뀌면 재설치하지 않는다.
COPY requirements.lock.txt /app/requirements.lock.txt

# torch 는 **CPU 전용 휠**로 먼저 넣는다. lock 을 그냥 설치하면 리눅스 기본 휠이 딸려 오는
# CUDA 런타임까지 받아 온다 — 2026-08-09 실측으로 nvidia/ 2.9GB · triton/ 651MB · torch/
# 911MB, 합쳐서 이미지의 4.5GB 였다. 컨테이너에는 GPU 가 없으니 전부 쓰이지 않는 무게고,
# 설치본에는 `podman save` 산출물로 그대로 들어간다.
#
# 순서가 중요하다. 먼저 깔아 두면 다음 줄의 lock 설치가 같은 버전을 이미 충족된 것으로
# 보고 건드리지 않는다. (torch 를 쓰는 것은 sentence-transformers 의 로컬 임베딩 경로다.)
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
      "torch==$(grep -i '^torch==' /app/requirements.lock.txt | cut -d= -f3)"

RUN pip install --no-cache-dir -r /app/requirements.lock.txt \
 && apt-get purge -y build-essential && apt-get autoremove -y

COPY . /app

# 마운트 지점을 **실재하는 빈 디렉터리**로 만든다. 소스 트리의 `data` 는 호스트 절대경로를
# 가리키는 심볼릭 링크라 이미지 안에서 깨지고, 그 위에 바인드 마운트를 걸면 컨테이너가
# 뜨지 않는다 — `.dockerignore` 에서 빼는 것과 이 한 줄이 한 쌍이다.
RUN rm -rf /app/data && mkdir -p /app/data

# 파서와 공유하는 작업 디렉터리. compose 가 호스트의 `<userData>/robo-data` 를 여기 붙인다.
ENV ROBO_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 5502

# 자체 호스팅 모델(ollama 등)의 `api_base` 가 `localhost` 를 가리키는 문제를 기동 시점에
# 흡수한다. 근거는 스크립트 머리말. 서브모듈의 yaml 은 손대지 않는다.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# 헬스체크는 실재하는 엔드포인트를 본다. 없는 경로를 보면 컨테이너가 영원히 unhealthy 로
# 남아 기동 대기가 타임아웃한다(antlr 이미지에서 실제로 겪은 함정).
HEALTHCHECK --interval=5s --timeout=5s --retries=60 --start-period=20s \
  CMD curl --fail --silent http://127.0.0.1:5502/docs >/dev/null || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5502"]
