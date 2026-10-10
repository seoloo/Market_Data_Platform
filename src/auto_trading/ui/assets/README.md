# 오비 브랜드 자산

## 승인 원본 오비·라이트 전용 배경 보정 (2026-10-10 최종 후속)

아래 생성 이력보다 우선한다. 사용자 첨부 `ChatGPT 이미지 2026년 10월 10일 오후
02_31_57.png`는 기존 `orbit-hero-light.png`와 바이트 동일한 1536×1024 PNG다.
SHA256: `fabcea516bf0645726c31d1961349f0ad7801397128caa2807a00ab4636e9551`.
라이트 상단에 이 원본을 그대로 사용한다. 다크도 같은 파일·위치·크기이며 CSS mask로
배경 가장자리만 기존 어두운 우주에 연결한다. 얼굴·자세를 생성형으로 다시 그리지 않는다.
기존 `orbit-obi.png`는 보존하지만 현재 실거래 홈의 상단 오비에는 쓰지 않는다.

- `orbit-space-light.png`: 별도로 생성한 라이트 전용 1536×1024 파스텔 하늘·구름·행성
  배경. 다크 PNG의 투명도/색만 바꾼 파일이 아니다. 캐릭터·글자·UI 없음.
- 생성 모드: imagegen 스킬의 내장 이미지 편집 도구, 위 사용자 PNG를 참조,
  `transparent_background=false`. 런타임 생성·새 API 키·CDN 요청 없음.
- 오비 투명 분리도 시도했으나 원본과 세부 표현이 달라 **사용하지 않았다**.
  검산용 미채택 결과는 Git 제외 build/ui-preview에 보관한다. 원본 자산은 모두 보존.

라이트 배경에 사용한 최종 프롬프트:

> Use case: precise-object-edit. Asset type: wide 1536x1024 seamless-feeling light-mode
> desktop app wallpaper. Image 1 is a style/composition reference. Create a separate
> background matching its soft pastel blue sky, airy white clouds, lilac/mint accents,
> sparse four-point stars and gentle stylized planets. Remove the cat and its glowing
> orbital ring COMPLETELY; no characters or mascots anywhere. This is NOT a lightened
> dark outer-space photo. Original bright friendly storybook space illustration, luminous
> sky-blue/ivory/lavender, quiet central areas suitable behind white translucent dashboard
> cards, soft cloud banks near edges and bottom, restrained small planets near corners.
> No words, no letters, no UI, no grids, no logos, no realistic Earth horizon. Maintain
> the reference illustration style.

정적 제공은 새 라이트 배경 경로 1개만 whitelist에 추가하며 HTML export에도 복사한다.
작은 미국 주식 비트맵 대신 로컬 Lucide SVG+색상 배지를 사용한다. 공식 기업 로고라고
표시하지 않으며 종목 이름/설명은 유지한다. 이전 기업 그림 파일과 호환 URL도 보존한다.

## 투명 데스크톱 아이콘 교체 (2026-10-10 후속, 아래 생성 이력보다 우선)

사용자 첨부 `ChatGPT 이미지 2026년 10월 10일 오후 01_42_29.png`는 이미 RGBA이며
바깥 모서리 alpha=0이다. 이미지 뷰어의 검은 표시가 실제 검은 배경이 아니다.
`orbit-icon-source.png`는 바이트 동일 보존 사본(1254×1254), SHA256
`ec3815f2b9abe62cdf103731473a80cf73ed9f58b0d7a4e38337f72351795932`.
생성형 편집/배경 제거/크롭 없이 설치된 Pillow로 포맷 변환·축소만 한다.
`obi.png`·`auto-trading.ico`·`obi.ico`를 RGBA로 교체하고, 바로가기 캐시 분리를 위해
동일 ICO의 새 경로 `orbit-transparent.ico`를 사용한다. ICO 7프레임도 alpha 유지.
exe PE 아이콘을 새 투명 ICO로 재빌드한다. `obi-original.png`와 승인 화면 배경은 보존.
10/09 검은 배경 자산은 Git 이력에 있고 이전 exe는 build/orbit-backup에 보관한다.
이번 후속은 새 이미지 생성이 아니므로 imagegen 호출/프롬프트/API 사용이 없다.

- 사용자 승인 원본: `네온 궤도 속 우주 고양이.png` (2026-10-09).
- `obi-original.png`: 제공 PNG의 바이트 동일 보존 사본(1254×1254 RGB).
  SHA256: `234578d170f91336fb193692bf9d8ac3ef39f96cf5b1f4a58916a68c1bbd40bd`.
- `obi.png`: 원본 전체를 512×512로 Lanczos 축소한 웹 로고·캐릭터.
- `auto-trading.ico` / `obi.ico`: 동일 16·24·32·48·64·128·256px 프레임.
  웹 파비콘/실행 파일은 전자, 바로가기는 캐시 구분을 위해 후자 사용.
- `auto-trading.svg`: 이전 AT 로고의 호환 자산. 현재 HTML의 로고/파비콘에서는 쓰지 않는다.

원본 검은 배경·색·얼굴·네온 궤도를 유지한다. 배경 제거/자동 크롭/생성형 재편집은 없다.
화면의 둥근 모서리는 CSS 표시이며 원본을 바꾸지 않는다.
빌드 환경의 Pillow로 `scripts/build_dashboard_icon.py`를 실행한다.
웹 PNG/ICO는 저장소에 포함하며 런타임에 Pillow나 외부 이미지 URL은 필요 없다.
16px은 복잡한 원본의 축소이므로 세부 별·발광 표현은 줄어든다.
위 내용은 2026-10-09 자산의 생성 이력이다. 승인 원본과 ICO는 그대로 보존한다.

## 오르빗 승인 화면용 자산 (2026-10-10)

사용자가 앱 이름을 **오르빗**으로 확정하고 4번 라이트 화면과 1번의 다크 배경을 선택했다.
`orbit-hero-light.png`, `orbit-space-dark.png`, `orbit-obi.png`는 imagegen 스킬의
내장 이미지 생성 도구로 준비한 로컬 PNG다. API 키·유료 데이터·외부 이미지 URL은 쓰지 않는다.
원본 PNG/ICO를 덮어쓰지 않으며 런타임 이미지 생성도 없다.

최종 프롬프트 요약(각 자산별 독립 호출, UI/숫자/텍스트 생성 금지):

- `orbit-hero-light.png`: 승인한 라이트 시안의 상단 오비·민트 궤도·파스텔 행성/구름을
  artwork-only로 재구성. 오른쪽에 캐릭터, 왼쪽은 HTML 제목·자산 카드용 밝은 여백.
  시안의 얼굴·색·스타일 보존, 새 UI·숫자·말풍선·문구 제외.
- `orbit-space-dark.png`: 1번 관제실 시안에서 UI·글자·카드·차트·캐릭터를 모두 제외하고
  짙은 남색 우주/작은 별/왼쪽 아래 푸른 지구/오른쪽 위 행성 지평선 배경만 재구성.
- `orbit-obi.png`: 기존 승인 오비의 검은 바깥 배경만 실제 alpha로 제거하는 cutout.
  하얀 고양이 귀 헬멧·남색 얼굴·청록 눈·발·민트 궤도 유지. 새 캐릭터·크롭·텍스트 금지.

웹 제공은 정확한 3개 경로 화이트리스트만 추가한다. 자산 요청은 계좌/DB loader를 호출하지
않으며 원본/키/임의 파일은 제공하지 않는다. HTML 내보내기에서도 PNG를 복사해 로컬 참조한다.
