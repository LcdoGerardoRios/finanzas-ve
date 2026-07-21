-- =====================================================================
-- FINANZAS VE · schema.sql
-- Esquema de base de datos para Supabase (PostgreSQL)
-- Ejecutar completo en: Supabase Dashboard > SQL Editor > New query
-- =====================================================================

-- ---------------------------------------------------------------------
-- Extensiones necesarias
-- ---------------------------------------------------------------------
create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------------------
-- 1) CUENTAS  (wallets: banco VES, banco USD, efectivo, Zinli, etc.)
-- ---------------------------------------------------------------------
create table if not exists cuentas (
    id              bigint generated always as identity primary key,
    nombre          text not null unique,
    moneda_nativa   text not null check (moneda_nativa in ('VES', 'USD', 'EUR')),
    balance_inicial numeric(18,2) not null default 0,
    creado_en       timestamptz not null default now()
);

comment on table cuentas is 'Wallets / cuentas del usuario (banco, efectivo, billeteras digitales)';

-- ---------------------------------------------------------------------
-- 1.b) CATEGORIAS  (categorías de gasto/ingreso, editables desde la app)
-- ---------------------------------------------------------------------
create table if not exists categorias (
    id        bigint generated always as identity primary key,
    nombre    text not null unique,
    tipo      text not null default 'Ambos' check (tipo in ('Gasto', 'Ingreso', 'Ambos')),
    creado_en timestamptz not null default now()
);

comment on table categorias is 'Categorías de transacciones/presupuestos, administrables desde la app (Módulo Ajustes)';

-- ---------------------------------------------------------------------
-- 2) TASAS_CAMBIO  (histórico diario de tasas VES/USD)
-- ---------------------------------------------------------------------
create table if not exists tasas_cambio (
    id             bigint generated always as identity primary key,
    fecha          date not null default current_date,
    tipo_tasa      text not null check (tipo_tasa in ('BCV', 'Binance', 'Mercado')),
    valor_usd      numeric(18,4) not null,          -- cuántos VES equivalen a 1 USD
    creado_en      timestamptz not null default now(),
    actualizado_en timestamptz not null default now(),  -- se refresca en cada UPDATE
    unique (fecha, tipo_tasa)                    -- permite upsert diario por tipo
);

comment on table tasas_cambio is 'Histórico de tasas de cambio VES/USD (BCV, Binance P2P, Mercado paralelo)';

-- Refresca actualizado_en automáticamente cada vez que se actualiza una fila
-- (ej. cuando el script diario hace UPDATE sobre la tasa del día).
create or replace function fn_tocar_actualizado_en()
returns trigger
language plpgsql
as $$
begin
    new.actualizado_en := now();
    return new;
end;
$$;

drop trigger if exists trg_tasas_actualizado_en on tasas_cambio;

create trigger trg_tasas_actualizado_en
before update on tasas_cambio
for each row
execute function fn_tocar_actualizado_en();

-- ---------------------------------------------------------------------
-- 3) TRANSACCIONES  (movimientos: ingreso, gasto, transferencia)
-- ---------------------------------------------------------------------
create table if not exists transacciones (
    id                bigint generated always as identity primary key,
    creado_en         timestamptz not null default now(),
    cuenta_id         bigint not null references cuentas(id) on delete cascade,
    tipo              text not null check (tipo in ('Ingreso', 'Gasto', 'Transferencia')),
    categoria         text not null default 'Sin categoría',
    monto_original    numeric(18,2) not null,
    moneda_original   text not null check (moneda_original in ('VES', 'USD', 'EUR')),
    tasa_usada        numeric(18,4),               -- se autocompleta por trigger si es VES
    monto_usd         numeric(18,2),               -- se autocompleta por trigger
    notas             text,
    -- Solo aplica cuando tipo = 'Transferencia' (incluye "compra de dólares",
    -- que es una transferencia entre una cuenta VES y una cuenta USD con
    -- una tasa de conversión propia, distinta de la tasa BCV).
    cuenta_destino_id bigint references cuentas(id) on delete set null,
    monto_destino     numeric(18,2)                -- monto acreditado en la cuenta destino, en SU moneda
);

comment on table transacciones is 'Movimientos financieros. monto_usd y tasa_usada se calculan automáticamente si no se envían. cuenta_destino_id/monto_destino solo aplican a Transferencias.';

create index if not exists idx_transacciones_cuenta on transacciones(cuenta_id);
create index if not exists idx_transacciones_cuenta_destino on transacciones(cuenta_destino_id);
create index if not exists idx_transacciones_categoria on transacciones(categoria);
create index if not exists idx_transacciones_creado_en on transacciones(creado_en);

-- ---------------------------------------------------------------------
-- 4) PRESUPUESTOS  (límites de gasto por categoría, en USD)
-- ---------------------------------------------------------------------
create table if not exists presupuestos (
    id              bigint generated always as identity primary key,
    categoria       text not null,
    monto_limite_usd numeric(18,2) not null,
    periodo         text not null check (periodo in ('Semanal', 'Mensual')),
    creado_en       timestamptz not null default now(),
    unique (categoria, periodo)
);

comment on table presupuestos is 'Límites de gasto por categoría en USD, semanales o mensuales';

-- ---------------------------------------------------------------------
-- 5) PAGOS_PROGRAMADOS  (cuentas por pagar / recordatorios de pago)
-- ---------------------------------------------------------------------
create table if not exists pagos_programados (
    id                bigint generated always as identity primary key,
    descripcion       text not null,
    monto_original    numeric(18,2) not null,
    moneda_original   text not null check (moneda_original in ('VES', 'USD', 'EUR')),
    monto_usd         numeric(18,2),
    fecha_vencimiento date not null,
    cuenta_id         bigint references cuentas(id) on delete set null,
    categoria         text not null default 'Sin categoría',
    estado            text not null default 'Pendiente' check (estado in ('Pendiente', 'Pagado')),
    creado_en         timestamptz not null default now()
);

comment on table pagos_programados is 'Compromisos de pago futuros. Al marcarse Pagado, generan una transacción tipo Gasto.';

create index if not exists idx_pagos_estado_fecha on pagos_programados(estado, fecha_vencimiento);

-- =====================================================================
-- TRIGGER: cálculo automático de monto_usd / tasa_usada en transacciones
-- =====================================================================
create or replace function fn_calcular_monto_usd()
returns trigger
language plpgsql
as $$
declare
    v_tasa numeric(18,4);
begin
    -- Si el monto_usd ya viene informado manualmente, se respeta tal cual.
    if new.monto_usd is not null then
        return new;
    end if;

    if new.moneda_original = 'USD' then
        new.monto_usd := new.monto_original;
        new.tasa_usada := 1;

    else
        -- VES o EUR: se necesita una tasa. Si no vino en tasa_usada,
        -- se toma la última tasa BCV disponible en tasas_cambio.
        if new.tasa_usada is null then
            select valor_usd
              into v_tasa
              from tasas_cambio
             where tipo_tasa = 'BCV'
             order by fecha desc, creado_en desc
             limit 1;

            if v_tasa is null then
                raise exception 'No hay tasa BCV registrada en tasas_cambio. Registra una tasa antes de crear transacciones en VES.';
            end if;

            new.tasa_usada := v_tasa;
        end if;

        new.monto_usd := round(new.monto_original / new.tasa_usada, 2);
    end if;

    return new;
end;
$$;

drop trigger if exists trg_calcular_monto_usd on transacciones;

create trigger trg_calcular_monto_usd
before insert or update on transacciones
for each row
execute function fn_calcular_monto_usd();

-- Mismo cálculo aplicado a pagos_programados (útil para presupuestos/alertas)
create or replace function fn_calcular_monto_usd_pago()
returns trigger
language plpgsql
as $$
declare
    v_tasa numeric(18,4);
begin
    if new.monto_usd is not null then
        return new;
    end if;

    if new.moneda_original = 'USD' then
        new.monto_usd := new.monto_original;
    else
        select valor_usd into v_tasa
          from tasas_cambio
         where tipo_tasa = 'BCV'
         order by fecha desc, creado_en desc
         limit 1;

        if v_tasa is not null then
            new.monto_usd := round(new.monto_original / v_tasa, 2);
        end if;
    end if;

    return new;
end;
$$;

drop trigger if exists trg_calcular_monto_usd_pago on pagos_programados;

create trigger trg_calcular_monto_usd_pago
before insert or update on pagos_programados
for each row
execute function fn_calcular_monto_usd_pago();

-- =====================================================================
-- SEGURIDAD (RLS) — app 100% personal, un solo usuario
-- =====================================================================
-- Se habilita RLS y se crean políticas abiertas para el rol "anon",
-- que es el que usarán Streamlit (con la anon key) y los Atajos de iOS.
-- IMPORTANTE: esto funciona porque tu URL y anon key de Supabase no son
-- públicas. No las publiques en un repo público ni las compartas.
-- Ver guia_despliegue_paso_a_paso.md para el manejo seguro de claves.

alter table cuentas enable row level security;
alter table categorias enable row level security;
alter table tasas_cambio enable row level security;
alter table transacciones enable row level security;
alter table presupuestos enable row level security;
alter table pagos_programados enable row level security;

create policy "acceso_total_anon_cuentas" on cuentas
    for all using (true) with check (true);

create policy "acceso_total_anon_categorias" on categorias
    for all using (true) with check (true);

create policy "acceso_total_anon_tasas" on tasas_cambio
    for all using (true) with check (true);

create policy "acceso_total_anon_transacciones" on transacciones
    for all using (true) with check (true);

create policy "acceso_total_anon_presupuestos" on presupuestos
    for all using (true) with check (true);

create policy "acceso_total_anon_pagos" on pagos_programados
    for all using (true) with check (true);

-- =====================================================================
-- DATOS DE EJEMPLO (opcional, comenta o borra si no los quieres)
-- =====================================================================
insert into cuentas (nombre, moneda_nativa, balance_inicial) values
    ('Banco Venezuela (VES)', 'VES', 0),
    ('Zelle / Banco USA (USD)', 'USD', 0),
    ('Efectivo', 'USD', 0),
    ('Banesco', 'VES', 0),
    ('Bancamiga', 'VES', 0),
    ('Binance', 'USD', 0),
    ('Banesco USD', 'USD', 0),
    ('BdV USD', 'USD', 0)
on conflict (nombre) do nothing;

insert into tasas_cambio (fecha, tipo_tasa, valor_usd) values
    (current_date, 'BCV', 40.00)
on conflict (fecha, tipo_tasa) do nothing;

insert into categorias (nombre, tipo) values
    ('Comida', 'Ambos'),
    ('Transporte', 'Ambos'),
    ('Servicios', 'Gasto'),
    ('Salud', 'Gasto'),
    ('Entretenimiento', 'Gasto'),
    ('Ropa', 'Gasto'),
    ('Educación', 'Gasto'),
    ('Deporte', 'Gasto'),
    ('KÖMUN (negocio)', 'Ambos'),
    ('Ahorro/Inversión', 'Ambos'),
    ('Compra/Venta de divisas', 'Ambos'),
    ('Salario', 'Ingreso'),
    ('Otros', 'Ambos')
on conflict (nombre) do nothing;

insert into presupuestos (categoria, monto_limite_usd, periodo) values
    ('Comida', 150, 'Mensual'),
    ('Transporte', 40, 'Mensual'),
    ('Entretenimiento', 20, 'Semanal')
on conflict do nothing;

-- =====================================================================
-- FIN DEL SCRIPT
-- =====================================================================
