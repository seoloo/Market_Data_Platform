# Elliott Wave 분석 체계와 독립 연구 설계

작성: 2026-09-24 · 트랙: `EW-WA-CONTEXT` · 상태: **Phase 0 설계 / 4h 후보 창 가용성 감사, 시장 파동 판정 미실시**.
최초 기반 점검 기준 커밋: `c01bee7`. 본 문서는 거래 전략이나 수익성 판정이 아니다.

## 1. 목적과 변경하지 않는 경계

질문은 “과거 차트에 가장 그럴듯한 파동을 그릴 수 있는가”가 아니라,
**당시 알 수 있었던 파동 후보 상태가 실제 워뇨띠 진입과 어떤 관련이 있었는가**다.
원자료에 Elliott 사용 증거가 없으므로 사용했다고 가정하거나 거래 의도를 역추론하지 않는다.

- `T/v2`, `O/v1`, `O/v2`, `O/v3`, Paper 원장, 테스트넷, UI, 위험 설정, 스케줄은 변경하지 않는다.
- D22는 운영 전략으로 계속 `INCOMPLETE / EXCLUDED`. 최신 사용자 요청에 따라 이 독립 연구에서만
  HTF/MTF·변동성·과확장·시장 구조의 **설명 변수 틀**을 참조한다. 옛 점수·진입 규칙은 복원하지 않는다.
- `WA_strategy`는 [08의 기각 전략](08-wa-preregistration.md), `WA_trades`는 워뇨띠 실제 체결 자료를 뜻한다.
  두 대상을 이름·테이블·보고서에서 분리한다. WA 전략을 Elliott 조건으로 재튜닝하지 않는다.
- 연구 출력은 Shadow 전용. 주문·veto·사이징·보호주문에 영향 없음. 유의한 결과도 새 가설 제안까지만 허용한다.
- 실거래에서 Elliott 파동 분석·판정은 **최소 마감된 4h 봉 이상**으로 한다. 1m은 4h 내부 사건의
  순서·자료 품질만 감사하며, 1m 파동 카운트/진입 신호/주문 판단을 생성하지 않는다.
- 문헌 정리 후 가드 피벗·RSI 다이버전스·1m 경로 재생, 4h 증거·합성 문법·제한된 후보 창 감사까지 추가했다.
  **시장 파동 count**, WA 결합·가설 검정은 하지 않았다.

## 2. Opus 작업 인계와 재사용 가능 기반

아래는 코드·문서·커밋을 확인한 기록이다. 테스트·스케줄·거래소 호출을 재실행한 결과는 아니다.

| 대상 | 확인한 현재 기록 | 이번 연구와의 관계 |
|---|---|---|
| 운영 | T/v2와 O 계열 별도 Paper, O/v2 비상 스톱, O/v3 2배 관찰, 저녁 실행 시각 변경 | 기존 운영을 동결. PROGRESS에는 사용자 스케줄 변경 확인이 남아 있음 |
| 안전성 | WS 25개 테스트 추가, 실측 훈련 최종 9/9; 운영 실행기 스트림 연결은 후속 작업 | Elliott 성과 또는 실거래 승인 근거가 아님 |
| UI | localhost·GET·DB mode=ro의 6개 화면 구현 | 이번 연구 결과를 아직 연결하지 않음 |
| 최근 후보 | [32 신규 상장 숏 등 비교](32-new-direction-candidates.md) | 후보 제안 상태를 보존. 이번 요청을 그 후보 실행 승인으로 해석하지 않음 |
| 실제 체결 | [06 탐색](06-wonyotti-exploration.md): 2018~2021 BitMEX 자료, XBTUSD 왕복 2,589건 재구성 | 기존 집계 재사용 후보. 이번에 재계산하거나 원본 진위를 새로 인증하지 않음 |
| 시장 결합 | 기존 v3는 Binance BTCUSDT를 대용해 2020~2021의 390건 분석 | 거래소·상품 차이, 제외 사유, 더 긴 warmup을 다시 감사해야 함 |
| 피벗 | [structure.py](../../src/auto_trading/features/structure.py)에 `at_ms`와 `confirmed_at_ms` 분리 | 재사용 후보이지 Elliott 완성 엔진이 아님. 장중 고저 순서·동률·초기화 추가 감사 필요 |

06의 기존 관찰에서는 2020~2021 숏 진입의 단순 “과열 끝 역추세” 설명이 지지되지 않았다.
이는 새 Elliott 가설의 자동 기각도, 지지도 아니다. 반대 결과를 숨기지 않는 배경 정보로 보존한다.

## 3. 자료, 권위, 충돌 처리

### 3.1 읽은 자료

- **P**: 사용자 제공 `엘리어트 파동 이론(by+엘갤).pdf`, 21쪽. 원본 위치 `C:/Users/seolh/Downloads/`.
  SHA-256 `7b1b8e64caebad8854292952f1d7e54e6a3cc4816c85efaf20d4a94fb1ce91d2`.
  전체 본문 추출, p.4·9·13·15·17·20 도해/표 렌더링 확인. 번역·편집 DOOROO, p.2에 원자료 출처 명시.
- **A**: [요청한 Aurra 한국어 URL](https://www.aurra.markets/ko-kr/academy/advanced-guides/elliott-wave-theory-a-trader-guide-to-wave-counting-and-strategy)은 읽기 도구에서 오류.
  [같은 제목의 공식 영문판](https://www.aurra.markets/academy/advanced-guides/elliott-wave-theory-a-trader-guide-to-wave-counting-and-strategy)을 확인했다.
  한국어판과 문장별 동일성은 미확인. 영문판 갱신일 2026-08-11, 조회일 2026-09-24.
- **E**: 아래 Elliott Wave International의 직접 해설을 규칙 교차 확인에 사용했다.
  [Impulse](https://www.elliottwave.com/waveopedia/impulse/),
  [Rules / guidelines](https://www.elliottwave.com/articles/elevate-your-elliott-wave-analysis/),
  [Zigzags](https://www.elliottwave.com/waveopedia/zigzags/),
  [Flats](https://www.elliottwave.com/waveopedia/flats/),
  [Triangles](https://www.elliottwave.com/waveopedia/triangles/),
  [Diagonals](https://www.elliottwave.com/waveopedia/elliott-wave-pattern-diagonals/),
  [Truncation](https://www.elliottwave.com/waveopedia/truncation/),
  [Combinations](https://www.elliottwave.com/waveopedia/combinations/).

이들은 이론·교육 자료이지 BTC 예측력의 실증 연구가 아니다. “높은 확률”, “반드시 반전” 같은
표현을 검증된 성과로 가져오지 않는다. 문서 안의 매매 권유도 시스템 실행 지시가 아니다.

### 3.2 반드시 분리할 규칙 층

| 층 | 뜻 | 구현 처리 |
|---|---|---|
| **H: hard rule** | 특정 패턴 정의 안에서 반드시 충족하는 구조 제약 | 위반 시 해당 패턴 후보 무효. 시장이 규칙을 지킨다는 뜻은 아님 |
| **G: guideline** | 자주 관찰된다고 제안된 비율·모양·모멘텀 특성 | 충족 근거 기록. 미충족만으로 구조를 무효화하지 않음 |
| **S: subjective / disputed** | 학파·분석가별 허용 범위나 해석이 다른 조건 | 규칙 프로파일로 명시. 결과를 본 뒤 느슨한 프로파일로 바꾸지 않음 |

여기에 **I: 구현 약속**(tick 경계, 피벗 임계, 탐색 한도)을 별도 표시한다.
I를 Elliott의 보편적 법칙으로 포장하지 않는다. 아래 H도 선택한 엄격 프로파일의 범위를 넘지 않는다.

### 3.3 원자료 오류·충돌 등록부

| 위치 | 문제 | 이 연구의 처리 |
|---|---|---|
| P p.17 vs p.15 | Triangle 세분 표기가 각각 5-5-5-5-5와 3-3-3-3-3 | 도해와 E의 삼각형 설명에 맞춰 **조정 다섯 다리, 3-3-3-3-3**로 정리. p.17은 표기 오류로 판단 |
| P p.17 vs p.15 | 확산 삼각형 105% 상한 문장과 125%·150% 도해가 충돌 | 105%를 hard rule로 사용하지 않음. 확산형은 초기 자동 판정에서 보류 |
| P p.7 | 4파가 3파를 50% 넘게 되돌리면 4파가 아니라는 단정 | 보편 hard rule로 채택하지 않음. 비중첩 등 구조 검사와 구분한 G/S |
| P p.9 | Diagonal 4파가 2파의 **시작**을 넘지 않는다는 표현 | 일반 상승 overlap과 충돌. 4파가 3파를 전부 되돌리지 않음, 즉 **2파 끝보다 위**라는 제약으로 명시 |
| P p.9 | Ending diagonal 5-3-5-3-5도 허용한다는 견해, overlap 예외 | S. 초기 ending 프로파일은 3-3-3-3-3·overlap·수렴형만 지원 |
| P p.12 / p.20 | 삼중 지그재그 WXYZ와 복합 WXYXZ 혼용 | 연결 X를 생략하지 않는 `W-X-Y-X-Z`로 표준화 |
| P p.3 | Triangle을 impulse 1·4파 중첩 예외처럼 표현 | 조정 triangle과 motive diagonal을 분리. 조정 triangle을 이유로 부모 impulse의 위반을 면제하지 않음 |
| P p.10·17 | 15분 이하에 주로 등장, 약 60%, 선물의 후속 5파가 가장 길다는 일반화 | BTC 실측 빈도로 취급하지 않음. 이번 Elliott 범위는 4h 이상 유지 |
| A 본문 | 3파가 가장 길다는 표현, 4파 조정으로 3파를 탄다는 도입 표현 | hard는 “3파가 최단이 아님”. 4파 이후는 같은 degree의 5파. 기사 매매 문장을 규칙으로 복제하지 않음 |
| E Impulse | 선물 일중의 드문 overlap 예외 언급 | S로 기록. 초기 엄격 프로파일은 wick overlap도 거부, 나중에 예외로 실패 후보를 구제하지 않음 |

## 4. 파동 문법: 무엇을 인식할 것인가

`motive = impulse 또는 diagonal`. 모든 motive를 impulse라고 부르지 않는다.
파동 번호는 봉 개수가 아니라 **하위 패턴으로 이루어진 가격 구간**이다.
6개 꼭짓점이 보이는 것만으로 내부 5-3-5-3-5가 입증되지는 않는다.

| 패턴 | H: 구조 정의 / 엄격 프로파일 | G: 보조 특성 | S·지원 범위 | 근거 |
|---|---|---|---|---|
| Impulse 1~5 | 5개 다리, 1·3·5 motive와 2·4 corrective. 3은 impulse. 2가 1 시작 침범 금지, 3 최단 금지, 4와 1 비중첩 | 3파의 강한 움직임, 2/4 교대 | 1은 leading, 5는 ending 대안 가능. 종가만으로 wick 위반 면제하지 않음 | P 3~7, E Impulse/Rules |
| ABC correction | ABC는 조정의 상위 표기이며 아래 subtype을 식별해야 함 | 이전 움직임 일부 회수 | 모든 correction을 ABC로 강제하지 않음. Triangle·WXY 별도 | P 11~20 |
| Zigzag | A motive(impulse/leading), B corrective, C motive(impulse/ending): 5-3-5. B는 A 시작을 넘어가지 않음 | C와 A의 길이 유사 | C 미달형·A/C 동시 diagonal 허용은 초기 보류. 일반형부터 | P 11~12, E Zigzags |
| Flat | 3-3-5. A/B corrective, C impulse/ending. 엄격 프로파일 B/A 되돌림 ≥0.90 | 상대적으로 횡보 | 0.90은 채택 문헌의 분류 경계. regular/expanded/running을 분리 | P 13~14, E Flats |
| Triangle | ABCDE의 조정 다섯 다리. 부모 4 또는 B 등 문법상 위치 확인 | 거래량·변동성 감소, E의 추세선 미달/초과 | contracting·barrier·running·expanding 구별. X 위치 허용은 학파 차이로 보류 | P 15~17, E Triangles |
| Complex correction | W-X-Y 또는 W-X-Y-X-Z, X도 corrective. W/Y/Z는 완성된 조정 패턴 단위 | 횡보 지속, 형태 교대 | 무한 중첩 금지. double/triple zigzag와 sideways three 구별. 초기 자동화 제외 | P 18~21, E Combinations |
| Leading diagonal | 1 또는 zigzag A 위치, motive 다섯 다리, 2/4 전부 되돌림 금지 | 이후 깊은 되돌림 | 5-3-5-3-5와 3-3-3-3-3 견해 병존. 1-2-1-2와 경쟁 후보, 초기 자동화 보류 | P 8~10, E Diagonals |
| Ending diagonal | 5 또는 zigzag/flat C 위치. 초기 strict는 3-3-3-3-3, 1/4 overlap, 수렴형 | 모멘텀 약화·throw-over 가능 | 쐐기 모양만으로 ending 확정 불가. 부모 위치 불명은 generic wedge | P 8~10, E Diagonals |

### 4.1 Impulse: Python으로 옮길 수 있는 최소 제약

시간순 endpoint `p0..p5`, 방향 `d=+1`(상승), `d=-1`(하락), `q_i=d*p_i`,
`L_i=abs(p_i-p_(i-1))`라 하자. 음수 q는 방향 대칭 표현일 뿐 실제 가격은 양수 Decimal이다.

- I: 시각 증가, 고/저 교대, 각 길이 >0, 가격은 해당 상품 tick 격자. 동률 시 피벗 선택도 고정한다.
- H/I: `q1>q0`, `q0<q2<q1`, `q3>q1`, `q1<q4<q3`, `q5>q4`.
  2가 시작점과 **같은** 경계는 이 strict 프로파일에서 무효로 고정한다. 문헌의 “넘지 않음”과 경계 차이를 기록한다.
- H/I: 완성 시 `L3 >= min(L1,L5)`가 필요. tick 수준 동률을 허용하되 진단 플래그를 남긴다.
  5가 진행 중이면 이 조건은 아직 `PENDING`일 수 있다. `L3<L1`일 때 5가 L3보다 길어지면 해당 후보 무효.
- H: 4파 구간의 **모든 관측 high/low**가 1파 가격 영역을 침범하지 않아야 한다. endpoint만 통과시켜 wick을 숨기지 않는다.
- H: 내부 문법 검증은 별도다. 내부 자료가 부족하면 `subdivision_status=UNVERIFIED`이며 full-valid를 주지 않는다.
- G: `q5>q3`가 통상형. 그렇지 않아도 자동 오류는 아니나 truncation의 내부 구조 확인이 필요하다.

1=새 구간 시작 후보, 2=그 되돌림, 3=추진, 4=조정, 5=마지막 추진이라는 역할을 기록한다.
시장 심리(기관 매수 등)는 OHLCV만으로 확인할 수 없으므로 관측 필드로 만들지 않는다.

### 4.2 ABC·삼각형·대각선의 수치 표현

ABC endpoint `a0,a1,b,c`, A 방향 `d`, `q=d*p`로 정규화한다.
`retr_B=(q_a1-q_b)/(q_a1-q_a0)`, `ext_C=(q_c-q_b)/(q_a1-q_a0)`.
분모 0은 INVALID_INPUT이다.

- Zigzag strict: `0<retr_B<1`, 일반형 `q_c>q_a1` + 내부 5-3-5.
  일반형만 검출하는 구현 한계를 표시하고 미달형을 “존재하지 않는다”고 결론내리지 않는다.
- Flat strict: `retr_B>=0.90` + 내부 3-3-5. B가 A 시작을 넘고 C가 A 끝을 넘으면 expanded,
  B는 넘지만 C는 못 넘으면 running 후보. regular의 “근처” tick/ATR 허용폭은 I로 별도 고정 전 자동 분류 보류.
- Contracting triangle: 같은 종류의 극값끼리 C가 A, D가 B, E가 C보다 안쪽이어야 한다.
  running은 B가 A 시작을 넘을 수 있다. 추세선 overshoot와 이전 피벗 초과는 다른 검사다.
- Ending diagonal strict: 부모 5/C 위치 + 각 다리 corrective + `q0<q2`, `q2<q4<q1`,
  `q3>q1`, `q5>q3`, `L1>L3>L5`, `L2>L4`를 초기 일반 수렴형 제약으로 제안한다.
  실제 `(time,price)`의 1-3선·2-4선 폭도 줄어드는지 검사한다. 길이 감소만으로 쐐기를 판정하지 않는다.
  마지막 throw-over는 선 경계 특성으로 별도 기록한다. 예외·절단형은 `UNSUPPORTED_VARIANT`.

### 4.3 Extension, truncation, alternation

- **Extension**: 1·3·5 중 한 다리가 내부 하위 구조를 길게 펼치는 현상. 특정 길이 비율만으로 확정하지 않는다.
  내부 완성 여부와 `L3/L1`, `L5/L1` 같은 관측량을 별도로 저장한다. “1.618 초과면 연장”은 이론 H가 아닌 I/G다. (P 4~7)
- **Truncation**: 5의 하위 다섯 구조가 완성됐지만 3의 극점을 넘지 못한 경우.
  단순 lower high / higher low 또는 아직 진행 중인 5를 절단으로 부르지 않는다.
  endpoint 확정 이후에만 당시부터 알 수 있는 라벨을 추가한다. (P 6, [E Truncation](https://www.elliottwave.com/waveopedia/truncation/))
- **Alternation**: 2와 4의 깊이·시간·복잡도가 다른 경향. 반드시 번갈아야 하는 H가 아니다.
  깊이 비율, 지속 봉 수 비율, subtype 동일 여부를 기록한다. 불일치만으로 후보를 버리지 않는다. (P 21)

### 4.4 Fibonacci

상승 leg `(a,b)`의 되돌림 수준은 `b-r*(b-a)`, 다음 leg의 확장 목표는 새 시작점 `c+r*(b-a)`.
하락은 동일한 signed 식으로 대칭 처리한다. 피벗 두 개·투영 기준점·가격 척도를 반드시 기록한다.
초기 길이는 선형 가격을 사용하고, 로그 척도 결과와 유리한 쪽을 골라 섞지 않는다.

`0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618, 2.618`은 참고 수준 집합이지 필수 도달값이 아니다.
0.5는 관행적 50% 수준으로 구분한다. 가까운 정도를 연속값으로 저장하고, “근처” 폭은 사전 고정한다.
Fib 도달만으로 wave 종료·반전·진입을 확정하지 않는다. (P 7·12·14·17)

## 5. Wave degree와 multi-timeframe counting

degree는 패턴의 부모/자식 관계이지 “4h=Minor, 1d=Intermediate”라는 고정 대응이 아니다.
Cycle·Primary·Intermediate·Minor 등의 전통 이름을 구현 정확도의 근거로 삼지 않고,
초기에는 `degree_id`, `parent_scenario_id`, `child_pattern_ids`로 계층을 표현한다.

- Elliott 출력 범위: **4h·12h·1d·1w**. 초기 검증의 주 시간대는 4h를 제안하고 나머지는 맥락/계층 감사용.
- 15m·30m·1h·2h는 D22 맥락 기록에만 사용. 4h 미만 Elliott를 몰래 추가해 검출률을 높이지 않는다.
- 같은 시간대 안에서도 작은/큰 피벗 척도로 하위 구조를 조사할 수 있다. 척도 수와 중첩 깊이는 제한한다.
  더 작은 파동이 해상도 아래면 `UNVERIFIED`다. 큰 TF만으로 완전한 내부 구조를 주장하지 않는다.
- 4h/12h/일봉은 UTC 경계, 주봉은 월요일 00:00 UTC를 제안. 공급자 close_ms의 포함/배제 규약을 변환·검사한다.
- 평가 시각 이전에 **마감되고 가용해진** 봉만 사용한다. 현재 주의 고저·완성된 일봉을 과거 장중에 주지 않는다.
- 1d를 중기·장기에 중복 가산하지 않는다. 같은 고점에서 파생된 여러 TF 후보를 독립 증거로 세지 않는다.
- 상하위 count가 안 맞으면 `MTF_CONFLICT`, warmup이 없으면 `INSUFFICIENT_HISTORY`.
  억지로 맞추거나 다수결로 단일 진실을 만들지 않는다.

## 6. Causal 판정과 시나리오 원장

### 6.1 시각 계약과 피벗

`extreme_at_ms`(고저 발생), `confirmed_at_ms`(그것을 확인), `available_at_ms`(자료 가용),
`as_of_ms`(판정), `computed_at_ms`(재생 실행)를 구분한다. 역사 재생에서는 실제 수신 시각이 없을 수 있다.
이때 `availability_mode=BAR_CLOSE_ASSUMED`와 고정 지연 가정을 표시하며 실제 당시 수신을 입증했다고 하지 않는다.

확정 피벗은 불변. 후보 끝점은 현재까지의 extremum으로 **현재 snapshot 안에서만** 잠정 기록한다.
미래를 붙여서 과거 시점의 미확정 피벗을 확정 피벗으로 바꾸지 않는다.
고저가 같은 봉 안에 있고 순서를 알 수 없으면 다음 봉 확인까지 지연하거나 `AMBIGUOUS_INTRABAR`로 남긴다.
기존 detect_swings의 장중 순서·초기화는 새 용도 테스트를 통과해야 재사용한다. 운영 모듈은 수정하지 않는다.

피벗 방식은 초기 고정 퍼센트 reversal을 재사용 후보로 제안한다. 임계값 수치·척도 수·warmup·window는 미정이다.
WA 날짜·방향·손익을 가린 개발자료와 합성 경로로 정하고 결과를 보기 전에 동결한다.
전체 표본의 변동성에 맞춰 임계값을 계산하거나, 각 WA 진입에 맞춰 시작점을 다시 고르지 않는다.

### 6.2 상태와 invalidation

`FORMING → END_CANDIDATE → END_CONFIRMED`; 별도로 `INVALIDATED`, `EXPIRED`, `UNKNOWN`을 둔다.
`END_CONFIRMED`도 선택한 인식 규칙의 확인이지 실제 시장 파동의 정답 인증은 아니다.

| 검증 대상 | 그 시점에 기록할 경계 / 조건 |
|---|---|
| 상승 2 진행 후보 | p0 이하 도달 시 strict impulse 후보 무효 |
| 상승 4 진행 후보 | p1 이하 wick 도달 시 strict impulse 후보 무효 |
| 상승 5 진행, L3<L1 | p4+L3 초과 시 3 최단 제약 위반 |
| Zigzag B 후보 | A 시작 도달/초과 시 strict zigzag 무효; flat은 **별도 신규 후보**로만 생성 |
| Ending diagonal | 부모 위치·내부 문법·수렴 제약별 predicate. 하나의 수평 가격으로 표현 불가한 경우 목록 저장 |
| “종료했다”는 주장 | 당시 endpoint보다 새 극점이 생기면 종료 가설 무효. 과거 완료 구조 자체가 없었던 것으로 삭제하지 않음 |

하락은 비교 방향 반대다. `invalidation_levels`는 여러 개 또는 null일 수 있다.
가격 기준(high/low/close)·비교 연산자·tick 처리·rule_id를 함께 저장한다.
**분석 invalidation은 거래 stop이 아니다.** 이후 재카운트는 새 timestamp/ID로 추가하고 과거 snapshot을 보존한다.

### 6.3 복수 후보와 confidence

모든 시점에 파동 이름을 붙이지 않는다. 무위반 후보 0개면 `NO_VALID_COUNT`, 자료 부족은 `UNKNOWN`으로 구별한다.
탐색은 연속·인접 피벗과 고정 시작 창을 사용한다. 임의 피벗 건너뛰기로 모양을 맞추지 않는다.
후보 생성 개수·깊이·실행량 상한을 설정하고, 초과 시 `SEARCH_TRUNCATED`를 기록한다.

초기 출력은 최대 **3개 후보 + UNKNOWN/OTHER 표시**를 제안한다. 이것은 상위 3개가 모든 가능성을
포괄한다는 뜻이 아니다. 하드 위반 후보는 순위 점수가 높아도 채택 불가다.

`confidence`는 초기에는 확률이 아니라 아래 근거 벡터다.

- `hard_checks`: PASS/FAIL/PENDING, `subdivision_status`: VERIFIED/PARTIAL/UNVERIFIED.
- `evidence`: Fib 거리·교대·채널 적합·모멘텀·거래량의 원값과 결측.
- `ambiguity`: 경쟁 count 수, 부모 충돌, 탐색 잘림 여부.
- `confidence_kind=RULE_SUPPORT_UNCALIBRATED`, `probability=null`.
- 순위는 고정 사전식 순서(위반 없음 → 검증된 내부 구조 → 덜 복잡한 설명 → 고정 ID)부터 시작한다.
  지표들을 임의 가중 합산한 87% 같은 숫자는 금지한다.

“count 정답 확률”은 정답 데이터 자체가 불명확하다. 향후 확률이 필요하면 예측 대상(예: 특정 기간 내
특정 구조 돌파)을 먼저 정의하고 별도 미열람 구간으로 calibration해야 한다. rank/softmax는 확률 검증이 아니다.

저장 계약 초안:

```text
Snapshot: track_id, schema_version, rule_profile, config_digest, code_digest,
          data_digest, venue, instrument, as_of_ms, availability_mode,
          timeframe, quality_status, search_truncated, unknown_reasons
Scenario: scenario_id, parent_id, degree_id, pattern, direction, phase,
          anchors[{price, extreme_at_ms, confirmed_at_ms, is_provisional}],
          hard_checks[], evidence{}, confidence{}, invalidation_levels[],
          lineage_id, supersedes_id, created_as_of_ms
```

seed가 필요한 표본 추출은 분석 runner에서만 수행·기록한다. 코어 판정은 I/O·시계·난수 없이
`analyze(closed_candles, previous_state, cfg, as_of_ms)`로 재현한다.

## 7. Momentum·volume·D22 맥락

보조 지표는 규칙 위반을 구제하지 않는다. 파동 모양과 동일 가격에서 나온 지표를 독립 증거처럼 중복 가산하지 않는다.
기간은 구현 전 고정하며 전체 표본 fit·성과 기반 기간 선택을 금지한다.

| 필드군 | 재현 가능한 관측값 초안 | 주의 |
|---|---|---|
| Momentum | 확정된 같은 종류 피벗 사이 가격 변화와 RSI 또는 MACD 값 변화 | 가격 고점과 지표 고점을 미래에서 골라 맞추지 않음. 약화만으로 5 종료 확정 불가 |
| Volume | 파동별 거래량/봉 수, 직전 rolling 기준 대비 상대 거래량 | 길이가 긴 파동의 총거래량 착시 방지. 거래소·contract/base/quote 단위를 통일 |
| HTF/MTF trend | 마감봉 가격-EMA 거리/ATR, EMA 기울기/ATR, 확정 HH/HL/LH/LL | D22에는 정확한 기간 미확정. 이번에도 승인된 옛 수치가 있는 것처럼 만들지 않음 |
| Volatility | rolling 실현 변동성, ATR/가격, 과거만의 percentile | 전 기간 분위수 사용 금지 |
| Price extension | `(close-EMA)/ATR`, 직전 확정 스윙 대비 signed 거리 | 경제적 “과열”로 바로 해석하지 않음. 먼저 연속값 보관 |
| Failed breakout | t 이전에 확정된 고저를 돌파한 뒤, t까지 이미 관측된 재진입 | 미래 반락을 t의 신호로 사용 금지. 돌파 폭·복귀 창·마감 조건 고정 필요 |

Elliott “extension”과 D22 “price extension/과확장”은 다른 필드다.
종료 후보+과확장+다이버전스를 합친 composite를 먼저 만들지 않고, 각각 및 교호작용을 비교한다.

### 7.1 상승·하락 다이버전스: 독립 보조 분석

2026-09-24 사용자 추가 요청. 초기 범위는 **일반 상승/하락 다이버전스(regular divergence)**다.
히든 다이버전스·연속 3중 다이버전스는 이번 기본 판정에 섞지 않는다.
가격과 모멘텀의 불일치를 측정하는 보조 변수이며, Elliott 필수 조건이나 자동 진입 신호가 아니다.

| 종류 | 비교하는 가격 | 대응 지표 | 해석의 한계 |
|---|---|---|---|
| 상승 다이버전스 `REGULAR_BULLISH` | 두 저점 중 나중 저점이 더 낮음 (LL) | 나중 값은 더 높음 (HL) | 하락 모멘텀 약화 가능성. 상승 반전이나 LONG 수익 보장 아님 |
| 하락 다이버전스 `REGULAR_BEARISH` | 두 고점 중 나중 고점이 더 높음 (HH) | 나중 값은 더 낮음 (LH) | 상승 모멘텀 약화 가능성. 하락 반전이나 SHORT 수익 보장 아님 |

근거: [Fidelity RSI 설명](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI),
[StockCharts 다이버전스 탐지의 한계](https://help.stockcharts.com/scanning-and-alerts/scan-writing-resource-center/scanning-case-studies/scanning-for-divergences)
(2026-09-24 조회). 인식에 시간이 걸리며, 단순 가격/지표 기울기의 반대만으로 충분한 패턴이 확인되는 것은 아니다.

**규칙 층의 구분**:

- H(다이버전스 분류 내부): 같은 지표·시간대·상품의 저점끼리 또는 고점끼리 비교해야 한다.
  고점 하나와 저점 하나를 연결하거나, 두 시점의 RSI 기간을 다르게 해서 판정하지 않는다.
- G: 과매도/과매수, 5/C 종료 후보, 거래량 약화, 이후 구조 돌파는 보조 근거다.
  RSI 30/70 통과를 기본 필수 조건으로 추가하지 않는다.
- S/I: 어떤 피벗을 연결하는지, 최소 간격·차이·유효 기간, 지표 선택은 연구 설계다.
  차트에서 가장 잘 맞는 쌍을 손으로 선택하거나 WA 손익에 맞춰 기간을 튜닝하지 않는다.

### 7.2 재현 가능한 판정·시각·무효화 계약

초기 **가격 피벗 기준 대응 방식**을 제안한다. 같은 종류의 연속 확정 가격 피벗 `p1, p2`를 선택하고,
각 피벗이 발생한 봉의 마감 기준 지표값 `m1, m2`를 대응시킨다.
이는 지표 자체의 독립적인 국소 저점/고점을 찾는 방식과 다르므로 `pairing_method=PRICE_ANCHORED`로 기록한다.
지표의 독립 피벗을 매칭하는 방식은 시간 허용폭·동률·확정 지연이 추가되므로 별도 버전으로 남긴다.

후속 [EW-0 프로파일](33-elliott-phase0-data-and-profile.md)은 두 epsilon을 0으로 두고
저장 가격·Decimal RSI의 엄격 부등호만 사용한다. 이는 tick·RSI 차이 문턱의 최적값이라는 주장이 아니다.
피벗 최소/최대 간격·노출 TTL은 WA 결합 전에 별도로 고정하며, 작은 수치 차이의 분포를 보고
사후 epsilon을 올려 유리한 패턴만 남기지 않는다.

```text
REGULAR_BULLISH:
  pivot_kind == LOW
  p2 < p1 - epsilon_price  AND  m2 > m1 + epsilon_indicator
REGULAR_BEARISH:
  pivot_kind == HIGH
  p2 > p1 + epsilon_price  AND  m2 < m1 - epsilon_indicator
```

가격은 wick 피벗, 지표는 같은 봉의 close 기반이라는 차이를 메타데이터에 명시한다.
엄격 부등호의 동률/문턱 경계는 `NONE`, 결측·warmup 부족·갭은 `UNKNOWN`이다.
간격 조건에 안 맞는 연속 쌍은 건너뛰고, 오래된 유리한 피벗을 찾아 대체하지 않는다.
주 지표는 RSI를 제안한다. RSI의 smoothing·초기화·0 손실/0 변동 처리를 고정한다.
MACD는 사전등록한 보조 비교로만 두며 line과 histogram을 구분하고 유리한 출력을 사후 선택하지 않는다.

- `detected_at_ms = max(두 가격 피벗 confirmed_at_ms, 두 지표 available_at_ms)`.
  값이 피벗 봉에 계산돼 있어도 두 번째 가격 피벗의 확정 전에는 확인된 다이버전스가 아니다.
- 미확정 두 번째 피벗은 `FORMING`, 확정 후 처음 조건을 만족하면 `DETECTED`.
  주 분석은 `DETECTED`만 사용하고, 미확정 관찰은 별도 표로 남긴다.
- `DETECTED`는 패턴 확인이지 반전 확인이 아니다. 이후 구조 돌파를 확인한다면 새 시각의
  `REVERSAL_CONFIRMED` 사건으로 저장한다. 그 확인을 과거 진입 시점으로 소급하지 않는다.
- 상승형의 두 번째 저점 아래 새 저점, 하락형의 두 번째 고점 위 새 고점은
  “그 endpoint에서 반전했다”는 **별도 가설**의 무효화 기준으로 제안한다.
  이미 관측된 다이버전스 사건 자체를 삭제하지 않으며, 새 피벗 쌍은 새 event ID로 남긴다.
- 고정 TTL 이후 `EXPIRED`. 수익이 날 때까지 유효 기간을 연장하지 않는다.
  해당 가격은 분석 경계일 뿐 실제 주문 stop이 아니다.
- 기본 연구 시간대는 Elliott와 같은 4h 이상. 낮은 TF와의 결합은 추가 연구로 사전등록하며,
  4h 가격 저점과 1h RSI 저점을 직접 한 쌍으로 비교하지 않는다.

추가 저장 필드: `divergence_id`, `lineage_id`, `type`, `timeframe`, `indicator_id`,
`indicator_config_digest`, `pairing_method`, `pivot_ids`, `price_values`, `indicator_values`,
`confirmed_at_ms`, `detected_at_ms`, `expires_at_ms`, `state`, `quality_status`, `reversal_invalidation_level`.
지표별 사건과 wave scenario의 연결은 별도 필드이며, 같은 가격 피벗을 공유하는 사건을 독립 표본으로 세지 않는다.

### 7.3 Elliott·D22·실제 거래와 비교할 항목

- DV1: 하락 5/C 종료 후보에 상승 다이버전스가 겹칠 때 WA LONG 집중도가 다른가?
- DV2: 상승 5/C 종료 후보에 하락 다이버전스가 겹칠 때 WA SHORT 집중도가 다른가?
- DV3: 추세·변동성·과확장을 맞춘 뒤에도 다이버전스가 추가 설명력을 갖는가?

DV1~DV3는 §8의 H1~H4를 보완하는 **추가 탐색 가설**이다. 확정 검정에 포함하려면 검정 가족·표본/검정력도
함께 다시 사전 고정해야 하며, 추가 결과 중 좋은 것만 골라 기존 기준을 통과했다고 하지 않는다.
Elliott 종료 후보 E와 방향에 대응하는 다이버전스 D의 네 집단 `(E=0,D=0)`, `(1,0)`, `(0,1)`, `(1,1)`을
같은 대조군 설계로 비교한다. UNKNOWN은 0에 합치지 않고 별도 보고한다.
먼저 진입 집중도를 보고, 이후 size·holding·PnL·MFE·MAE는 사후 결과로만 비교한다.
E/D의 겹침이 단순히 동일 가격 구조를 두 번 표현한 것인지도 확인한다.
어떤 결과도 워뇨띠가 다이버전스를 사용했다는 증거로 단정하지 않는다.

## 8. WA 실제 진입과 결합할 후속 분석 설계

### 8.1 데이터와 사건 단위

1. 원본은 `data/external/wonyotti/`에서 읽기만 한다. 파일 존재/크기는 확인했으며 이번에 CSV 내용은 재처리하지 않았다.
   [기존 재구성 코드](../../scripts/explore_wonyotti_trips.py)의 source hash·중복체결 ID·시각 정렬·역전 분할·수수료·funding 귀속을 감사한다.
2. 주 사건은 **0 → 비제로 최초 체결 시점**, 하나의 flat-to-flat episode당 1건. 포지션 반전은 청산/신규 분리.
   추가 진입·감축·주문 제출은 보조 연구로만 분리한다. 수십만 부분 체결을 독립 표본으로 세지 않는다.
3. 체결 시각은 주문 의사결정 시각과 다를 수 있다. 지정가가 미리 제출됐다면 체결 당시 상태와 관련성이지
   주문을 낸 동기를 설명하는 것이 아니다. 주문 생성 시각의 신뢰도가 확보되면 별도 비교한다.
4. 진입 직전 snapshot은 원시 microsecond 시각을 보존하고, `available_at < entry_time`을 보수적으로 사용한다.
   ms 절삭으로 같은 millisecond의 이후 사건을 포함하지 않는다. 동시성 불명은 이전 봉으로 한 칸 물린다.
5. 원상품은 **BitMEX XBTUSD 인버스 계약**. 해당 거래소 가격·거래량 확보가 우선이다.
   Binance 대용 연구는 별도 `CROSS_VENUE_PROXY` 표시, 같은 표에 구분 없이 합치지 않는다.
   BitMEX 자료 확보 전 Binance 기반 결과를 워뇨띠가 본 실제 차트로 부르지 않는다.
6. 2018~2021은 이미 체결·성과를 탐색한 구간이다. 새로운 파동 라벨을 붙여도 pristine OOS가 되지 않는다.
   모든 TF의 warmup·결측·최종 관측일로 공통 가용 기간을 다시 정한다. 손익에 따라 사건을 빼지 않는다.

### 8.2 입력과 사후 결과의 물리적 분리

| 필드 | 당시 입력으로 허용? | 정의 / 경계 |
|---|---|---|
| LONG/SHORT·첫 진입 계약수 | 파동 계산에는 금지, 나중에 join | 체결 부호와 최초 size. 파동 라벨러는 거래 파일 자체를 읽지 않음 |
| 실제 leverage | 확인된 당시 계좌 스냅샷 없으면 불명 | 전날 지갑 잔고는 근사이며 전 상품·평가손익과 시차 문제 존재 |
| 최대 position size·추가 진입 합계 | 금지, 사후 결과 | 기존 `max_abs`, `entry_btc`는 episode 미래 전체를 사용하므로 당시 feature/매칭변수 불가 |
| holding time | 금지, 사후 결과 | first fill → flat. 기간 끝 미청산은 우측 검열, 조용히 삭제하지 않음 |
| PnL | 금지, 사후 결과 | 실제 inverse 회계, BTC 총/순손익, fee/funding 분리. USDT 선형 손익식으로 바꾸지 않음 |
| MFE/MAE | 금지, 사후 결과 | 아래 가격 excursion과 size 경로 반영 계좌 excursion을 별도 정의 |

가격 excursion은 고정 진입가 `P0`, 방향 `d`에 대해 경로 `r(u)=d*(P(u)/P0-1)`에서
`MFE=max(0,max r)`, `MAE=max(0,-min r)`로 양의 크기를 보고한다. 이것은 inverse 계약 실제 PnL이 아니다.
분할 체결·포지션 크기 변화를 반영한 계좌 MFE/MAE는 실제 회계 재생 후 별도 열로 계산한다.
진입/청산이 봉 중간이면 봉 전체 high/low에는 보유 전후 가격이 섞인다. 더 세밀한 자료가 없으면
내부 완전봉의 하한과 경계봉 포함 상한 등 오차 범위를 남기고 정확값으로 부르지 않는다.

### 8.3 검증 질문과 상태 정의

첫 질문은 수익이 아니라 **진입의 집중도**다. 상승/하락은 시장 장기 방향이 아니라 종료 후보 leg의 방향이다.
상승 C는 하락 추세 속 반등일 수 있다. 이 구별 없이 “C파=하락”으로 코딩하지 않는다.

| 가설 | 진입 이전에 정의할 노출 | 비교 |
|---|---|---|
| H1 | 상승 5 또는 상승 C의 END_CANDIDATE / 최근 END_CONFIRMED | SHORT 사건에서 matched control보다 흔한가 |
| H2 | 하락 5 또는 하락 C의 같은 상태 | LONG 사건에서 matched control보다 흔한가 |
| H3 | ending diagonal·과확장·이미 확인된 failed breakout | 반대 방향 진입과 관련 있는가. 세 요인은 별도이며 복합은 추가 검정 |
| H4 | Elliott 종료 후보와 D22 맥락의 동시 상태 | D22만 사용한 설명에 비해 Elliott가 추가 설명력을 주는가 |

“종료 부근”은 hindsight 고점 ±몇 봉이 아니다. 종료 전 후보와 종료 후 확인을 분리한다.
`END_CANDIDATE`에는 현재 마지막 leg, 관측된 내부 구조, endpoint/경계 거리, confirm 불명 상태를 저장한다.
`END_CONFIRMED`의 “최근” 허용 기간 및 종료 후보 점수 문턱은 구현 전 고정한다.
그 수치가 미정이므로 지금 H1~H4는 검정 사전등록이 아니라 질문 목록이다.
과거 반전 성공 여부로 후보를 사후 선별하지 않는다. 틀린 후보·조기 후보·UNKNOWN도 모두 분모에 포함해 보고한다.

### 8.4 Random / baseline timestamp

- B0 **달력 기준선**: 동일 데이터 가용 구간의 규칙적 timestamp 전체. 파동 상태의 시장 체류 비율을 보여주는 기술 통계다.
- B1 **진입 가능 기준선**: 재구성 가능한 경우 그 시각 직전 해당 포지션이 flat인 risk set에서 무작위 추출.
  같은 연도/월·시간대·요일 등 활동 패턴을 맞추고, 각 case의 방향을 대응 control에도 부여한다.
  사용자 온라인 여부는 자료가 없어 통제하지 못한다고 적는다.
- B2 **시장 맥락 기준선**: B1에 진입 이전 추세·변동성·과확장 상태를 추가 매칭한다.
  B1은 총 관련성, B2는 기존 맥락을 넘는 Elliott의 추가 관련성이라는 서로 다른 질문이다.
  H4에서 분석할 변수 자체를 완전히 매칭해 변이를 제거하지 않도록 모형별 역할을 먼저 고정한다.
- seed·controls 수·허용 시간 차·caliper·재사용 여부·제외 사유·선택 확률을 저장한다.
  실제 case와 control의 봉 시점/확인 지연 규칙은 동일해야 한다.
- 월/주 단위 block resampling이나 제한 permutation으로 군집 의존을 반영한다. 시점 하나씩 독립 셔플하지 않는다.
  permutation의 교환 가능성 가정과 flat risk set 제약을 보고한다. 대조 시점 수를 늘렸다고 독립 WA 사건이 늘지 않는다.
- 같은 wave episode 안 여러 진입, 4h 동일 snapshot 반복, 중첩 보유를 cluster ID로 묶는다.

### 8.5 통계 계획과 판정 한계

주 보고량은 방향별 `상태 비율(case)-상태 비율(control)`, 비율비/odds ratio와 군집 보정 신뢰구간이다.
case-control 표본에서 계산한 확률을 실제 시장 진입 빈도로 해석하지 않는다.
후속 조건부 회귀는 D22-only와 D22+Elliott를 같은 복잡도 예산·시간 블록 평가로 비교한다.
size·holding·PnL·MFE·MAE는 이차 분석이며, 평균·중앙값·꼬리와 불확실성을 함께 보고한다.

- H1~H4, subtype, TF, direction, threshold 변형을 검정 목록에 모두 등록한다. confirmatory family는 Holm 등
  다중검정 보정을 사전 고정하고, 추가 세부 탐색은 exploratory로 분리한다. 가장 좋은 한 결과만 보고하지 않는다.
- 한 사건에 3개 scenario를 붙여 표본을 3배로 늘리지 않는다. 사전 정의한 집합 노출(적어도 하나의 후보)과
  보수적 합의 노출(모든 지원 후보가 같은 상태)을 병행하되 주/민감도 구분을 먼저 정한다.
  UNKNOWN 비중·후보 수에 따른 탐지 편향을 보고한다. max score 후보만 사후 선택하지 않는다.
- 검정력 80% 원칙은 유지하되, 이 질문은 거래 bps 평균 검정이 아니다.
  기저 상태 빈도·최소 의미 있는 집중도 차이·군집 수·상관·다중검정·결측을 사용한 설계 시뮬레이션이 필요하다.
  [기존 power 식](../current/BACKTEST_AND_TESTING.md)을 원시 체결 행수에 그대로 대입하지 않는다.
- 사전등록 전: 거래 방향/PnL을 가린 시장자료로 상태 빈도와 확인 지연을 점검하고, 기존 episode 메타데이터로
  가용 독립 군집을 감사한다. 효과 크기는 기대 수익을 보고 맞추지 않고 최소 의미 기준으로 정한다.
  숫자 확정 전에는 충분한 표본이라고 주장하지 않는다.
- 표본 부족은 `DATA_INSUFFICIENT`, 인식률 부족은 `LABEL_COVERAGE_INSUFFICIENT`, 자료 문제는 `DATA_QUALITY_FAILURE`.
  “Elliott가 효과 없음”과 구분한다. 긴 확인 지연 자체도 핵심 연구 결과다.
- 2018~2021 탐색에서 유의해도 워뇨띠의 사용 의도·인과·자동매매 수익을 입증하지 않는다.
  새 전략은 경제적 가설·현실 비용·별도 사전등록·미열람 검증과 사용자 승인을 다시 거쳐야 한다.

## 9. 구현 순서와 테스트 계획

파동 후보 구현은 복잡한 모든 패턴을 해결하려 하지 않고 **strict impulse·일반 zigzag·수렴 ending diagonal**에 집중한다.
Flat·Triangle은 혼동 대안/미지원 상태로 남기고, 학파 예외·complex·truncation 자동 확정은 나중의 별도 버전이다.
미지원 패턴을 무효로 간주하거나 모든 가격 구간을 셋 중 하나로 강제하지 않는다.

| 순서 | 산출물·게이트 | 범위 |
|---|---|---|
| A. Phase 0 후속 | source conflict 표 승인, 규칙 프로파일·피벗 척도·시간대·UNKNOWN 정책·분석 가족·검정력 설계 확정 | 실제 거래와 라벨 결합 전 |
| B. Phase 1 | 원자료 provenance, 시장 구간·TF 재집계·warmup·사건 재구성 감사 | DB read-only, 별도 연구 산출물. 운영 테이블 migration 없음 |
| C. Phase 2에 준하는 순수 연구 코어 | 4h 피벗·RSI adapter·1m 경로·4h 증거 구현. 엄격 문법은 합성 검증, bounded 후보 생성도 합성 검증. 시장에서는 **후보 창 가용성만** 감사(33 §13). 실측 하위 degree parser·시장 count·WA snapshot 결합은 대기 | 기존 decide/risk/execution import 경로에 연결하지 않음 |
| D. 인식 검증 | 합성 기대값·불변성·블라인드 차트 검토, coverage/지연/UNKNOWN 보고 | PnL이나 WA 진입점 표시 없이 검토, 분석가 불일치도 보존 |
| E. 별도 결합 승인 뒤 | WA event join·대조군·집중도 분석·사후 결과 기술 통계 | 이번 단계 밖. 거래 백테스트나 전체 Phase 4 통과로 부르지 않음 |

현재 순수 구성요소는 [pivots.py](../../src/auto_trading/research/elliott/pivots.py),
[divergence.py](../../src/auto_trading/research/elliott/divergence.py),
[intrabar.py](../../src/auto_trading/research/elliott/intrabar.py),
[minute_path.py](../../src/auto_trading/research/elliott/minute_path.py),
[h4_evidence.py](../../src/auto_trading/research/elliott/h4_evidence.py),
[grammar.py](../../src/auto_trading/research/elliott/grammar.py),
[candidates.py](../../src/auto_trading/research/elliott/candidates.py),
[coverage.py](../../src/auto_trading/research/elliott/coverage.py)다.
경로 자료 감사·4h 투영 규칙·적용률·합성 문법·시장 창 가용성 한계는
[33 §7~13](33-elliott-phase0-data-and-profile.md)에 기록했다.
다음 예상 모듈 경계(경로·API는 후속 구현 때 확정): pure `research/elliott/{types,rules,child_degree}.py`,
I/O는 `scripts/research_elliott_*.py`, 산출물 `reports/elliott/<run_id>/`에 분리.
분류기는 시장자료만 읽고, 거래 join runner는 고정된 snapshot을 받아 읽는다. 양방향 피드백 없음.

필수 테스트:

1. 상승/하락 대칭, impulse 각 hard rule 통과·경계·위반, 3파 최단 검사의 진행 중 PENDING.
2. 5-3-5와 3-3-5, 1-2-1-2와 diagonal, 일반 쐐기와 부모 5/C ending을 구분하는 반례.
3. future append/perturb 후 과거 snapshot byte-identical, prefix 재생과 online 재생 일치, 동일 입력 1,000회 동일.
4. 피벗 확정 지연·미완성 주봉·동일 봉 고저·동률·tick·갭·warmup·중복·역순 자료와 fail-closed.
5. 과거 count/무효 이유 보존, candidate explosion 제한, 잘림·UNKNOWN·UNSUPPORTED 구별.
6. 수동 주석은 독립 2인 이상·거래/미래 가림을 권고. 합의율은 재현성 측정이지 예측력 검증이 아님.
7. join의 엄격한 시각 경계, partial fill 군집, 반전 분할, 미청산 검열, MFE/MAE 경계봉 불확실성.
8. random control seed 재현·데이터 가용성 동일·matching balance·시간 군집 보존, null 합성자료의 거짓 발견 점검.
9. 다이버전스 합성 예시: 저점 100→95·RSI 25→32는 상승형, 고점 110→115·RSI 75→68은 하락형
   (각 차이가 설정 epsilon을 초과하는 테스트 설정). 같은 방향 변화·동률·문턱 경계·다른 TF·결측은 각각 NONE/거부/UNKNOWN 기대값 검사.
   피벗 확정 전 미노출, 지표 warmup·초기화, expiry, 미래 봉 추가 후 기존 사건 불변, 새 극값 이후 과거 기록 보존을 검사한다.
10. 구현 변경 시 프로젝트 전체 pytest·leakage·mypy strict·ruff·import 계약 검사.

## 11. 복수 시나리오 출력과 평가자(JEV) 연결 계약

Elliott 분석이 끝난 뒤 JEV를 붙일 수 있도록 **출력 형식과 평가자 인터페이스만** 먼저 고정한다.
JEV의 정체·공급자·모델은 미정이며 추측해 구현하지 않는다. 지금은 평가자를 붙이지 않았다.

**출력** — `research/elliott/scenario_contract.py`, 스키마 `ew-scenario-set/1`.
`generate_bounded_candidates` 결과를 그대로 변환한다(새 판정 규칙 없음, `EW-0` 불변).

| 요구 | 필드 | 규칙 |
| --- | --- | --- |
| wave count·현재 상태 | `wave_count`, `anchors[]`, `current_state` | 예: `WAVE_4_FORMING`, `WAVE_5_END_UNVERIFIED`, `END_INVALIDATED`, `REJECTED` |
| confidence | `rule_support` | 하드 규칙 PASS/FAIL/PENDING 수·가이드라인 부합 수 원값. `kind=RULE_SUPPORT_UNCALIBRATED`, `probability=null` 고정 |
| invalidation | `invalidation_levels[]` | 가격·비교(`<=`/`>=`)·기준(HIGH/LOW)·발동 시각. `scope=ANALYSIS_ONLY` |
| 근거 | `evidence[]` | 가족별 FIBONACCI·ALTERNATION(원값, 출처, 가이드라인 범위)·MOMENTUM·VOLUME·MULTI_TIMEFRAME·CHANNEL. 계산 안 한 것은 `NOT_COMPUTED` — 중립값이 아니다 |
| UNKNOWN 가능성 | `uncertainty`, 묶음의 `exhaustive=false`, `other_or_unknown=ALWAYS_POSSIBLE` | 하위파 `UNVERIFIED`, 경쟁 시나리오 수, 교차 거래소 대리 등 플래그 |

단일 count를 확정하는 필드는 없다. 묶음 상태는 `SCENARIOS`·`NO_VALID_COUNT`·
`INSUFFICIENT_ANCHORS`·`UNKNOWN`. `validate_scenario_set_dict`가 완전 목록 주장·확률 기입·
UNKNOWN 자리 제거·분석 외 무효화 범위를 거부한다. `scenario_set_digest`(정렬 JSON sha256)로 입력을 고정한다.

**평가자** — `research/elliott/evaluation.py`, 스키마 `ew-scenario-eval/1`.

- `ScenarioEvaluator` Protocol: `evaluate(scenario_set_json, *, evaluated_at_ms) -> ScenarioEvaluation`.
- 결과: 시나리오별 `relative_support`(0~1, 합 1 불필요) + 근거 문장, `other_or_unknown_support`,
  `interpretation=RELATIVE_NOT_PROBABILITY`, 평가자·모델·프롬프트 버전, 입력 해시, 원출력.
- 판정: `RELATIVE_SUPPORT` · `ABSTAIN`(근거 부족을 스스로 밝힘) · `UNAVAILABLE`(실패·시간 초과).
  실패를 중립 의견으로 채우지 않는다.
- `validate_evaluation`: 해시 불일치, 입력에 없는 ID, 중복, 0~1 밖, 확률 표기, 판정과 내용 불일치를 거부.
- 자리표시 `AbstainEvaluator`로 파이프라인만 시험한다.

평가 결과는 Shadow 기록일 뿐 주문·veto·사이징·보호주문에 쓰지 않는다. 거래 경로가
`auto_trading.research.elliott`를 import하지 못하도록 import-linter 계약이 막는다.
JEV를 실제로 붙이는 것은 (1) 하위파 출처 계약과 UNKNOWN 경계 문제가 풀리고
(2) 평가 대상·비교 기준을 결과 열람 전에 따로 고정한 뒤다.
상대 평가를 확률로 쓰려면 §6.3대로 예측 대상을 정의하고 미열람 구간에서 calibration해야 한다.

## 10. 다음 결정과 완료 조건

지금의 권고는 **이론 문법 → causal 후보 판정기의 신뢰성 → 실제 진입 집중도 → 새 가설** 순서다.
파동 라벨 수나 과거 성공 그림 수를 성과 지표로 삼지 않는다.

후속 Phase 0의 [시장 데이터·초기 규칙 감사](33-elliott-phase0-data-and-profile.md)에서
4h 개발용 시장·2% 피벗·RSI(14)·가격 피벗 기준 엄격 비교를 `EW-0`로 고정했다.
이것은 WA 결합 분석의 사전등록이나 검정력 통과가 아니다.

미확정: Elliott 후보 탐색 깊이·endpoint window, 다이버전스 노출 TTL·최소/최대 간격,
시장 가격 단위 감사,
endpoint 확인·최근 구간 정의, profile 세부 예외,
동일 거래소 데이터 확보, 통계적 최소 효과·군집/대조군 수·검정력. 결과를 보기 전에 별도 확정해야 한다.
따라서 본 문서는 사전등록 완료도, Elliott 파동 판정기·WA 실제 결합 분석 완료도 아니다.

문서 검증: 출처·로컬 링크·요구 주제·변경 범위 검사. PDF 원본 보존.
후속 구현에서 독립 연구 모듈·테스트·import 계약만 추가했으며 기존 전략/DB/스케줄 변경 없음.
