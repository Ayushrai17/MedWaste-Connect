# MedWaste Connect — Manual Testing Bugfix, Dashboard Discrepancy Fix & Footer Contrast Report

**Date:** 2026-11-11  
**Stack:** Django 6.1.3 · Python 3.14.7 · SQLite (`db.sqlite3`) · Bootstrap 5.3 · Windows 11  
**Final Verification Status:** `PASS` (186/186 Django tests passing; zero pending migrations; headless Chrome verified across viewports and roles)

---

## 1. Summary of Issues Addressed

| Area | Root Cause | Resolution | Verification Status |
| :--- | :--- | :--- | :--- |
| **Bug 1: Collection Request Create Crash** | `CollectionRequestCreateForm.__init__` accessed nonexistent `User.address` (`AttributeError`). | Updated `collection/forms.py` to use `getattr(hospital_user, "address", "")` and fall back to the hospital's latest non-empty `CollectionRequest.pickup_address`. | `PASS` (pytest/unittest + live Chrome `GET /collection/requests/new/` HTTP 200) |
| **Bug 2: Obsolete Step 1–3 Dashboard Placeholder Copy** | `templates/accounts/dashboard.html` rendered placeholder copy (`"coming soon"`, `"Future modules"`, `"Step 3"`, `"Planned Waste Lifecycle"`). | Rebuilt [`hospital_dashboard`](accounts/views.py), [`collector_dashboard`](accounts/views.py), [`facility_dashboard`](accounts/views.py), and [`templates/accounts/dashboard.html`](templates/accounts/dashboard.html) with live KPI cards, recent operational records, pending-approval status handling, and direct module actions. | `PASS` (0 stale text matches across `templates/**/*.html`; live Chrome verified across all 4 roles) |
| **Bug 2 Follow-up: Hospital Dashboard ImportError** | [`hospital_dashboard`](accounts/views.py) imported non-existent `DiscrepancyReport` from `tracking.models`. | Investigated [`CustodyDiscrepancy`](tracking/models.py) in `tracking/models.py` & `tracking/migrations/0001_initial.py`; updated import and hospital-scoped open discrepancy queryset (`bag__hospital`, `collection_request__hospital`, `reception_manifest__collection_request__hospital`, statuses `OPEN`/`UNDER_REVIEW` with `.distinct().count()`). Audited all `.py` files for any stale references. | `PASS` (regression tests + live Chrome `/accounts/dashboard/hospital/` HTTP 200) |
| **Bug 3: Float Precision Noise & KPI Card Overflow** | Aggregated SQLite weights rendered long float tails (`1.1000000000000000`) and risked overflowing `.kpi-card`. | Added [`quantize_weight`](core/utils.py) (`ROUND_HALF_UP` to `Decimal("0.01")`) across backend aggregators, applied `\|floatformat:2` across inventory/analytics/regulatory templates, and added responsive `clamp(...)` + overflow-safe rules in [`static/css/main.css`](static/css/main.css). | `PASS` (regression tests + live Chrome `1.10 KG`, `75.00 KG`, `108.15 kg`, `0` KPI overflows) |
| **Bug 4: Floating Public Navbar Overlapping Dashboard Header** | Public `.app-navbar-header` overlapped sticky `.dashboard-portal-header` on dashboard layouts. | Wrapped site navbar inside `{% block site_navbar %}` in `templates/base.html`, cleared it in `templates/dashboard_base.html`, kept `.dashboard-portal-header` `sticky-top` with `#FFFFFF` background, and constrained `.dashboard-main` overflow in [`static/css/main.css`](static/css/main.css). | `PASS` (Chrome `1920x1080`, `1366x768`, `1024x768` at scroll `0` & `400px`: `topOverlap=False`, `horizOverflow=False`) |
| **Bug 5: New Hospital Registration Visibility & Approval Workflow** | `register()` ignored `PlatformSettings.hospital_requires_approval`, sent no Admin notifications, and admin/analytics views filtered out or rendered empty `h.name` labels for self-registered active hospitals. | Updated [`register`](accounts/views.py) & [`role_required`](accounts/views.py) to honor `hospital_requires_approval` (`PENDING_APPROVAL` blocks operational modules with 403 until approved while allowing dashboard status banner) and call `notify_administrators`; updated Admin overview, approvals queue, and `/reports/admin/` hospital dropdown (`organization_name\|default:username`). | `PASS` (regression tests + live Chrome self-registered hospital visible in Admin overview & `/reports/admin/` dropdown) |
| **Footer Badge Contrast Fix** | Two footer pill badges used `bg-secondary-subtle text-white-50` (light translucent text on light grey background). | Replaced utility classes with `.footer-pill-badge` in [`templates/partials/footer.html`](templates/partials/footer.html) and styled with `#005B50` (`var(--dark-green)`), `#007A68` hover (`var(--emerald)`), `#FFFFFF` text/icons, and pill wrapping in [`static/css/main.css`](static/css/main.css). | `PASS` (Chrome desktop `1366x768` & mobile `390x844` verified) |

---

## 2. Hospital Dashboard Import Fix Details (`CustodyDiscrepancy`)

- **Root Cause:** `accounts/views.py` inside `hospital_dashboard` executed `from tracking.models import DiscrepancyReport`, raising `ImportError: cannot import name 'DiscrepancyReport' from 'tracking.models'` at runtime on `GET /accounts/dashboard/hospital/`.
- **Actual Tracking Discrepancy Model:** `CustodyDiscrepancy` defined in [`tracking/models.py`](tracking/models.py) and [`tracking/migrations/0001_initial.py`](tracking/migrations/0001_initial.py), linking optionally to `bag` (`waste.WasteBag`), `collection_request` (`collection.CollectionRequest`), and `reception_manifest` (`treatment.FacilityReceptionManifest`) with statuses `OPEN`, `UNDER_REVIEW`, `RESOLVED`, and `CLOSED`.
- **Project-Wide Import Audit:** Searched all `.py` files for `DiscrepancyReport`, `CustodyDiscrepancy`, and `from tracking.models` / `import tracking.`. Zero stale references exist anywhere else in the codebase.
- **Scoped Queryset Applied in [`hospital_dashboard`](accounts/views.py):**
  ```python
  open_discrepancies_count = (
      CustodyDiscrepancy.objects.filter(
          Q(bag__hospital=request.user)
          | Q(collection_request__hospital=request.user)
          | Q(reception_manifest__collection_request__hospital=request.user),
          status__in=[
              CustodyDiscrepancy.Status.OPEN,
              CustodyDiscrepancy.Status.UNDER_REVIEW,
          ],
      )
      .distinct()
      .count()
  )
  ```
- **Schema / Migrations:** No schema changes or migrations were created or required.

---

## 3. Files Modified During Stabilization Pass

- [`accounts/views.py`](accounts/views.py) — `register`, `role_required(..., allow_pending=False)`, `hospital_dashboard` (`CustodyDiscrepancy` import & scoped queryset + live hospital KPIs), `collector_dashboard`, `facility_dashboard`
- [`templates/accounts/dashboard.html`](templates/accounts/dashboard.html) — Replaced all placeholder sections with real role-specific operational KPIs, quick actions, pending verification notice, and live recent records
- [`templates/reports/regulatory_reports.html`](templates/reports/regulatory_reports.html) & [`templates/reports/regulatory_report_print.html`](templates/reports/regulatory_report_print.html) — Added `|floatformat:2` weight formatting filters
- [`templates/partials/footer.html`](templates/partials/footer.html) & [`static/css/main.css`](static/css/main.css) — Replaced low-contrast footer badge styling with `.footer-pill-badge` (`#005B50` background, `#FFFFFF` text/icons, `#007A68` hover)
- Prior pre-resume audit fixes preserved in workspace: `core/utils.py`, `collection/forms.py`, `collection/models.py`, `collection/views.py`, `treatment/models.py`, `tracking/views.py`, `waste/views.py`, `accounts/views_admin.py`, `reports/services/analytics_engine.py`, `reports/services/regulatory_reports.py`, `templates/base.html`, `templates/dashboard_base.html`, `templates/waste/inventory.html`, `templates/accounts/admin/dashboard_overview.html`, `templates/accounts/admin/approvals_queue.html`, `templates/accounts/admin/hospital_list.html`, `templates/reports/admin_analytics.html`, `templates/reports/hospital_analytics.html`, `templates/reports/collector_analytics.html`, `templates/reports/facility_analytics.html`, `templates/tracking/tracking_dashboard.html`, `templates/collection/hospital/request_list.html`
- Regression tests updated/added in [`core/tests.py`](core/tests.py), [`accounts/tests.py`](accounts/tests.py), [`collection/tests.py`](collection/tests.py), and [`waste/tests.py`](waste/tests.py)

---

## 4. Verification & Test Results (Executed After Final Edits)

### Django CLI Checks
- `.\venv\Scripts\python.exe manage.py check` → `System check identified no issues (0 silenced).`
- `.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run` → `No changes detected`
- `.\venv\Scripts\python.exe manage.py test --verbosity=2` → `Ran 186 tests in 657.071s` — **`OK`** (0 failures, 0 errors, 0 skipped)

### Headless Chrome Live Browser Verification (`http://127.0.0.1:8000/`)
- **Dashboard & Portal Suite (`verify_browser.mjs`):** 28 viewport/route checks (`1920x1080`, `1366x768`, `1024x768`) across the self-registered hospital (`/accounts/dashboard/hospital/`, `/waste/`, `/collection/requests/new/`, `/collection/requests/`), `admin_demo` (`/accounts/dashboard/admin/`, `/accounts/dashboard/admin/approvals/`, `/reports/admin/`), `collector_demo` (`/accounts/dashboard/collector/`), `facility_demo` (`/accounts/dashboard/facility/`), and public `/` — all returned HTTP `200`, `topOverlap=False`, `horizOverflow=False`, `kpiOverflows=[]`, `hasFloatNoise=False`, `hasPlaceholderCopy=False`, `hasException=False`.
- **Footer Badge Contrast Check (`verify_footer.mjs` at `1366x768` desktop & `390x844` mobile):** Both pills computed `backgroundColor: rgb(0, 91, 80)` (`#005B50`), `color: rgb(255, 255, 255)`, `iconColor: rgb(255, 255, 255)`, `borderRadius: 800px`, zero horizontal overflow (`scrollWidth <= innerWidth`).

### Protected Assets & Database Integrity Check
- **SQLite Records Preserved:** Existing self-registered active hospital record (`HOSPITAL`, `ACTIVE`, `is_active=True`), local verification batch/bag records (`Decimal('1.10')`), and `PlatformSettings(hospital_requires_approval=False)` remained untouched.
- **Locked Homepage Files Untouched (`LastWriteTime` verified):** `templates/partials/hero-scene.html`, `static/css/hero-animation.css`, `static/js/hero-animation.js`, `static/img/hero/*`, `templates/core/home.html`, and `core/views.py` all preserve their `2026-11-10` modification timestamps.

