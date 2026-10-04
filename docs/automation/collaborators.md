# 협업자의 자동 리뷰·병합 요청

현재는 Phase 9 검증 준비 단계이며 **자동 병합은 꺼져 있다**.
활성화와 실제 검증 결과는 [구축 상태](setup-status.md)를 따른다.

허용 사용자: `ckrhehfl`, `ljwoo8942`, `chika-breeki`, `ddoss2414-max`.
계정 이름과 GitHub 계정 ID, 현재 저장소 쓰기 권한을 모두 검사한다.
사용자 추가는 별도 소유자 승인과 정책 변경이 필요하다. 관리자 권한은 필요 없다.

## 요청 방법

1. 이 저장소에 feature branch로 PR을 만들고 draft를 해제한다. main을 대상으로 한다.
2. GitHub **Actions → Automation Request → Run workflow**를 선택한다.
3. branch는 **main**, PR 번호를 입력하고 실행한다.
4. 접수 완료 후 현재 소유자 PC의 실행기가 CI → 독립 리뷰 → 필요한 수정 → 새 CI/리뷰 →
   Decision Gate를 처리한다. 자동 병합이 활성화된 경우 모든 조건이 충족되어야 병합한다.

접수 workflow 성공은 접수 완료이며 리뷰나 병합 성공을 뜻하지 않는다.
PR의 `software-checks`, `codex-review`, `decision-gate` 결과를 확인한다.
리뷰 workflow artifact에는 전체 findings가 있고 Gate 실행 요약에는 판정과 미검증 항목이 있다.
요청 후 사람이 PR 커밋을 변경하면 기존 요청은 거부한다. 최신 커밋으로 새 요청을 만든다.
기존 run의 **Re-run jobs**는 허용하지 않는다. 중단·실패한 실행을 무한 반복하지 않는다.
접수 artifact 보존은 7일이다. 만료 후에는 새 요청이 필요하다.

## 실행 조건과 중단

소유자 PC와 Codex가 동작하고 GitHub/ChatGPT 로그인이 유효해야 한다.
주기 접수 확인의 실제 활성화 여부는 구축 상태에 기록한다. PC가 꺼져 있으면 요청이 대기한다.
GitHub Actions에는 ChatGPT 로그인 파일·API Key를 넣지 않는다. 협업자와 인증을 공유하지 않는다.
소유자의 기존 구독 사용 한도가 적용된다.

독립 Reviewer는 읽기 전용이다. Fixer는 finding만 수정하며 **PR당 총 2회**까지다.
요청자가 달라지거나 실패/새 커밋이 생겨도 사용한 수정 횟수를 되돌리지 않는다.
CI 실패는 Builder의 기술 수정 대상으로 처리하되 이 경로로 Fixer 한도를 우회하지 않는다.
사람 판단·실물 증거가 필요한 상태는 병합하지 않고 관찰 방법과 기대 결과를 알린다.
실행 중 오류/시간 초과/병합 응답 불명확 시 해당 요청을 소비된 상태로 보존한다.
실행기가 실제 PR 상태를 조사한 뒤 새 요청으로 재개하며, 병합을 맹목적으로 재시도하지 않는다.
소스코드 리뷰를 사람에게 떠넘기지 않는다.

## 운영자 검증

승인된 깨끗한 최신 main에서 `python -m tools.automation_worker --once`로 대기 요청 한 건을
처리한다. 특정 접수 run은 `--request-run <ID>`를 사용한다. 여러 실행은 OS 잠금으로 직렬화한다.
고정된 소유자 계정만 이 실행기를 실행한다. 개인 PC의 소유자 인증/관리자 권한을 신뢰하는 구조이며
악의적인 관리자나 저장소 쓰기 사용자의 모든 수동 변경을 막는 인증 체계라고 주장하지 않는다.
로컬 `requests.sqlite3`와 기존 `fix-attempts.sqlite3`를 삭제·초기화·다른 PC로 분산하지 않는다.
결과는 `artifacts/automation-requests/`에 보존하며 새 외부 업로드를 하지 않는다.

요청 workflow는 읽기 권한뿐이다. Gate는 소유자만 게시하고 별도 job의 `statuses: write`만 사용한다.
Actions에 repository contents write를 부여하지 않는다. 실제 병합은 기존 소유자 로컬 인증으로
보호된 PR API에 현재 SHA를 지정해 한 번 요청한다. `--admin`이나 main 직접 push를 사용하지 않는다.
GitHub의 지연 실행형 native auto-merge 설정은 사용하지 않는다.

공식 참고: [수동 workflow 실행](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
[PR 병합 API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request).
