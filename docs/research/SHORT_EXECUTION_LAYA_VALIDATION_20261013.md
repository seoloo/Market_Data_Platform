# 고정 숏 두 버전 — 실행 자료 감사·Laya 비교 사전등록

작성: 2026-10-13 Codex. 운영 Phase6 유지. 신규 정식 전략은 Phase0/UNVERIFIED.
상태: **REGISTERED_EXPLANATORY_DESIGN / PREDICTIONS_AND_PERFORMANCE_NOT_RUN**.

## 1. 승인과 이번 연구의 질문

사용자: “다음작업 진행해주고 검증 설계하고 완료까지 해줘 그리고 laya를 사용한 것과
안한것을 비교도 추가로 해줘. 확장해도돼 기존 연구들과 달라도 되고 제약 없어도 되니까”.
추가: **“시장가로 해도돼 그것도 같이 비교해줘”**.

이번 요청을 유한한 설명용 후향 검증/범위 확장 승인으로 기록한다. 검정력 선행 예외는 이번
파일럿 계산에 한정한다. **시장가 예외는 이 연구의 체결 가정 비교뿐**이며 실제 시장가 주문·
실거래 설정 변경 권한은 아니다. 좋은 숫자를 만들려고 결과 후 규칙/질문을 바꿀 권한으로 쓰지 않는다.
208/그 자료·2026-09-01 이후 보존 시장자료·기존전략/운영/Paper/Live/주문/예약/환경설정은 보존.
Elliott/D22 판정 불변. 새 수익 OOS/PASS/운영 승인은 이번 완료의 뜻이 아니다.

H-E: 동일 고정 신호에서 분봉·참여 제한·부분청산·정산mark 근사를 반영해도 가격 체결 대리의
수익과 실행 가능성이 유지되는가? **현존 historical OHLCV≠실제 체결/호가 기록**이라는 한계를 둔다.
H-L: 고정 기회를 Laya가 선별했을 때, 피한 손실이 놓친 이익과 API 비용을 넘는가?
같은 변수를 문장으로 재조합하는 것이며 별개의 기관 자금 정보가 아니다.

## 2. 이전과 다른 점, 바꾸지 않는 점

이전 [3,840조합](MULTI_INDICATOR_COMPLEMENT_DRAFT_20261012.md)에서 Keltner의 전체 증분은
양수였지만 후반에는 이익이 감소했다. 이번에는 새 지표/청산을 탐색하지 않는다.
이전227/229의 Laya 사용과 달리: 날짜/심볼/절대가격 제거, 질문/문턱1개 고정,
공통 응답 coverage 비교, TRAIN상수 확률 대조, 피한손실/놓친이익·무작위 같은 수의 선택,
문장 순서·반복 응답 안정성, 분봉 부분체결·fundingmark 자료 감사까지 한다.
기존227/229의 불리 판정을 재판정하거나 다른 이름으로 채택하지 않는다.

기존 코드/입력은 수정하지 않는다. 신규 순수 모듈 `short_validation_20261013.py`,
신규 runner `run_short_validation_20261013.py`, 합성 테스트만 추가한다.

## 3. 고정 전략·실험표 — 재선택 없음

두 버전:
- RSI: RSI7 직전≥70→현재<70, 마지막완성일봉 ADX14>25·−DI>+DI.
- KELTNER: 위 조건+4h EMA20≤종가≤EMA20+2ATR20.
- 모두 SHORT, 48h 보유/3% 보호stop. 진입계기·위험회피·사이징 숫자는 이전 고정대로.

각 버전의 Laya OFF/ON, 각 주문 IOC/MARKET =8 주 비교.
추가 FULL(응답 결측과 무관한 기존 신호)2×주문2는 coverage 감사용.
총12 계좌대상×기본/비용2배/4h지연×4기간=144 계좌. 144개의 새 수익 가설/PASS가 아니다.
TRADE edge는 종목 내 겹침 제거, 계좌는 공유자본/위험/다음진입의 내생적 효과를 별도 기록.

기간: BTC/ETH USD-M2020-06-01≤t<2026-09-01.
TRAIN2020~2023 / 역사확인2024~2025 / 후반2026-01~08 / ALL.
모두 기존 노출 자료, 미사용OOS0. 끝144h 진입 purge, 기간별 계좌 초기1만USDT, ALL은 연속.

## 4. 자료와 실제 체결의 한계

- 기존 `data/trading.db` 읽기전용의1h→4h/funding. 이전 raw SHA를 대조한다.
- 기존 무료 공식 월별 ZIP: BTC/ETH1m, mark1h, 정확히2020-06~2026-08의75개월씩.
  1m 전체 timestamps/OHLCV/기존4h 재집계 일치/ZIP CRC·SHA를 확인한다.
  실행에 필요한 고정 기회 주변144h만 메모리에 보관, 원본/DB에는 쓰지 않는다.
- 성과 전 BTC14봉의 기존1h→4h와1m→4h 불일치를 발견했다. 2023-11 공식1m/4h ZIP
  현재 체크섬은 둘다 일치하지만, 일부4h는1m합계와도달랐다. 따라서 단순 코드 반올림 문제라고 숨기지 않는다.
  이번 연구는 **동일1m 원천으로 평가구간4h를 재구성**해 로컬 연구 JSON으로 고정한다.
  준비기간은 기존자료, 평가기간의 OHLCV/지표/모델입력은 새 canonical4h로 모두 일관 적용.
  미규명가격진실을 인증한 것이 아니며, 원시1m/공식4h/기존DB 차이는 별도자료 경고로 보존.
  기존DB·과거연구의 숫자·판정은 소급 수정하지 않는다. 코드/수치는 성과 전에만 확정한다.
- 무료 공개 Binance fundingRate REST를 같은 상한으로 따로 보관. 정산 시각·rate 일치 확인.
  같은 정산 시각의 실제mark가 있으면 사용, 없으면 해당hour mark의 **시가**를 사용한다.
  해당hour 고저는 비용결과의 상·하한 민감도이며 실시간 feature가 아니다.
- 기존 직전4h 종가 근사를 hour mark 시가 대리로 교체한다. 모든 비교군에 동일하게 적용.
  정산 후 수 ms jitter의 정확한mark를 hour 시가로 인증하지 않는다.
- 가격 touch만으로 체결하지 않는다. IOC는 다음 시가의 불리 가격이 직전 참조 종가±0.3% 안인지,
  MARKET 대리는 가격한도 없이 같은 불리 가격/유동성 제한으로 비교한다.
  진짜 과거 호가/queue/틱 스프레드가 없어 **조건부 체결 대리**다. 시장가도 전량 체결 보장 없음.

mark ZIP에 정산hour 누락도 발견했다(BTC27·ETH9). 필요한36시간만 무료 공식REST로
연구 전용 파일에 보완한다(최대64hour, 시간/심볼/종료경계 검사). 기존ZIP은 덮어쓰지 않는다.
실제 정산mark가 아니라 hour OHLC 보완이므로 시가 대리/고저 bounds라는 한계는 유지한다.

## 5. 비용과 회계

4h 계좌는 이전 고정 회계: fee편도0.05%, 가격불리편도0.045%, stop추가불리0.2%,
실제fundingrate×보유수량×mark대리. IOC/MARKET 동일 비용; maker 우대를 부여하지 않는다.
종목목표0.25배/거래위험1%/총명목≤1배/DD30%/일손실2%/4연패240분 유지.
기존 G5/G6 전부/청산tier/mark기반 강제청산 parity는 미검증이며 전체 실행기와 같다고 쓰지 않는다.

분봉 부속감사(새 복리 계좌 아님):
- 각 고정 FULL baseline episode의 신호마감+1분(지연은추가4h)에 진입, Laya/OFF 같은 지연.
- 희망명목2,500USDT. 수량 .001 내림, **직전 완성1m volume0.1%** 한도, 남는 진입량 취소.
- 48h 뒤 일반 청산은 매분 가격한도/참여 한도에 맞는 수량만 청산. 나머지 보호 유지·다음분 재시도.
- 비상stop은 매분 고가로 확인, gap/불리 가격 적용. minute내순서불명 funding은 debit 유지/credit 생략.
- entry funding 제외, carried exit funding 포함, 부분청산 이후 줄어든 수량으로 계산.
- 144h까지 ordinary 미청산이면 MTM+fee reserve/UNRESOLVED, 시장가 청산을 발명하지 않는다.
- 이 부속감사는 고정 기회당 수익·부분량·미체결·분할청산의 실행 영향이다.
  계좌위험 gate·재진입을 모두 재현한 새 분봉 CAGR/Sharpe라고 표현하지 않는다.

## 6. Laya 입력·질문·비용·재현

`english` 명시(공급자 default의 다른 모델로 바꾸지 않음). 공급자는 `api.laya-ai.com`만.
질문1개: 익명snapshot만으로48h/3%stop/왕복0.19%+funding/stop추가0.2%인 SHORT가
비용 후 positive / nonpositive 중 어느 쪽인가? positive 확률≥0.60이면 ON 기회 허용.
숫자와 질문은 소스에서 고정, 문턱·질문·모델을 결과 후 탐색하지 않는다.

입력: 4h/1d/7d/30d수익, 직전/현재RSI7·RSI14, Stoch14 K, ATR14/가격,
EMA20거리/ATR20, 현재volume/직전20평균, 마지막가용fundingrate, 완성일봉ADX하락 여부.
모두 신호시점까지 계산, 결측/0분모/funding9h초과는UNKNOWN.
날짜/심볼/절대가격/뉴스/향후PnL·성과·추천답·계좌/키는 입력에 넣지 않는다.

유효 응답이어야 OFF/ON 공통 coverage에 포함. JSON확률finite·[0,1]·합1±0.001,
binary label 검사, batch응답60초초과는진입비교에서미가용. 실패를중립/0.5로채우지 않는다.
OFF FULL도 별도로 보고해서 API결측으로 baseline 손익을 유리하게 바꾸지 않는다.
raw response/input/prompt-model hash/요청응답 시각/latency를 저장·재생한다.
고정32상태는필드순서만뒤집어추가질문,16상태는동일요청반복(lexicographic hash선택, 결과무관).
이는 안정성 부속감사이며 가장좋은표현을 골라 수익을 재계산하지 않는다.

상한512 state결정(본요청+부속검사+재개누적 포함), batch16, retry/자동충전 없음.
공급자의 현재 안내0.05USD/1,000calls 기준 상한추정0.0256USD, token과금 안내와 충돌한
검색 캐시는 실제 요금 증거가 아니다. 계정 실제청구액은 미확인으로 기록, 예상비용만 분리한다.
환경변수·운영 client/뉴스·알림은 변경하지 않는다. 외부 전송은 위 익명시장 feature와 서비스 인증뿐.

**오늘 요청한 모델로 과거 수치를 판정한다.** 학습 cutoff/불변 weight digest가 확인되지 않았으므로
익명화가 과거 학습 기억을 완전히 제거했다는 주장도, “그 당시 Laya가 이 답을 했다”는 주장도 금지.
이는 후향 모델 pilot이고 모델 확률은 실현 수익확률로 calibration되지 않았다.

## 7. 평가와 설명용 판정 — 성과 열람 전 고정

- 각군 순손익/CAGR/Sharpe/MDD/노출/거래수/turnover/fee/funding/정지/종목/세기간/2배비용/지연.
- 고정 OFF episode의 retained/skip, 피한손실/놓친이익, 평균 증분은 **baseline 전체 기회당** 계산.
  선별된 거래당 평균만으로 성공이라 하지 않는다. 계좌는 다음 진입 변화가 있어 인과기여와 구분.
- Brier vs TRAIN만 fit한 상수 승률, 실제비용 후 win을 label로 사용. 확률calibration의 실행조건 의존성 기록.
- 같은기간/같은종목/같은 retained 수의 무작위 baseline episode subset1000개(seed20261013).
  미래를 쓰는 운영 전략이 아니라 후향 random null, 코인/시간상관 때문에 독립p가 아님.
- 필드순서 gate변경율, 반복확률차, provider반환model/route, 결측/latency·응답 coverage.
- 분봉기회 고정명목 총net·명목당net·부분진입/청산·price cap 미체결·누락·mark비용 bounds.
- 설명용 Laya증분 **유리**: coverage≥95%, retained TRAIN≥40/확인≥20/후반≥10,
  각 rule의 확인·후반에서계좌net증분>0 및기회당증분>0, 비용2배/지연확인net양수,
  paired random percentile≥95%, 순서변경 gate≤10%를 모두 만족할 때만.
  확인·후반 기회당증분 모두≤0이면불리, 그외는판단불가/혼재. 표본미달·결측은따로기록.
  market/IOC 중 좋은 것만 고르고 통과시키지 않는다. 정식 PASS와 기존 판정은 불변.

## 8. 전향 검증 설계 — 설계 완료와 검증 통과를 구분

향후 미사용 관찰의 최초 가능 경계2026-10-14 00:00UTC(이번에는 수집을 켜지 않는다).
그 이후라도 다른 연구가 이미 본 자료는 exposure등록부감사 뒤에만 미사용으로 인정한다.
운영 연결 전 모델/질문/데이터/체결/cost/state-schema를 불변 버전으로 동결하고 두군의 판단·실패도 전부 기록.
단기누출방지:4h마감/완성일봉, 입력보관asof, 모델응답뒤첫완성분 시가부터만체결가능;
실제호가/수량·부분체결·정산mark·판단latency를 미래구간에서 측정한다.
미측정호가를 과거에서 복원하지 않는다. 운영Shadow/Paper 활성화는 별도 승인 단계.

전향 최소: 실제OOS체결≥100 및80% paired증분 검정력, 계좌정지로 표본이 끊길 가능성 포함.
필터는 거래 빈도를 줄이므로 전략 건수와 baseline 기회·주cluster 수를 따로 계산.
파일럿 TRAIN paired SD로 필요표본/100건 detectable effect를 계산하되 **후향·선택오염 SD**라고 명시.
25bps 증분은 최소 실용효과의 가정이지 참효과 증거가 아니다. 4개 주요비교의 보수계수 z2.6/80%로 계획.
IID표본수는 하한 참고이며 BTC/ETH동시성·중첩·주cluster/HAC로 다시 늘려야 한다.
12개월은 실행자료 점검기간이지100건·검정력 통과 보장이 아니다. 기간만채워PASS하지 않는다.
최종OOS는선택에쓰지않고 Phase4의baseline/비용/안전/누출등 모든요건을 별도 점검한다.
이전숫자·질문을고치면새가설·새등록·새미사용구간이필요하다.

## 9. 실행 순서와 완료 증거

자료감사(성과0)→합성/누출/전체검증→이설계+코드+테스트 commit→Laya호출/캐시고정→
고정 비교1회→완료결과/TXT/PROGRESS/BRIEFING/RESEARCH_HISTORY commit.
등록/코드미commit·code/data/응답hash불일치면중단, 완료result있으면재탐색금지.
성과 전 자료 감사 완료(현재 Laya예측·신규성과0):
- BTC/ETH 각각3,287,520분, gap0. legacy4h와BTC14/ETH15봉 차이 보존.
- 공식 분봉으로 만든 canonical4h 사용, 양쪽 평가13698봉씩/재집계내부차이0.
  내부 일치는같은원천의일관성이지독립적가격진실의확인아님.
- funding6,849개씩: 실제 정산mark3,107개씩(총6,214), hour시가근사3,742개씩.
  정산시각/rate의REST일치 확인, 누락된markhour36개는무료REST보완 완료.
- canonical source SHA256 `3a27e3091012641fcae1cb78aa232433d38cd28e52fcdc76bb3932e9914a7e6a`.
  기존 source SHA는이전연구의`2cb4b17b...53e05`, 같은source라고표현하지않는다.
- Laya고정기회230·본요청230+순서32+반복16=278 state, 예상상한금액0.0139USD.
- 신규합성22개통과(누출1개포함), 전체pytest3553/누출110/타입629/스타일958 정상.
  문서링크290 정상. 기존UI import계약1건위반은불변이며 전체검증PASS로표현하지않는다.
결과는 추후 §11에만 추가한다.

## 10. 확인한 원문

- [Binance 시장자료 API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data): fundingRate의정산mark와markPriceKlines 계약.
- [Binance 공개 자료 저장소](https://github.com/binance/binance-public-data): 월별ZIP/checksum·수정이력. 현재보유파일은새SHA/CRC로고정하고당시원본불변을주장하지않는다.
- [현재 이용 중인 Laya 공급자 API 안내](https://laya-ai.com/laya-api): english명시/batch/출력·과금,
  openweight제작자의공식호스팅이아닌독립호스팅이라고밝힌다. 예전프로젝트의“공식API”표현은이번새검증에승계하지않는다.
