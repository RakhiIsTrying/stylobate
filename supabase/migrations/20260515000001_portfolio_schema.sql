-- 20260515000001_portfolio_schema.sql
-- Phase 3A: Portfolio + Watchlist tables with RLS.
-- Builds on the user_profiles table from the initial schema.

set search_path = public;

-- ============================================================================
-- portfolios
-- ============================================================================
create table if not exists portfolios (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references user_profiles(id) on delete cascade,
  name          text not null,
  base_currency char(3) not null default 'USD',
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

create index if not exists portfolios_user_id_idx on portfolios (user_id);

alter table portfolios enable row level security;

drop policy if exists "portfolios_owner_all" on portfolios;
create policy "portfolios_owner_all"
  on portfolios for all
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- ============================================================================
-- positions
-- ============================================================================
create table if not exists positions (
  id            uuid primary key default gen_random_uuid(),
  portfolio_id  uuid not null references portfolios(id) on delete cascade,
  ticker        text not null,
  market        text not null check (market in ('US','IN','CRYPTO')),
  asset_class   text not null check (asset_class in ('equity','etf','crypto')),
  quantity      numeric(20, 8) not null check (quantity > 0),
  cost_basis    numeric(20, 4),
  currency      char(3) not null,
  opened_at     date,
  created_at    timestamptz default now(),
  unique (portfolio_id, ticker, market)
);

create index if not exists positions_portfolio_id_idx on positions (portfolio_id);

alter table positions enable row level security;

-- Positions owned via portfolio -> user
drop policy if exists "positions_owner_all" on positions;
create policy "positions_owner_all"
  on positions for all
  using (
    exists (
      select 1 from portfolios p
      where p.id = positions.portfolio_id and p.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from portfolios p
      where p.id = positions.portfolio_id and p.user_id = auth.uid()
    )
  );

-- ============================================================================
-- tax_lots (unused in 3A; required by 3B Portfolio Strategist)
-- ============================================================================
create table if not exists tax_lots (
  id            uuid primary key default gen_random_uuid(),
  position_id   uuid not null references positions(id) on delete cascade,
  qty           numeric(20, 8) not null check (qty > 0),
  price         numeric(20, 4) not null check (price >= 0),
  currency      char(3) not null,
  acquired_at   date not null
);

create index if not exists tax_lots_position_id_idx on tax_lots (position_id);

alter table tax_lots enable row level security;

drop policy if exists "tax_lots_owner_all" on tax_lots;
create policy "tax_lots_owner_all"
  on tax_lots for all
  using (
    exists (
      select 1 from positions po join portfolios pf on pf.id = po.portfolio_id
      where po.id = tax_lots.position_id and pf.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from positions po join portfolios pf on pf.id = po.portfolio_id
      where po.id = tax_lots.position_id and pf.user_id = auth.uid()
    )
  );

-- ============================================================================
-- watchlists
-- ============================================================================
create table if not exists watchlists (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references user_profiles(id) on delete cascade,
  name        text not null,
  created_at  timestamptz default now()
);

create index if not exists watchlists_user_id_idx on watchlists (user_id);

alter table watchlists enable row level security;

drop policy if exists "watchlists_owner_all" on watchlists;
create policy "watchlists_owner_all"
  on watchlists for all
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- ============================================================================
-- watchlist_items
-- ============================================================================
create table if not exists watchlist_items (
  watchlist_id uuid not null references watchlists(id) on delete cascade,
  ticker       text not null,
  market       text not null check (market in ('US','IN','CRYPTO')),
  added_at     timestamptz default now(),
  notes        text,
  primary key (watchlist_id, ticker, market)
);

alter table watchlist_items enable row level security;

drop policy if exists "watchlist_items_owner_all" on watchlist_items;
create policy "watchlist_items_owner_all"
  on watchlist_items for all
  using (
    exists (
      select 1 from watchlists w
      where w.id = watchlist_items.watchlist_id and w.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from watchlists w
      where w.id = watchlist_items.watchlist_id and w.user_id = auth.uid()
    )
  );
