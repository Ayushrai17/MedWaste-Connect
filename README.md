# MedWaste Connect

A Django-based biomedical waste management and chain-of-custody tracking platform designed to manage hospital waste registration, QR-based container identification, pickup collection requests, custody auditing, treatment facility reception, treatment batch logging, and final disposal documentation.

---

## Project Overview

**MedWaste Connect** digitally connects healthcare generators, waste transport collectors, biomedical treatment facilities, and platform administrators in a single multi-role web application aligned with biomedical waste segregation and compliance workflows.

---

## Key Features (Implemented)

- **Role-Based Access Control (RBAC):** Dedicated authentication, routing, and view-level authorization (`@role_required`) for **ADMIN**, **HOSPITAL**, **COLLECTOR**, and **FACILITY** accounts, including configurable hospital registration approval (`PlatformSettings.hospital_requires_approval`) and administrative suspension controls.
- **Biomedical Waste Registration & Inventory:** CPCB Schedule I color-coded waste categorization (`YELLOW`, `RED`, `WHITE`, `BLUE`), batch waste registration (`WasteRegistrationBatch`, `WasteBag`) with decimal weight validation (`0.05 kg` – `25.00 kg`), searchable/filterable Hospital Waste Inventory Dashboard, and formula-injection-safe CSV exports.
- **QR Code Generation & Verification:** Automatic generation of UUID-backed PNG QR codes (`qrcode` + `Pillow`), printable bag/batch labels, authorized bag details, and a privacy-sanitized public verification route (`/waste/track/<uuid:qr_token>/`).
- **Collection Request Management:** Hospitals select eligible uncollected bags, set preferred pickup dates/times and bay addresses, monitor status transitions, or cancel eligible pending requests; administrators review requests, schedule pickups, and assign collectors with schedule overlap prevention.
- **Collector Assignment & Verification:** Collectors view assigned routes, accept or reject assignments with mandatory rejection reasons, mark pickups in progress/collected, and record individual bag verification (`BagVerificationRecord`).
- **Facility Gate Reception:** Authorized treatment facility operators check in arriving consignments (`FacilityReceptionManifest`), record gate weighbridge weights and weight variance against expected dispatch weights, perform individual bag acceptance/rejection inspections (`FacilityBagReceipt`), and track daily facility processing capacity (`TreatmentFacility`).
- **Treatment Batch Management:** Facility staff execute and track treatment batches (`TreatmentBatch`) across `INCINERATION`, `AUTOCLAVING`, `SHREDDING`, `CHEMICAL_DISINFECTION`, and `ENCAPSULATION` workflows (`PENDING` → `IN_PROGRESS` → `COMPLETED` / `FAILED` / `ON_HOLD`).
- **Disposal Record Management:** Final disposal documentation (`DisposalRecord`) linked to completed treatment batches, administrative verification, and printable disposal certificates/summaries.
- **Chain-of-Custody Tracking & Discrepancy Management:** Immutable event ledger (`CustodyEvent`), handover logs (`CustodyHandover`), and formal exception tracking (`CustodyDiscrepancy`) covering missing bags, weight discrepancies, packaging damage, and facility rejections with resolution workflows.
- **In-App Notifications:** Role-targeted event notifications (`Notification`) and administrator broadcast alerts.
- **Analytics & Compliance Report Previews:** Role-specific operational analytics dashboards, compliance monitoring metrics, statutory report previews (Form IV, Form II, category summaries, facility performance, dispatch manifests, discrepancy audits), print views, and sanitized CSV exports.

---

## Technology Stack

Verified project runtime and dependency versions:

- **Backend:** Python `3.14.7`, Django `6.1.3` (`django>=5.0,<7.0`)
- **Configuration & Utilities:** `python-decouple==3.8`, `qrcode==8.2`, `pillow==13.0.0`, `asgiref==3.13.0`, `sqlparse==0.6.1`, `tzdata==2026.6`
- **Database:** SQLite3 (default development database via Django ORM; environment-configurable)
- **Frontend:** Django Templates, Bootstrap `5.3`, Bootstrap Icons, Leaflet / Chart.js (UI charts & maps), Vanilla JavaScript, custom CSS
- **Testing:** Django `TestCase` / `Client` test runner (`unittest`)

---

## Project Structure

```text
MedWaste-Connect/
├── accounts/           # Custom User model, auth, role dashboards, Admin Directorate
├── collection/         # Pickup requests, admin scheduling, collector assignments
├── config/             # Django project settings, root URLs, ASGI/WSGI entry points
├── core/               # Public landing page, shared utilities, core tests
├── facilities/         # Treatment facility registry, licensing & capacity services
├── fleet/              # Registered app scaffold reserved for future fleet extensions
├── hospitals/          # Registered app scaffold reserved for future hospital profiles
├── notifications/      # Database-backed stakeholder notifications & context processor
├── reports/            # Analytics engine, compliance monitor, report previews & CSV export
├── static/             # CSS, JavaScript, and curated UI / hero illustrations
├── templates/          # Modular HTML templates across all portals and error pages
├── tracking/           # Chain-of-custody ledger, handovers & discrepancy management
├── treatment/          # Facility gate reception, treatment batches & disposal records
├── waste/              # Waste categories, registration batches, QR engine & inventory
├── .env.example        # Safe environment variable template (no real secrets)
├── .gitignore          # Excludes venv, .env, local SQLite DBs/backups, media, cache
├── manage.py           # Django administrative CLI entry point
└── requirements.txt    # Python package requirements
```

> **Note:** `hospitals` and `fleet` are registered Django app modules currently retained as minimal scaffolds without standalone models or URL routes.

---

## Installation Instructions

### 1. Clone the Repository
```bash
git clone <your-github-repository-url>
cd MedWaste-Connect
```

### 2. Create and Activate a Virtual Environment
- **Windows (PowerShell):**
  ```powershell
  python -m venv venv
  .\venv\Scripts\Activate.ps1
  ```
- **Linux / macOS:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the safe template and customize values for your environment (a strong `SECRET_KEY` environment variable is strictly required when `DEBUG=False`):
- **Windows (PowerShell):**
  ```powershell
  Copy-Item .env.example .env
  ```
- **Linux / macOS:**
  ```bash
  cp .env.example .env
  ```

### 5. Run Database Migrations
```bash
python manage.py migrate
```

### 6. Create an Administrative User (and Optional Demo Data)
Create an administrator account:
```bash
python manage.py createsuperuser
```
*(Optional for local development only — refused when `DEBUG=False`: set `MEDWASTE_DEMO_*_PASSWORD` in `.env` or omit them to generate random per-run passwords via `python manage.py seed_users`.)*

### 7. Start the Django Development Server
```bash
python manage.py runserver
```
Open `http://127.0.0.1:8000/` in your browser.

---

## Running Tests & System Checks

Verify Django system checks, migration synchronization, and run the automated test suite:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

---

## Demo and Academic Disclaimer

This project is an **academic software demonstration** developed for educational evaluation. All records, weights, treatment statuses, custody timelines, and compliance report previews displayed in this application represent simulated or user-entered application data only; they do not represent independently verified physical biomedical waste disposal, live certified hardware scale telemetry, or official statutory certification from the Central Pollution Control Board (CPCB) or any State Pollution Control Board.

---

## License

No open-source license has been specified for this repository. All rights remain with the project author unless a license is explicitly added later.
