-- 실거래 기록 DB — Supabase 표 만들기 (2026-10-10)
-- Supabase 대시보드 → SQL Editor → New query 에 이 파일 전체를 붙여 넣고 Run 한 번.
-- 여러 번 실행해도 안전하다 (IF NOT EXISTS).
--
-- 원본은 PC 의 data/live_ledger.db (SQLite). scripts/ledger_sync.py 가 매일 여기로 올린다.
-- RLS 를 켜고 정책을 두지 않는다 → 바깥(publishable / anon 키)에서는 한 줄도 못 읽는다.
-- PC 스크립트의 Secret 키만 RLS 를 건너뛰어 쓴다. 폰 앱을 만들 때 로그인한 나만 읽는 정책을 더한다.

create table if not exists public.ledger_events (
    id          text primary key,            -- 장부 · 주문 번호 · 입금 순서로 만든 고유 번호
    account     text not null,               -- glide · cruise · accumulate
    kind        text not null,               -- buy · sell · deposit
    trade_date  date,
    symbol      text,
    qty         numeric,
    price       numeric,                     -- 1 단위 가격 (currency 단위)
    amount      numeric,                     -- 체결 금액 · 입금액 (currency 단위)
    fee         numeric,
    currency    text,                        -- KRW · USD
    fx          numeric,                     -- 원/달러 (있을 때만)
    status      text,                        -- 체결 · 접수 · 체결 없음 · 입금 기록 …
    note        text,
    updated_at  timestamptz not null default now()
);
create index if not exists ledger_events_date on public.ledger_events (trade_date);

create table if not exists public.daily_snapshots (
    date        date primary key,            -- 한국 날짜
    value       numeric,                     -- 그날 평가액 (원)
    deposits    numeric,                     -- 그날까지 넣은 돈 (원)
    priced      boolean,                     -- false 면 시세 없이 원가로 셈 (추정)
    fx          numeric,
    books       jsonb,                       -- 장부별 {value, deposits, priced}
    at          timestamptz,
    updated_at  timestamptz not null default now()
);

alter table public.ledger_events enable row level security;
alter table public.daily_snapshots enable row level security;

-- 새 프로젝트는 새 표에 API 역할 권한을 자동으로 주지 않는다 → PC 스크립트(Secret 키 = service_role)에만 준다.
-- anon · authenticated 에는 주지 않는다 (바깥에서 못 읽게). 폰 앱 때 authenticated 읽기 + 정책을 더한다.
grant select, insert, update on public.ledger_events to service_role;
grant select, insert, update on public.daily_snapshots to service_role;

-- 해 볼 만한 질문들 (SQL Editor 에서 하나씩):
-- select account, kind, count(*), sum(amount) from ledger_events group by 1, 2 order by 1, 2;
-- select date_trunc('month', trade_date) as month, sum(fee) from ledger_events where fee > 0 group by 1;
-- select date, value, deposits, value - deposits as diff from daily_snapshots order by date;
