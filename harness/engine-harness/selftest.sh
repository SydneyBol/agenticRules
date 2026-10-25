#!/usr/bin/env bash
# Runs the bundled self-test cases against the real engine. Usage: ./selftest.sh  (after `mvn package`)
set -u
cd "$(dirname "$0")"
JAR=target/harness.jar
[ -f "$JAR" ] || { echo "build first: mvn -q package -DskipTests"; exit 1; }
fail=0
run() { # name expected_exit
  local out code
  out=$(java -jar "$JAR" "cases/$1.json" 2>"target/$1.stderr.log"); code=$?
  echo "$out" > "target/$1.out.json"
  if [ "$code" -eq "$2" ]; then echo "PASS  $1 (exit $code)"; else echo "FAIL  $1 (exit $code, expected $2) -> target/$1.out.json"; fail=1; fi
}
run spend_based_rewards 0
run visit_counter 0
run progressive_spend_bonus 0
run compile_error 3
exit $fail
