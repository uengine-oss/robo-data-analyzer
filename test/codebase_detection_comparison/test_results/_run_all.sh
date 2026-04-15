#!/usr/bin/env bash
# 테스트 데이터 6 개 순차 실행. --no-cache, 상세 로그 파일별 저장.
set -u
cd "$(dirname "$0")/../../.."

RESULTS="test/codebase_detection_comparison/test_results"
TEST_ROOT="../테스트데이터"

TARGETS=(
  "apascrt00010t02_20260406"
  "library_system"
  "loan-ejb-app"
  "ofbiz-order-standalone"
  "swing_system"
  "ukesa"
)

OVERALL_START=$(date +%s)

for name in "${TARGETS[@]}"; do
  target="$TEST_ROOT/$name"
  log_file="$RESULTS/${name}.log"

  echo ""
  echo "=== $name 시작 $(date '+%H:%M:%S') ==="
  START=$(date +%s)
  python -m test.codebase_detection_comparison.analyze "$target" \
    --timeout 600 --verbose > "$log_file" 2>&1
  END=$(date +%s)
  ELAPSED=$((END - START))
  echo "=== $name 끝 $(date '+%H:%M:%S') (${ELAPSED}s) ==="
done

OVERALL_END=$(date +%s)
TOTAL=$((OVERALL_END - OVERALL_START))
echo ""
echo "=== 전체 완료 / 총 ${TOTAL}s ==="
