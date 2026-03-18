-- Schemas and the one setting the control depends on.

create schema if not exists staging;   -- files as received, then typed
create schema if not exists core;      -- cleaned client master
create schema if not exists pricing;   -- tariff, client overrides, fee functions
create schema if not exists dq;        -- findings on the tariff and the client master
create schema if not exists recon;     -- repriced fees compared with invoices

drop table if exists recon.settings cascade;
create table recon.settings (
    only_row         boolean primary key default true check (only_row),
    amount_tolerance numeric not null default 0.05   -- EUR; differences up to this are rounding, not findings
);
insert into recon.settings default values;
