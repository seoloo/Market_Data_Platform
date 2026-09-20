"""순수 도메인 계층.

**I/O 금지**: 네트워크, DB, 파일, 시스템 시계, 시드 없는 난수에 접근하지 않는다.
이 규칙은 CI 의 import-linter 와 tests/architecture 가 강제한다 (AGENTS.md §3.1).
"""
