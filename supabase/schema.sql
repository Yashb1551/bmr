-- =====================================================================
-- Batch Planner — Supabase / PostgreSQL schema
--
-- Paste this into the Supabase SQL Editor (Project -> SQL Editor -> New
-- query -> Run) to build an empty database ready for the app.
--
-- Running it is OPTIONAL: the app calls SQLAlchemy's create_all() on
-- startup and will create anything missing by itself. This file exists so
-- you can (a) provision the database before the first deploy, (b) review
-- the schema without reading the ORM, and (c) apply the RLS lockdown at
-- the bottom, which create_all() does not do.
--
-- It is written to match Main Codes/batch_planner/models.py exactly —
-- same table names, columns, nullability, and index names — so running it
-- first and letting the app start afterwards produces no conflict and no
-- schema drift. Every statement is IF NOT EXISTS, so it is safe to re-run.
--
-- It creates NO data. The first admin account is seeded by the app on its
-- first run against an empty users table (see batch_planner/seed.py); to
-- bring across an existing local SQLite database instead, use
-- Main Codes/migrate_to_supabase.py.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Master data: equipment, products, recipes, cleaning templates
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS equipment (
    id          VARCHAR NOT NULL,
    category    VARCHAR NOT NULL,        -- Reactor / Dryer / Centrifuge / ...
    subtype     VARCHAR,                 -- GLR / SSR / Vacuum Tray / FBD / NULL
    capacity_l  FLOAT,
    area        VARCHAR NOT NULL,        -- floor / area label
    status      VARCHAR NOT NULL,        -- Active / Down / Retired
    notes       TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS products (
    code          VARCHAR NOT NULL,
    name          VARCHAR NOT NULL,
    batch_size_kg FLOAT NOT NULL,
    recipe_sheet  VARCHAR NOT NULL,      -- -> recipe_sheets.name
    active        BOOLEAN NOT NULL,
    PRIMARY KEY (code)
);

-- One row per product recipe. Kept separate from recipe_stages so an empty,
-- not-yet-filled recipe still exists for the Products page to open.
CREATE TABLE IF NOT EXISTS recipe_sheets (
    name VARCHAR NOT NULL,
    PRIMARY KEY (name)
);

-- One operation of one recipe, in run order (seq).
CREATE TABLE IF NOT EXISTS recipe_stages (
    id                 SERIAL NOT NULL,
    sheet              VARCHAR NOT NULL,  -- -> recipe_sheets.name
    seq                INTEGER NOT NULL,
    name               VARCHAR NOT NULL,  -- operation text, verbatim from the BMR
    op_minutes         FLOAT NOT NULL,
    clean_minutes      FLOAT NOT NULL,
    equipment_ids      VARCHAR NOT NULL,  -- comma-separated equipment.id list
    temperature        VARCHAR NOT NULL,  -- standard, free text ("60-65", "RT")
    actual_temperature VARCHAR NOT NULL,  -- optional manual reference
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_recipe_stages_sheet ON recipe_stages (sheet);

-- One equipment category's cleaning procedure. key = category, or
-- "Dryer-<subtype>" where a category has more than one procedure.
-- "key" is quoted throughout: it is a PostgreSQL keyword (non-reserved, so it
-- would parse bare, but quoting removes the doubt).
CREATE TABLE IF NOT EXISTS ecr_template_sheets (
    "key" VARCHAR NOT NULL,
    PRIMARY KEY ("key")
);

CREATE TABLE IF NOT EXISTS ecr_template_steps (
    id           SERIAL NOT NULL,
    template_key VARCHAR NOT NULL,       -- -> ecr_template_sheets.key
    seq          INTEGER NOT NULL,
    name         VARCHAR NOT NULL,
    op_minutes   FLOAT NOT NULL,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_ecr_template_steps_template_key
    ON ecr_template_steps (template_key);

-- ---------------------------------------------------------------------
-- The schedule: orders -> batches -> allocations + cleaning records
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS orders (
    id              SERIAL NOT NULL,
    product_code    VARCHAR NOT NULL,
    quantity_kg     FLOAT NOT NULL,
    requested_start TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    priority        INTEGER NOT NULL,
    planner         VARCHAR,
    batch_label     VARCHAR,             -- user-entered batch no. for the order
    created_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (product_code) REFERENCES products (code)
);

CREATE TABLE IF NOT EXISTS batches (
    id           SERIAL NOT NULL,
    order_id     INTEGER NOT NULL,
    batch_number INTEGER NOT NULL,       -- 1-based index within the order
    product_code VARCHAR NOT NULL,
    status       VARCHAR NOT NULL,       -- Scheduled / Paused / ...
    label        VARCHAR,                -- order's batch no., suffixed if split
    PRIMARY KEY (id),
    FOREIGN KEY (order_id) REFERENCES orders (id)
);
CREATE INDEX IF NOT EXISTS ix_batches_order_id ON batches (order_id);

-- One stage of one batch occupying one piece of equipment for a window.
-- The equipment is unbookable from op_start through clean_end; the batch
-- itself moves on at op_end.
CREATE TABLE IF NOT EXISTS allocations (
    id           SERIAL NOT NULL,
    batch_id     INTEGER NOT NULL,
    equipment_id VARCHAR NOT NULL,
    stage_name   VARCHAR NOT NULL,
    stage_seq    INTEGER NOT NULL,       -- BMR Op. No. within the batch
    temperature  VARCHAR,                -- actual, drawn per batch
    op_start     TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    op_end       TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    clean_end    TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (batch_id) REFERENCES batches (id),
    FOREIGN KEY (equipment_id) REFERENCES equipment (id)
);
CREATE INDEX IF NOT EXISTS ix_allocations_batch_id ON allocations (batch_id);
CREATE INDEX IF NOT EXISTS ix_allocations_equipment_id ON allocations (equipment_id);

-- Equipment Cleaning Record: one step of one equipment's cleaning sequence
-- for one batch. Documentation trail only — it does not affect when the
-- scheduler considers equipment free (that is allocations.clean_end).
CREATE TABLE IF NOT EXISTS cleaning_steps (
    id             SERIAL NOT NULL,
    batch_id       INTEGER NOT NULL,
    equipment_id   VARCHAR NOT NULL,
    seq            INTEGER NOT NULL,
    step_name      VARCHAR NOT NULL,
    step_start     TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    step_end       TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    last_operation VARCHAR NOT NULL,     -- the batch op right before cleaning
    last_op_no     INTEGER,              -- that op's BMR Op. No.
    PRIMARY KEY (id),
    FOREIGN KEY (batch_id) REFERENCES batches (id),
    FOREIGN KEY (equipment_id) REFERENCES equipment (id)
);
CREATE INDEX IF NOT EXISTS ix_cleaning_steps_batch_id ON cleaning_steps (batch_id);
CREATE INDEX IF NOT EXISTS ix_cleaning_steps_equipment_id ON cleaning_steps (equipment_id);

-- ---------------------------------------------------------------------
-- Accounts and audit trail
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    username         VARCHAR NOT NULL,
    password_hash    VARCHAR NOT NULL,   -- PBKDF2, see batch_planner/security.py
    display_name     VARCHAR NOT NULL,
    role             VARCHAR NOT NULL,   -- Admin / Manager / Planner
    active           BOOLEAN NOT NULL,
    allowed_products VARCHAR,            -- Manager only; comma-separated codes
    PRIMARY KEY (username)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL NOT NULL,
    "timestamp" TIMESTAMP WITHOUT TIME ZONE NOT NULL,  -- quoted: PG keyword
    action      VARCHAR NOT NULL,
    details     TEXT NOT NULL,
    PRIMARY KEY (id)
);

-- =====================================================================
-- Row Level Security
--
-- Supabase automatically exposes everything in the `public` schema over
-- its PostgREST API, reachable with the project's anon key. This app does
-- NOT use that API — it connects straight to Postgres as the `postgres`
-- role, which has BYPASSRLS. So enabling RLS with no policies at all
-- closes the REST/realtime door completely while leaving the app working
-- exactly as before.
--
-- Do not add policies unless you actually want anon/authenticated API
-- access. Access control for the app itself is the login and the role
-- checks in batch_planner/auth.py, not RLS.
-- =====================================================================

ALTER TABLE equipment           ENABLE ROW LEVEL SECURITY;
ALTER TABLE products            ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe_sheets       ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe_stages       ENABLE ROW LEVEL SECURITY;
ALTER TABLE ecr_template_sheets ENABLE ROW LEVEL SECURITY;
ALTER TABLE ecr_template_steps  ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders              ENABLE ROW LEVEL SECURITY;
ALTER TABLE batches             ENABLE ROW LEVEL SECURITY;
ALTER TABLE allocations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE cleaning_steps      ENABLE ROW LEVEL SECURITY;
ALTER TABLE users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log           ENABLE ROW LEVEL SECURITY;
