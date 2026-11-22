# Preliminary 분류·Gate 수정 모듈

## 포함 파일
- `models.py`: 분류 결과의 계층/말단/지정폐기물 메타데이터 추가
- `waste_classifier.py`: 4자리 폐기물코드와 공식 분류번호 자동 구분, 계층/명칭/말단 검증
- `hazardous_checker.py`: 공식 분류표와 지정폐기물 목록 교차 검증
- `restriction_checker.py`: 기존 제한 규칙 처리 로직 유지
- `preliminary_integrator.py`: 분류 미확정 시 지정폐기물/제한 규칙 검사를 중단하고 Gate 분기

## 적용 방법
기존 프로젝트의 preliminary 패키지에서 위 5개 파일을 교체하십시오.
기존 `mixture_checker.py`는 변경하지 않습니다.

`PreliminaryIntegrator`의 데이터 디렉터리에는 다음 파일이 있어야 합니다.
- `waste_codes_4.csv`
- `waste_code_crosswalk.csv`
- `hazardous_codes.csv`
- `prohibited_restricted.csv`

## 분류 상태
- `MATCHED`: 공식 분류표에서 `말단=Y`인 코드 확인
- `PARENT_CLASS`: 대분류/중분류 또는 Crosswalk 연결 그룹. 세부 말단 코드 추가 확인 필요
- `MISMATCH`: 입력 명칭과 공식/Crosswalk 명칭 불일치
- `NEEDS_INFO`: 분류번호 미입력
- `NOT_FOUND`: 분류표 및 Crosswalk에 코드 없음
- `REVIEW`: 잘못된 코드 형식 등 데이터 검토 필요

## 데이터 해석상 주의
- 4자리 `폐기물코드`는 공식 분류번호로 정규화하지 않고 Crosswalk에서 조회합니다.
- Crosswalk의 `분류번호_중분류`가 공식 분류표에 없는 경우가 실제로 존재하므로, 해당 행의 부재만으로 오류 처리하지 않습니다.
- Crosswalk의 말단 코드는 직속 하위 코드일 필요가 없습니다. 말단 코드의 존재 여부, `말단=Y`, 연결 접두어, `말단_수`를 검증합니다.
- 공식 말단 여부는 `단계`가 아니라 `말단` 컬럼으로 판단합니다. `단일` + `말단=Y`도 유효합니다.
- `hazardous_codes.csv`는 `waste_codes_4.csv`에서 `말단=Y` 및 `지정폐기물=Y`인 108개 코드와 정확히 일치했습니다.

## 간단한 호출 예시
```python
result = integrator.run({
    "waste_case_id": "CASE-001",
    "waste_code": "01-01-01",
    "waste_name": "폐폴리에틸렌",
})
print(result.to_dict())
```

## 검증 결과
제공된 CSV 전체를 대상으로 확인했습니다.
- 공식 분류번호 376개: `MATCHED` 313개, `PARENT_CLASS` 63개, 기타 오류 0개
- Crosswalk 키 75개: 모두 `PARENT_CLASS`로 정상 해석
- Crosswalk 말단 코드: 전체 존재, 전체 `말단=Y`, 말단 수 일치
- 지정폐기물 목록 108개: 공식 분류표의 말단 지정폐기물(Y) 108개와 완전 일치
- 통합기 동작: 대표 입력 5건에서 상태 및 Gate 분기 확인

참고: 실제 프로젝트의 `mixture_checker.py`와 상위 Agent 입력 연결은 이 패키지에 포함하지 않았으며 기존 구현을 유지합니다.
