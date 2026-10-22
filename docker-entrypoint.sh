#!/bin/sh
# 컨테이너 안에서 `localhost` 는 **컨테이너 자신**이다.
#
# `llm_configs/*.yaml` 의 `api_base` 는 호스트 프로세스로 돌던 시절의 값이라 자체 호스팅
# 모델을 `http://127.0.0.1:11434/v1` 처럼 가리킨다. 그대로 두면 컨테이너가 자기 자신의
# 11434 포트에 붙으려다 실패한다 — 기동은 성공하므로 증상은 「분석 시작」을 누른 뒤에야
# 연결 오류로 나타난다.
#
# 이 저장소에는 같은 문제의 선례가 있다: data-fabric 이 데이터소스 접속정보의 localhost 를
# `MINDSDB_REPLACE_LOCALHOST` 값으로 치환한다(compose.yml 머리말). 같은 방식을 쓴다.
#
# 서브모듈의 yaml 을 고치지 않고 **기동 시점에** 바꾼다. 파일을 고쳐 두면 호스트 프로세스로
# 돌릴 때(개발 체크아웃·스킬 기반 분석) 반대로 깨진다 — 같은 파일이 두 실행 형태를 모두
# 만족해야 하므로, 차이는 실행 시점에 흡수하는 것이 맞다.
#
# 끄려면 ROBO_REPLACE_LOCALHOST 를 빈 값으로 준다.
set -e

TARGET="${ROBO_REPLACE_LOCALHOST-host.containers.internal}"

if [ -n "$TARGET" ] && [ -d /app/llm_configs ]; then
  for f in /app/llm_configs/*.yaml; do
    [ -f "$f" ] || continue
    # `api_base` 줄에만 적용한다. 주석이나 모델 이름에 들어 있는 문자열까지 바꾸면
    # 설정의 의미가 달라진다.
    sed -i "/^[[:space:]]*api_base:/ s|//127\.0\.0\.1:|//$TARGET:|g; \
            /^[[:space:]]*api_base:/ s|//localhost:|//$TARGET:|g" "$f"
  done
fi

exec "$@"
