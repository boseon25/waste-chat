# 공식 경로 검증 및 갱신 방식

확인일: **2026-11-15 (Asia/Seoul)**. 공개 화면·공식 안내·접수 입구만 확인했으며 실제 민원/시험을 접수하지 않았습니다.

## 실제로 확인한 경로

### 국민신문고 — 기관 서면 질의 접수 통로

- 안내: https://www.epeople.go.kr/nep/pttn/pttnOnln/pttnOnlnContent.npaid
- 메뉴: 민원 → 민원상담 → 민원상담 신청
- 해당 안내 화면: https://www.epeople.go.kr/nep/pttn/pttnOnln/PttnOnlnWrtnInfo.npaid
- 그 화면의 민원신청 연결을 실제 클릭한 접수 입구: https://www.epeople.go.kr/nep/pttn/gnrlPttn/PttnRqstWrtnInfo.paid
- 확인 결과: 로그인/본인인증 경계로 이동. 로그인 뒤 실제 입력 화면·첨부·제출은 검증하지 않았습니다. 로그인 과정의 임시 세션 URL은 배포하지 않습니다.
- 공식 전체 절차: https://www.epeople.go.kr/nep/gdnc/PttnUtilGdncContent.npaid
- 공식 안내의 처리 단계: 신청서 작성 → 유사사례 검토 → 처리기관 선택·제출 → 담당부서 배정 → 처리 및 선택한 방식으로 결과 통보 → 만족도 조사.
- 소관 폐기물 담당기관은 현장·문의 내용에 따라 확인해야 합니다. 국민신문고 자체를 폐기물 분류 결정기관으로 설명하지 않습니다.

### KTR — 시험 접수 경로 예시

- 공식 안내: https://www.ktr.or.kr/customer-service/reception-request/contentsid/1964/index.do
- 메뉴: 시험접수 → 접수 · 의뢰안내 → 온라인 접수
- 공식 링크가 연결한 서비스: https://cs.ktr.or.kr/
- **실제 도착 결과: 온라인 상담 시스템 임시 점검 안내.** 정상 접수 가능한 것으로 표시하지 않습니다. 재개일을 추정하지 않습니다.
- 공식 택배 절차·신청서 안내: https://www.ktr.or.kr/customer-service/reception-request/contentsid/358/index.do
- 방문 접수 안내 링크: https://www.ktr.or.kr/customer-service/reception-request/contentsid/357/index.do (이번에는 링크를 확인했으며 방문 화면 전체를 별도로 시험하지 않음).
- 택배 안내에 실제 표시된 단계: 신청서 및 개인정보 동의서 준비 → 시료와 접수 → 기관이 의뢰시험신청서/계좌 안내 → 의뢰인이 확인·납부 → 시험 → 완료 통지 → 성적서 수령. 직접 수령·택배 및 사전 요청한 FAX/E-mail 수령 안내가 있습니다.
- 위 절차에 앞서 시스템은 시료 수용 여부·담당 지점·시험 항목·시험법·채취/운송 조건·비용/기간을 확인하도록 합니다. 이는 모든 위험 시료를 일반 택배로 보내도 된다는 뜻이 아닙니다.
- 기관 서식 다운로드 안내는 확인했으나 개별 공식 DOCX 서식 내부를 자동 작성한 것은 아닙니다. 제공하는 DOCX는 전사/검토용 준비 초안입니다.

### 국립환경과학원 — 기관·지정분야 확인 (접수 페이지가 아님)

- https://www.nier.go.kr/common/kor/content/comContsDetail.do?contsNo=191&menuNo=13004
- 메뉴: 법령정보 → 분석기관지정 → 검사기관 선택상자에서 **폐기물분석 전문기관** 선택.
- 이번에는 브라우저에서 실제로 선택하고 해당 표를 확인했습니다. 화면에 표시된 목록 갱신일은 **2026-07-22**였습니다.
- 표에서 KTR 영남은 일반분야, KTR 과천은 PCBs로 구분되어 있었습니다. 이 차이를 무시하고 모든 지점이 모든 시험을 한다고 표시하지 않습니다.
- 개별 지정기관의 현재 영업·시료 수용·세부 시험범위 및 다운로드 첨부 내부까지 대조한 것은 아닙니다.
- `evidence/`에 공개 화면 확인 이미지를 보관합니다.

## 경로 정보를 어떻게 가져오는가

1. 공식 사이트에서 메뉴·링크·절차를 찾아 브라우저로 접수 입구와 로그인/점검 경계까지 확인합니다.
2. 확인한 `안내 URL / 실제 접수 서비스 URL / 절차 URL / 메뉴 경로 / 준비자료 / 확인 범위`를 `contact_routes.py`에 구분해 저장합니다. 검색 결과만으로 접수 링크를 추정하지 않습니다.
3. `route_monitor.py check`는 **사전에 등록한 공식 호스트의 공개 페이지**만 읽습니다. 요청에는 폐기물 건 정보·연락처·문서·로그인 쿠키가 들어가지 않습니다. 리다이렉트도 HTTPS/승인 호스트/공개 주소만 허용합니다.
4. 안내 페이지에 실제 접수 링크가 남아 있는지, 접수 목적지의 로그인/점검 상태, 공개 절차의 지정 구간을 검사합니다. 원문 HTML 자체 대신 해시와 관련 절차 발췌를 기록하며 로그인 세션 파라미터는 보관하지 않습니다.
5. 경로 검사는 별도의 SQLite 이력 DB에 append-only로 저장합니다. 폐기물 DB와 섞지 않습니다. 승인 전 관찰과 검토한 기준을 구분하며, 사이트 변화가 있어도 카탈로그 URL이나 사용자 사실을 자동 변경하지 않습니다.

```bash
python route_monitor.py check --route all --registry output/my_route_checks.sqlite3
python route_monitor.py approve --registry output/my_route_checks.sqlite3 --check <최신검사번호> --reviewer "확인한 담당자"
python route_monitor.py check --route all --registry output/my_route_checks.sqlite3
python route_monitor.py show --route authority --registry output/my_route_checks.sqlite3
python phase3.py plan --db my_intake.sqlite3 --case "<건ID>" --purpose "분류 근거 확인" --registry output/my_route_checks.sqlite3
```

`approve`는 관찰 내용에 대한 검토 기록입니다. 점검 중인 관찰을 승인해도 서비스 정상/접수 가능으로 바뀌지 않습니다. 최신이 아닌 기록과 실패한 검사는 승인할 수 없습니다. 자동 접수 기능은 없습니다.

## 변화가 없다면?

- 등록한 링크·접수 경계·절차 구간이 검토 기준과 같으면 `unchanged` 기록과 확인 시각을 추가합니다.
- 동일 경로 버전을 유지하고 브라우저 확인일도 그대로 둡니다. HTTP 검사만으로 브라우저 전체 경로를 다시 클릭했다고 표시하지 않습니다.
- 사례를 열 때마다 검색/페이지 수집을 반복하지 않습니다. 기본은 저장한 카드/검사 이력을 읽습니다.
- 이번 실제 HTTP 재검사에서도 국민신문고는 **로그인 필요 상태가 그대로**, KTR은 **점검 중 상태가 그대로**였습니다. 무변경이 곧 정상 접수 가능을 의미하지 않습니다. 결과는 `route-live-040.jsonl`, `route-recheck-040.jsonl`, `output/routes_20261115.sqlite3`에 있습니다.

## 변화·실패·오래된 기록은?

- 링크/목적지 상태 변경: `route_changed_needs_review`.
- 감시 대상 절차 본문 변경: `procedure_changed_needs_review`.
- 타임아웃·오류·본문 누락·링크 미확인: 검증 실패/재확인 상태. **변화 없음으로 간주하지 않습니다.**
- 카탈로그 정의를 수정하면 이전 검사 캐시로 접수를 안내하지 않습니다.
- 확인 후 7일 경과 시 재확인 필요 상태가 됩니다. 과거 URL은 이력/후보로 보존하되 `usable_submission_url`은 비웁니다. 로그인 이후 미검증 화면이나 점검 중 경로도 정상 접수 링크로 활성화하지 않습니다.
- 변경 감지 후 담당자가 공식 화면과 대조해 카탈로그를 수정하고 다시 검사·검토합니다. 바뀐 주소를 모델이 임의 생성하여 자동 채택하지 않습니다.

## 자동화 범위의 한계

현재 자동 감시 대상은 **국민신문고와 KTR의 등록된 공개 접수 연결·상태·절차 구간**입니다. 모든 메뉴, 로그인 뒤 화면, NIER 지정현황 전체, 최신 법령·첨부파일·전화번호 전체의 변경을 자동 감시하지는 않습니다. NIER 기관/지정분야와 개별 시험 가능 항목은 의뢰 전 다시 확인해야 합니다.

주기 실행 명령은 제공했지만 예약 작업은 등록하지 않았습니다. 운영 시 주 1회 또는 실제 접수 준비 전 검사를 실행하고, 장애 시 수동 확인하는 방식을 권장합니다. 자동 검사만으로 공식 접수·발급 과정 전체를 보증하지 않습니다.
