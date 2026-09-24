# Batch Planner

Batch production scheduler for a pharmaceutical API manufacturing unit. Pick a
product and a quantity; it splits the order into batches, finds the earliest
free equipment for every operation of the recipe without disturbing anything
already scheduled, and gives you an estimated completion date/time — along
with an Equipment Cleaning Record (ECR) log for every piece of equipment the
batch used. Includes a live equipment occupancy map, a Gantt timeline of the
whole plant, and a plant-wide equipment cleaning-status dashboard.

## Running it

```powershell
pip install -r requirements.txt
python -m streamlit run "Main Codes\Scheduler.py"
```

(Using `python -m streamlit` instead of bare `streamlit` avoids relying on
Python's `Scripts` folder being on your `PATH`.) Or just double-click
`run_server.bat` in the project root, which runs the same command and also
makes the server reachable from other devices on your network.

All data — equipment, products, **recipes**, ECR cleaning templates, users,
orders, batches, the audit log — lives in one SQL database. With no
configuration that's a local `Database\plant.db` (SQLite), created on first
run. Set `DATABASE_URL` (env var, or a Streamlit secret) to point at Postgres
instead — see **Deploying** below.

If the database has no users it seeds a default admin login:

- **Username:** `admin`
- **Password:** `ChangeMe123!` (override with the `ADMIN_PASSWORD` secret or
  `BATCH_PLANNER_ADMIN_PASSWORD` env var)

Sign in, go to **Users**, and change this password (reset it, or add your own
Admin and delete this one) before giving anyone else access — the default is
in the source, so it isn't a secret.

Pages (left sidebar):
- **Scheduler** — three sections: schedule a new batch (with an optional batch number, shows that batch's full output right after scheduling); look up any already-scheduled batch's same full output by picking it from a dropdown; and a plant-wide Equipment Cleaning Schedule dashboard. See **Equipment Cleaning Records (ECR)** below for what "full output" now includes. A Manager with product access restrictions (see below) only sees their assigned products in the *schedule* dropdown — looking up an existing batch and the cleaning schedule are unrestricted for everyone.
- **Equipment Map** — live occupancy grid, colored by status, grouped by plant block (API B2 / API B3) then floor then equipment category (Reactors first). Unrestricted for every role — always shows the full plant schedule.
- **Gantt Timeline** — full plant schedule, plus a selector to pull up any single order/batch's own timeline across equipment. Unrestricted for every role — always shows the full plant schedule.
- **Equipment** (Admin only) — add/retire/delete equipment, import a master equipment list from a file, or import ECR cleaning-procedure templates from a file.
- **Products** (Admin + Manager) — add, edit, delete products/recipes (pick by product name), or import a whole BMR from a file. Manager's edit rights are limited — see **User roles** below. A restricted Manager's product selector only lists their assigned products.
- **ECR Master** (Admin + Manager) — pick an equipment category's cleaning procedure from a dropdown and edit its steps directly; that edited version becomes what every future batch's ECR log is generated from. See **Equipment Cleaning Records (ECR)** below.
- **Users** (Admin only) — add accounts, reset passwords, change roles, delete accounts, and set per-Manager product access (**Product Access** tab).
- **Batches** (Admin + Manager) — reschedule, pause/resume, or delete a batch that's already on the books. A restricted Manager only sees/manages batches for their assigned products.

Every page requires signing in. A compact **Plant snapshot** (Free / Running
/ Cleaning / Down / Retired equipment counts, right now) sits at the top of
the sidebar on every page — the same live counts that used to only appear
at the bottom of the Scheduler page.

## Deploying to the internet (Streamlit Community Cloud + Supabase)

The app runs unchanged on [Streamlit Community Cloud](https://share.streamlit.io)
(free) with a [Supabase](https://supabase.com) Postgres database (free) holding
all the data. **Community Cloud wipes the container's filesystem on every
restart** — so the database has to be external, and nothing sensitive can be
in the repo.

### What is and isn't in the repo

`.gitignore` excludes the **entire `Database/` folder** — the SQLite file, the
`recipes.xlsx` / `ecr_templates.xlsx` workbooks (legacy; data now lives in
Postgres), and every real BMR/ECR/equipment-list document. The repo is code
only, so it can be **public** safely. Do not commit anything under `Database/`.

### One-time setup

1. **Create a Supabase project**, then get the connection string from
   *Connect* (top bar) → **ORMs / psycopg2**, or Project Settings → Database.
   Supabase offers three; **pick Session pooler**:

   | | host / port | works from Streamlit Cloud? |
   |---|---|---|
   | Direct | `db.<ref>.supabase.co:5432` | ❌ IPv6-only — Streamlit Cloud is IPv4, so this fails to connect |
   | **Session pooler** | `aws-0-<region>.pooler.supabase.com:5432` | ✅ **use this one** |
   | Transaction pooler | `aws-0-<region>.pooler.supabase.com:6543` | ⚠️ works, but is meant for serverless |

   The pooler URIs put the project ref in the *username*, so they look like
   `postgresql://postgres.abcdefgh:PASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres`
   — not `postgres@db...`. Replace `PASSWORD` with your database password
   (Settings → Database → Reset database password if you don't have it); if it
   contains `@ : / ?` or `#`, percent-encode them.

   `sslmode=require` is added automatically if you leave it off, so pasting
   the URI exactly as Supabase gives it is fine.

2. **Create the tables.** Either just start the app (it runs SQLAlchemy's
   `create_all` on startup and creates anything missing), or — to provision
   the database up front and switch on the RLS lockdown — paste
   [`supabase/schema.sql`](supabase/schema.sql) into the Supabase **SQL
   Editor** and run it. The two produce the same schema, so it doesn't matter
   which comes first.

3. **Migrate existing data** from this machine into Supabase (skip this if
   you're starting fresh — the app seeds its own admin account instead):
   ```powershell
   $env:DATABASE_URL = "postgresql://postgres.abcdefgh:PASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
   python "Main Codes/migrate_to_supabase.py"
   ```
   This copies `Database/plant.db` into Postgres — equipment, products,
   recipes, ECR templates, users, orders, batches and the audit log. Source
   files are not touched. (Re-run with `--force` to overwrite the target.)
   Run it from the project root, and check the row counts it prints before
   moving on.

   Recipes and cleaning templates used to live in `recipes.xlsx` /
   `ecr_templates.xlsx` and now live in the database, so there are two
   possible sources. The script takes **SQLite whenever it has rows** and
   only falls back to a workbook for a database old enough to predate those
   tables — the workbooks are frozen at whenever the app stopped writing to
   them, so preferring them would silently roll back every recipe edited in
   the app since. It prints which source it used for each.

4. **Push the code** to a GitHub repo (public is fine — `Database/` is
   git-ignored, so no recipes or batch records go with it):
   ```powershell
   git add -A
   git commit -m "Batch Planner"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
5. **Create the Streamlit app** at <https://share.streamlit.io> → *Create app*
   → *Deploy a public app from GitHub*:
   - **Main file path:** `Main Codes/Scheduler.py`
   - **Advanced → Python version:** `3.12`
   - **Advanced → Secrets** (same keys as
     [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example)):
     ```toml
     DATABASE_URL = "postgresql://postgres.abcdefgh:PASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
     ADMIN_PASSWORD = "something-only-you-know"
     ```
     Secrets live in Streamlit's settings, never in the repo.
6. **Deploy**, sign in, and on **Users** confirm the accounts came across and
   passwords still work. If you started fresh instead of migrating, sign in as
   `admin` with your `ADMIN_PASSWORD` and change it straight away.

If the app comes up with a connection error, it's almost always the
connection string: check you used the **pooler** host (step 1) and that the
username is `postgres.<project-ref>`, not bare `postgres`.

### Day-to-day after deploy

- The live app and your local machine can both point at the same
  `DATABASE_URL` — set it locally too and you're editing the same data.
- All edits made in the UI (recipes, ECR templates, everything) persist in
  Supabase. There are no more Excel files to keep in sync.
- Redeploys (git push) never touch the data.

### Limits on the hosted app

- Legacy **`.doc`** import needs Microsoft Word (Windows only) — it shows a
  clear message and is effectively disabled. **`.docx`** upload/import works.
- The folder-import tabs (*Import from BMR Folder*, *Import Equipment List*,
  *Import ECR Templates*) read files from `Database/…`, which isn't in the
  repo — use them locally, or drag-drop `.docx` files through the per-product
  uploader instead.

### User roles

- **Admin** — full access everywhere: Equipment/Products/ECR Master/Batches/Users, every field of every recipe and ECR template, every product.
- **Manager** — everything a Planner can do, plus: can open Products and edit an *existing* product's **Operation Time**, **Cleaning Time**, and **Actual Temperature** columns (Operation text, Equipment IDs, and Standard Temperature are read-only; adding/deleting operation rows, adding/deleting products, and importing BMRs are hidden entirely); can edit cleaning-step templates on **ECR Master**; can reschedule/pause/resume/delete batches on **Batches**. No access to the Equipment or Users pages (importing BMRs, equipment lists, or ECR templates stays Admin-only).
- **Planner** — can schedule new batches (Scheduler page) and look up any already-scheduled batch's BMR/ECR output at any time, plus the Equipment Map/Gantt Timeline. No edit or delete rights anywhere — can't touch Products, ECR Master, Batches, Equipment, or Users.

Set a user's role from **Users** (Admin only).

### Per-Manager product access

By default a new Manager has **no** product access — an Admin has to explicitly
assign products from **Users** -> **Product Access**: pick the Manager, multiselect
the products they should work on, and Save. Once assigned, that Manager can only:
- start new batches for those products (Scheduler page's schedule dropdown is filtered),
- reschedule, pause/resume, or delete batches for those products (Batches page is filtered),
- edit the recipe for those products (Products page selector is filtered).

This restriction is scoped per Manager, so different Managers can be assigned
different, non-overlapping (or overlapping) product groups. It does **not**
affect viewing — the Equipment Map and Gantt Timeline always show the complete
schedule across every product, for every role, so everyone can see what the
whole plant is doing. Admins and Planners are never restricted by this setting.

### A note on data protection

The application data lives entirely in the SQL database (`DATABASE_URL`, or the
local SQLite file) — **not in the code repo**. `.gitignore` keeps the whole
`Database/` folder out, so the GitHub repo can be public without exposing
recipes, batch records, or the source BMR/ECR documents. Access to the data is
whatever protects your database and your Streamlit app (keep the app's viewer
list restricted).

The only download feature is the **Download BMR / ECR (PDF)** buttons on the
Scheduler page, which export the operation and cleaning grids for one batch
exactly as shown (every column, Actual Temperature included). Otherwise pages
apply mild browser-side deterrents against casual copying (text selection,
right-click, and Ctrl+C/P/S are disabled). Be clear-eyed about what that is
and isn't:
it's a speed bump against casual copy-paste, **not** a way to stop
screenshots. No web page — this one included — can prevent an OS screenshot
tool, a phone camera pointed at the screen, a browser's own print-to-PDF, or
someone opening dev tools. If screenshots of this data are a real concern,
that needs an organizational/device-level control, not a front-end trick.

## Plant layout

Equipment is the real Ultratech India Ltd (Taloja) inventory — 37 items across
4 floors, each with its real ID (`MA/B2/E/0xx`), capacity, and material of
construction (MOC): SRP/SS/Glass-Lined reactors, centrifuges, Multi Mills, a
Jet Mill, a Sifter, Sparkler/Nutsche/Candle filters, Vacuum Tray Dryers, Fluid
Bed Dryers, and a Conta Blender.

Every equipment ID encodes its **plant block** as the second `/`-segment
(`MA/B2/E/026` -> block `B2`) — the Equipment Map groups on this
automatically. Currently everything is block B2; **API B3** shows as an empty
section, ready for its own equipment list later.

Manage equipment any time from **Equipment**; it's real production
data, not demo seed data — nothing auto-repopulates it. Deleting equipment
with schedule history is blocked (use Retired/Down instead) to keep the audit
trail intact.

### Importing a master equipment list

Drop a `.doc` or `.docx` equipment annexure into `Database\Equipment List\`
(directly, or in a per-block subfolder like `Equipment List\B2\` /
`Equipment List\B3\` — both BMR and Equipment List folders are scanned
recursively, one level or several, skipping any `_converted` cache folder)
and it shows up on the **Equipment** page -> *Import Equipment List* with a
**Preview** button, labeled with its path relative to the folder (e.g.
`B3\Annexure-02_Equipment-Instrument List API2.docx`) so same-named files in
different blocks don't collide. It reads name, capacity, MOC, real equipment
ID, and floor (from the document's own floor-section-header rows — either
"Floor-&lt;name&gt;" or "&lt;name&gt; Floor" style) and shows a diff (new vs.
already-exists-will-update) before you confirm.

## Recipes

Recipes live in the database (tables `recipe_sheets` / `recipe_stages`), one
recipe per product, one row per operation in run order, with these fields:

| field | example |
|---|---|
| operation text | Reaction |
| **Operation Time (min)** | 240 |
| **Cleaning Time (min)** | 60 |
| **Equipment IDs** | MA/B2/E/026, MA/B2/E/027 |
| **Standard Temperature** | 60-65 |
| **Actual Temperature** | (blank, or e.g. "62") |

Edit them on the **Products** page → *Edit Product / Recipe*. (Earlier versions
kept this in `Database/recipes.xlsx`; `migrate_to_supabase.py` moves an
existing workbook into the database.)

All times are **whole minutes only** — every rule that computes a duration
(imports and the two examples above) rounds to the nearest minute; nothing
in the recipe or the schedule ever shows a fractional-minute value.

- **Equipment IDs** is a comma-separated list of the exact equipment IDs that
  operation is allowed to use. The scheduler picks whichever one in the list
  is free soonest. This is also how you lock a train together — e.g. only
  list one specific reactor and its own downstream dryer for a
  single-line-only operation.
- **Standard Temperature** is the admin-entered target (a range like "60-65",
  or "RT") — master data, only ever read, never recalculated by code. Only
  genuinely temperature-shaped values (numbers, ranges, "RT", etc.) are ever
  written here — see *Importing a whole BMR file* below.
- **Actual Temperature** here is an *optional* manually-set reference (e.g.
  from historical execution data) — Admin or Manager. Leave it blank and the
  scheduler derives a value from Standard Temperature instead; set it and the
  scheduler uses it as the base instead. Either way, every time a batch is
  actually scheduled, a fresh value within 1-2 degrees (or, for a range,
  uniformly within it) is drawn independently for that batch's own record —
  see **Temperature Actual** on the schedule, below.
- Edit recipes through the **Products** page -> *Edit Product / Recipe* (pick
  the product by **name**, not code — codes are shown alongside for products
  that share a name, e.g. "Bilastine (A004)" vs "Bilastine (A004/I)"). A
  **Manager** account can only edit Operation Time, Cleaning Time, and Actual
  Temperature there — every other field is read-only or hidden (see
  **User roles**, above).
- **Whatever you save here is final** — the recipe editor is the master.
  Nothing (including re-importing the same BMR) silently recalculates or
  overwrites a saved operation's time or temperature; re-importing a product
  that already has manual edits shows a clear warning before it would
  overwrite anything.
- Batch size is per-product (set on the Products page, Admin only). Requested
  quantity is split into `ceil(quantity / batch_size)` batches.

### Importing a whole BMR file

Drop a `.doc` or `.docx` BMR into `Database\BMR\` — directly, or in a
per-block subfolder like `Database\BMR\B2\` / `Database\BMR\B3\` — and it
shows up on the **Products** page -> *Import from BMR Folder* with a
**Preview** button, labeled with its path relative to `BMR\` (e.g.
`B3\BMR- Amlodipine Besilate.doc`). This reads the *entire* document, not
just one table:

- **Product code / name / batch size** are read from the page header table
  ("Product Name | X | Product Code | Y | Batch Size | Z kg"), pre-filled
  into editable fields — confirm or correct them before importing. If the
  header's Batch Size field is a placeholder rather than a real number
  (some templates leave it as a variable, e.g. "A Kg."), it's read as not
  found and defaults to 0 — pre-filled fields still need review either way.
  The header's Product Name is often just the bare substance name even for
  one stage of a multi-stage synthesis (e.g. "Bilastine" for both the
  Stage-I and Stage-II & Final documents) — if the **filename** says which
  stage it is ("...stage-I...", "...Stage-II & final..."), that's appended
  to the name too (e.g. "Bilastine - Stage I"), so two same-named products
  stay distinguishable at a glance, not just by product code. If a document
  uses a different header layout with no recognizable Product Code field at
  all, the code field falls back to the filename — check and correct it
  before confirming.
- **Every operation** across *every* operation-shaped table in the document
  is included, in order — real BMRs split process steps across several
  tables (Manufacturing Process, Multi Mill, Blending, ...), not just one.
  The Operation text is read exactly as written, never reworded.
- **Equipment IDs** are read from real codes appearing anywhere in that row —
  bracketed (`[PR/API/SSR/01]`) or bare (`MA/B2/E/026`), comma-separated —
  and **carry forward** to later operations until a different code appears,
  since real BMRs only restate it when the equipment actually changes.
- **Standard temperature** is read from the row's "Std." column, but only
  kept if it actually looks like a temperature (a number, a range, or a
  short token like "RT") — remarks or other text that ends up in that column
  (e.g. merged-cell bleed-over from an unrelated row) is discarded rather
  than stored as if it were a real value.
- **Cleaning time** defaults to 15 minutes, but *only* for an operation that
  both has equipment assigned **and** whose text actually mentions
  "clean"/"cleaning" (in any form — "cleanliness", "cleaned", etc.);
  everything else is left at 0 rather than assumed. This is what makes
  cleaning time actually count toward the schedule (equipment is blocked
  through `op_end + cleaning_time`, not just `op_end`) — but only where the
  source text gives a real basis for it.
- **Duration** is derived from the operation text itself, checked in order,
  and always rounded to a whole minute:
  1. an explicit duration — "for 8 hours", "for one hour" (spelled-out
     numbers up to twelve are understood) -> that duration directly.
  2. a quantity in kg -> 200 kg = 60 min (0.3 min/kg).
  3. a volume in L -> 400 L = 25 min (0.0625 min/L).
  4. an analysis/QC mention -> LOD = 60 min, Moisture = 40 min, otherwise
     300 min for a complete analysis.
  5. a heating/chilling/cooling mention -> 120 min.
  6. a charge/load/unload mention -> 5 min.
  7. a "check ..." mention -> 20 min.
  8. otherwise 0, flagged "Not detected — set duration manually". A row left
     at 0 that still has equipment assigned isn't scheduled as instantaneous:
     the scheduler (and the ECR generator, for cleaning steps) substitutes a
     fixed **5 min** — see below.
- Section-header rows that land in the Operation column with no real content
  (e.g. a lone "Stage-II") are automatically skipped, not imported as a step
  — and if a saved recipe still has a zero-duration, no-equipment row like
  that, the scheduler skips it at run time too rather than blocking the
  whole order (it only *blocks* on a row that has a real duration but no
  equipment — that's treated as a genuine data gap, not a label).
- Legacy **`.doc`** files are converted to `.docx` via Microsoft Word COM
  automation (needs Word installed on the machine running the app) before
  parsing; converted copies are cached in `Database\BMR\_converted\`.
- Importing only *previews* — nothing is saved until you click **Confirm
  Import**. Review the parsed table (especially the "Not detected" rows and
  any temperature values) before relying on it for real scheduling.

The simpler **Upload a BMR (.docx)** control inside *Edit Product / Recipe*
still exists for a single already-known product — it applies the same
duration rules to one uploaded table, staged into the editor grid for you to
adjust before saving.

### Every batch follows the master BMR's times exactly

**An operation runs for exactly the time its recipe gives it — every batch,
every time.** Nothing varies a duration at schedule time: not the operation
type, not the batch number, not how busy the equipment is. Schedule the same
product twice and the two batches have identical operation lengths; reschedule
a batch and its operations keep the same durations they had before.

The master recipe on the **Products** page is therefore the single source of
truth for timings. If a batch is taking the wrong amount of time, the recipe
is what to correct.

Two things follow from this that are worth being explicit about:

- **Equipment availability never changes an operation's duration.** If the
  equipment is busy the operation waits for a later start time; its length is
  never stretched or shrunk to fit a gap.
- **An operation the recipe leaves at 0 minutes** is not scheduled as
  instantaneous — it would reserve a zero-length slot, which means nothing. It
  gets a fixed **5 minutes** instead. This is a placeholder, not a real
  timing: set the true duration on the Products page for any row where it
  matters. (A row that is *both* zero-duration and has no equipment is treated
  as a section-header label and skipped entirely — see above.)

Cleaning steps work the same way: each step takes exactly the time its ECR
master template gives it. See **Equipment Cleaning Records (ECR)** below.

Note that **Temperature Actual is still drawn per batch** — a range like
"60-65" picks a value within it for each batch's record. That's a recorded
measurement, not a schedule timing, so it doesn't affect when anything runs.

### Batch numbers

The Scheduler page has an optional **Batch number** field (e.g. matching a
BMR's own `UT/A028/07` numbering). If the order splits into multiple
batches, each gets this number suffixed `-1`, `-2`, .... It's stored on the
order/batch.

### Filled BMR after scheduling

Once a batch is scheduled (Scheduler page), its record is shown on screen as
**Op. No. | Operation | Date | Initial Time | Final Time | Temperature
Actual**, computed from the scheduler's actual timeline for that batch. Op.
No. is the operation's sequence number within the batch. **Temperature
Actual** is a fresh value computed per batch from the recipe's Standard (or
manually-set Actual) Temperature — a range like "60-65" draws uniformly
within it, a single value like "25" jitters by 1-2 degrees, and non-numeric
text ("RT") passes through unchanged; it's redrawn independently for every
batch (never reused across batches of the same order), simulating the
natural run-to-run variation a real executed BMR would show. Any row whose
Operation text mentions **QC** or a **sample** is highlighted green — the
same highlighting rule the ECR log below it uses. A **Download BMR (PDF)**
button below the table (and a **Download ECR (PDF)** button below the
cleaning log) exports that grid with every column, Actual Temperature
included. The same view, for any already-scheduled batch, is also available
from the Scheduler page's **View an Existing Batch's Schedule** section.

### Equipment Cleaning Records (ECR)

Below every batch's BMR-style table (both right after scheduling, and when
looking a batch up later) is its **Equipment Cleaning Record (ECR) log** —
one cleaning sequence per distinct piece of equipment the batch actually
used, starting the moment that equipment's *last* use in the batch ends
(end of equipment usage throughout the batch = start of cleaning; when an
equipment ID is reused across several non-adjacent operations, this is the
truly last one by BMR Op. No., not just whichever tied allocation happened
to be read first). Each entry shows which **BMR Op. No.** that final use
was, not the operation text. Each cleaning sequence is computed step-by-step
from that equipment **category's**
imported ECR template (Reactor, Blender, Centrifuge, Dryer, Multi Mill, Jet
Mill, Nutsche Filter, Sparkler Filter, Sifter — one procedure document per
category, disambiguated by subtype only where a category has more than one,
currently just Dryer's Vacuum Tray vs. Fluid Bed variants), using the exact
same Op. No. / Operation / duration reading rules as a BMR (see *Importing a
whole BMR file*, above). Each step takes exactly the time the template gives
it, for every batch — the same rule operations follow (see *Every batch
follows the master BMR's times exactly*, above). Equipment with no imported
ECR template yet falls back
to a single generic "Equipment Cleaning" step using the recipe's own
Cleaning Time instead of erroring out. As with the BMR table, any cleaning
step mentioning **QC** or a **sample** is highlighted green.

These steps are computed once, when the batch is scheduled or rescheduled,
and stored — so a batch's log is a record of what was scheduled at the time,
and editing a template later doesn't rewrite the history of batches already
on the books. Reschedule a batch to pick up an edited template. Batches
scheduled before this feature existed have no ECR log (nothing was computed
for them at the time) — only newly scheduled or rescheduled batches get one.

The ECR log is a documentation trail only: it does **not** change when the
scheduler considers equipment free for its *next* booking — that's still
governed by the recipe's own (usually shorter) Cleaning Time field, exactly
as before. In reality the full cleaning paperwork often takes longer than
that scheduling buffer; this keeps the two concerns separate rather than
quietly changing plant throughput math.

Once its ECR log finishes, equipment is considered **held clean for 72
hours**; past that it needs cleaning again before its next use. The
Scheduler page's **Equipment Cleaning Schedule** section is a plant-wide
dashboard of every active equipment's status — *Clean* (with hours
remaining), *Needs Cleaning* (with hours overdue), *Running* / *Cleaning*
(mid-use right now), or *Never Used* — plus a flag if that equipment's next
*already-scheduled* booking would start after its clean-hold expires.

Import ECR templates from **Equipment** -> *Import ECR Templates*: drop a
`.doc`/`.docx` cleaning procedure into `Database\ECR\` and it appears there
with a Preview button, the same workflow as importing a BMR or an equipment
list.

### ECR Master — editing a cleaning template

An imported ECR template is a starting point, not the final word — open
**ECR Master**, pick a category from the dropdown, and edit its steps
(add/remove/reorder rows, change an operation's text or its time) directly,
the same editable-grid pattern as the Products page's recipe editor. Once
saved, that edited version is the master: every future batch's ECR log is
generated from it, and re-importing that category's original Word document
on the Equipment page will warn you first rather than silently overwriting
your edits (the exact same "edited since import" protection a BMR recipe
gets — see *Importing a whole BMR file*, above).

### Managing already-scheduled batches (Batches page)

- **Reschedule** — drops one batch's current bookings and re-runs it from a
  new start time, slotting around everything else already on the books.
  Only that one batch moves; siblings in the same order and every other
  order are untouched.
- **Pause / Resume** — a status flag for tracking (e.g. on hold for QC). It
  does not release or move the batch's already-booked equipment time —
  reschedule or delete it if you need that time freed.
- **Delete** — removes the batch and its equipment bookings. If it was the
  only batch on its order, the order goes with it; sibling batches are left
  alone.

## Products loaded so far

Products loaded from real Ultratech BMRs via `Database\BMR\`: `A004`
(Bilastine Stage-II & final), `A004/I` (Bilastine Stage-I), `A007`
(Cetirizine Dihydrochloride), `A024` (Glibenclamide), `A028` (Ketoconazole),
`A048` (Vardenafil HCl trihydrate) — all auto-imported with zero
unknown-equipment warnings, meaning every equipment reference in these real
documents matched the master equipment list exactly. `A012` and `A012/I`
(Dorzolamide HCl) were imported earlier the same way and still exist as
products (one has real order history), but their source `.doc` files are no
longer in the BMR folder, so they won't be refreshed by a re-import — edit
them directly on the Products page if they need changes. Plus `API-037`, an
earlier manual transcription of a *different* Ketoconazole master-BMR PDF
(see below).

**`API-037` and `A028` are the same real product (Ketoconazole) under two
different codes.** `API-037` was transcribed by hand from a scanned PDF
before the automated BMR-folder pipeline existed; `A028` (from the later,
cleanly-parseable `.doc` version of the same BMR) is the higher-fidelity
version — real equipment IDs, real temperatures, exact charge quantities —
and is the one real orders now use. `API-037`'s own order history has since
been cleared out via the Batches page; consider retiring it (Products page ->
untick Active) or deleting it (Products page -> Delete Product) now that it
has no schedule history left blocking that.

Many operations in these imports were left at 0 min (no explicit
duration/quantity/volume/keyword match in the source text) — review each
product's recipe on the **Products** page before scheduling real batches
against it.

## Scheduling logic

For each batch, operations run in the order their columns appear in the
sheet. For each operation, the scheduler checks every candidate equipment
ID's existing bookings and finds the earliest gap that fits the duration,
starting no earlier than the previous operation's finish time (or the
requested start, for the first operation). Equipment is blocked from
`op_start` through `op_end + cleaning_time`; the batch itself moves on at
`op_end` without waiting for cleaning. New orders only ever read existing
bookings and append after them — nothing already scheduled is moved or
erased.

## Known assumptions / next steps

- Plant is treated as running continuously (no shift calendar or holidays
  yet). Add a working-calendar table if the real plant isn't 24/7.
- Batches within one order are scheduled one after another (batch 1's full
  routing, then batch 2's, ...); they still pipeline naturally whenever a
  later batch's operation lands on different equipment than an earlier batch
  is using at that time.
- Single-user assumption: concurrent scheduling from two browser tabs at the
  exact same instant isn't locked against. Fine for one planner; would need
  a proper transaction/locking pass for multiple simultaneous users.
- Login is username/password only (PBKDF2-hashed, stored in the database), no
  SSO/2FA. Every admin action (add/delete equipment, products, recipes,
  users, imports) is written to the audit log with the acting username.
- The Manager role's column-level restrictions (Products page) are enforced
  in the UI (disabled grid columns), not by a database-level permission
  check — anyone with direct write access to the database bypasses that.
  Fine for a trusted internal deployment; would need a server-side check for
  anything more adversarial.
  The copy/screenshot deterrents are the same kind of soft control — see
  *A note on data protection*, above.
- BMR duration parsing only reads the *first* matching phrase in an
  operation's text. An operation describing multiple materials/holds in one
  line will only pick up the first one — split those into separate operation
  rows in the source BMR if each needs its own duration.
- The scheduler reserves exactly one equipment pool per operation — it can't
  express "needs the reactor AND the vacuum pump at the same time." Vacuum
  distillation steps only reserve the reactor (vacuum is a building utility
  here, not a discrete blockable asset in the master equipment list).
- Packing-operation sections in some BMRs aren't imported — they don't
  reference any equipment from the master list, so there's nothing for the
  scheduler to reserve there.
- Obvious conditional/contingency steps ("if TLC doesn't comply, maintain
  further") are excluded from scheduled recipes on principle — they're
  exception paths, not part of every batch's standard timeline — but this
  isn't automatically detected across all imports; check each recipe.
- `.doc` file support (both BMR and equipment-list imports) requires
  Microsoft Word installed on the machine running the app (COM automation).
  On a machine without Word, only `.docx` can be imported.
