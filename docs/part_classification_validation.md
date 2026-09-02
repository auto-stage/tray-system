# 실제 6종 부품 분류 검증 절차

## 현재 판정

- 소프트웨어 준비: 완료 후 자동/합성 프레임 테스트로 확인한다.
- 실제 분류 성능: 카메라와 실물 6종이 없는 현재 환경에서는 미검증이다.
- Mock 응답은 Reference와 Classification Test 통계에 저장되지 않는다.
- 복수 부품 counting은 단품 분류가 통과하기 전까지 `미검증` 상태다.

## UI 시험 순서

설정 화면에서 `Camera / Vision`을 선택한 뒤 카메라 역할 탭을 사용한다.

1. `ArUco / Stage Camera`에서 이동 스테이지용 카메라를 선택한다.
2. `Work Order / Inspection Camera`에서 고정 카메라를 별도로 선택하고 Enable 한다.
3. 고정 카메라 Preview가 `CONNECTED`인지 확인한다. 이 물리 카메라 한 대를 OCR과 Part Inspection이 공유한다.
4. 검사 영역에서 부품을 모두 치운 뒤 `빈 배경 등록`을 누른다.
5. 정답 class를 선택하고 해당 실물 한 개만 놓는다.
6. 회전·위치·거리·조명 조건을 선택하고 `현재 Object Sample 등록`을 반복한다.
7. 각 class에 최소 2개가 있어야 baseline 계산을 시도한다. 실제 평가는 class별 8~10개 이상을 서로 다른 조건에서 수집하는 것을 권장한다.
8. Reference 촬영과 다른 배치로 부품을 놓고 `Classification Test 기록`을 반복한다.
9. Ground Truth, Predicted, Correct/Misclassified/Unknown, score, 클래스별 누계와 confusion을 확인한다.

대상 class는 다음 6개로 고정한다.

- `flange_nut` — 플랜지 너트
- `t_bolt` — T 볼트
- `socket_head_bolt` — 렌치 볼트
- `corner_bracket` — 코너 브라켓
- `t_nut` — T 너트
- `l_bracket` — L형 브라켓

잘못 촬영한 Reference는 선택한 class 단위로 초기화한다. 이 작업은 확인 대화상자를 거치며 해당 class의 Reference와 관련 시험 기록을 함께 지운다.

## 확인할 조건

각 class를 최소한 다음 조건에 걸쳐 시험한다.

- 회전: 0°, 약 45°, 약 90°
- 위치: 좌측, 중앙, 우측
- 거리: 정상 거리를 중심으로 약간 가까움/멀어짐
- 조명: 정상 운용 범위의 밝기 변화

특히 다음 confusion을 우선 확인한다.

- `t_bolt` ↔ `socket_head_bolt`
- `corner_bracket` ↔ `l_bracket`
- `corner_bracket` ↔ `t_nut`

UI score는 OpenCV feature distance를 변환한 상대 점수이며 확률이 아니다. Reference 범위를 벗어나거나 1·2위 class의 거리가 충분히 벌어지지 않으면 정상적으로 `unknown`을 반환한다.

## 단품 통과와 counting 진입 기준

Reference에 사용하지 않은 반복 촬영 결과로 판단한다. 권장 데모 진입 기준은 각 class별 30회 이상 시험, class별 correct 95% 이상, unknown 5% 이하, 주요 confusion 쌍의 반복 오분류 없음이다. 위치·회전 한 조건에만 성능이 집중되면 통과로 보지 않는다.

이 기준을 만족한 뒤에만 다음 순서로 확장한다.

1. 복수 object 분리
2. 복수 부품 class 판정
3. class별 counting
4. OCR 작업지시와 비교
5. 최종 작업 UI PASS/NG

현재 OpenCV Adapter는 의도적으로 단품만 허용하며 여러 object를 찾으면 `MULTIPLE_OBJECTS_IN_SINGLE_MODE`로 `unknown`을 반환한다.

## OpenCV 유지 / YOLO 전환 기준

OpenCV를 유지하려면 조건 변화 후에도 선택 feature의 class 영역이 분리되고 위 단품 통과 기준을 만족해야 한다. 실제 sample에서 분산 대비 class 중심 분리가 큰 feature만 자동 선택하므로 임의 class threshold를 설정 파일에 넣지 않는다.

다음 중 하나가 반복되면 규칙을 추가로 누적하지 않고 YOLO 등 학습 기반 detector 전환을 검토한다.

- 다양한 Reference를 보강해도 classifier가 `REFERENCE_CLASSES_OVERLAP` 상태다.
- 회전·위치·정상 조명 변화에서 같은 confusion 쌍이 반복된다.
- unknown을 줄이면 오분류가 늘고, 오분류를 줄이면 unknown이 운용 불가 수준으로 늘어난다.
- 복수 부품에서 contour가 합쳐지거나 가려짐 때문에 개별 object 분리가 안정적이지 않다.

전환 판단은 실제 UI 누적 통계, confusion matrix, 저장된 실제 frame/feature를 근거로 기록한다. 합성 테스트나 Mock 성공은 전환 판단의 성능 근거로 사용하지 않는다.
