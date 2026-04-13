# detect_unknown

**규칙 없음**. 네거티브 매칭 전담 Skill.

이 Skill 은 `rules/` 폴더를 비워둠. `skill_loader` 는 이 폴더를 언어 식별자 없이도 로드함.

## 역할

- 어떤 언어로도 특정되지 않는 후보를 담기 위한 placeholder.
- `negative_matcher.detect_unknown_namespaces()` 가 실제 탐지를 수행.
- `known_proprietary.json` 의 힌트 맵으로 독점 프레임워크 추정.
