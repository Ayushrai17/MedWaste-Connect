# MedWaste Connect — Local Demo & Presentation Guide

This guide provides step-by-step instructions for running a reliable, fault-tolerant local demonstration and evaluator presentation of **MedWaste Connect** on Windows 10/11 using PowerShell.

---

## 1. Environment & Setup Prerequisites

- **Operating System:** Windows 10 / 11 (tested with Windows PowerShell 5.1 / PowerShell 7)
- **Python:** Python 3.11, 3.12, 3.13, or 3.14 (Verified on Python 3.14.7)
- **Web Browser:** Google Chrome, Microsoft Edge, or Mozilla Firefox (Modern HTML5 with camera permissions for barcode scanning)
- **Terminal:** PowerShell or Command Prompt running as normal user (no Administrator privileges required)

---

## 2. Windows PowerShell Setup Commands

### Step A: Navigate to the Project Root
```powershell
cd "<path-to-repository>\Medical_Wastemanagment"
```

### Step B: Virtual Environment Activation
Activate the existing virtual environment:
```powershell
.\venv\Scripts\Activate.ps1
```
*(If PowerShell restricts script execution, run: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and re-run the activation script)*

### Step C: Verify Dependencies
Ensure all core packages are installed:
```powershell
pip install -r requirements.txt
```

### Step D: Environment Configuration
Verify that `.env` exists. If configuring on a new machine, copy the template:
```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```
Create a local `.env` file by copying `.env.example` and generating a unique local `SECRET_KEY`:
```ini
SECRET_KEY=<generate-a-strong-random-50-character-key>
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost,testserver
```

### Step E: Database Safety & Backup
Before any demonstration, create an instantaneous snapshot backup of the SQLite database:
```powershell
Copy-Item db.sqlite3 db.sqlite3.backup -Force
```
*To restore at any point: `Copy-Item db.sqlite3.backup db.sqlite3 -Force`*

### Step F: Apply Database Migrations
Ensure all database tables are synchronized:
```powershell
python manage.py migrate
```

### Step G: Demo-Data Verification & Seeding
Verify or refresh the 4 institutional demo accounts and the approved treatment plant:
```powershell
python manage.py seed_users
```
*(Note: `seed_users` is 100% idempotent and safe to rerun; it uses `get_or_create` and will not duplicate records)*

### Step H: Start the Development Server
```powershell
python manage.py runserver 127.0.0.1:8000
```

### Step I: Local URL
Open your web browser and navigate to:
[http://127.0.0.1:8000/](http://127.0.0.1:8000/)

---

## 3. Demonstration Accounts by Role

All 4 stakeholder roles can be seeded locally when `DEBUG=True` using `python manage.py seed_users`:
- Set `MEDWASTE_DEMO_ADMIN_PASSWORD`, `MEDWASTE_DEMO_HOSPITAL_PASSWORD`, `MEDWASTE_DEMO_COLLECTOR_PASSWORD`, and `MEDWASTE_DEMO_FACILITY_PASSWORD` in your local `.env` file, **or**
- Omit those variables to let `seed_users` generate fresh random one-time passwords and print them in your local terminal output.

| Role | Username | Password Configuration | Organization | Portal Landing URL |
| :--- | :--- | :--- | :--- | :--- |
| **Administrator** | `admin_demo` | `MEDWASTE_DEMO_ADMIN_PASSWORD` or generated on seed | Central Health Directorate | `/accounts/dashboard/admin/` |
| **Hospital Staff** | `hospital_demo` | `MEDWASTE_DEMO_HOSPITAL_PASSWORD` or generated on seed | Apex Super-Specialty Hospital | `/accounts/dashboard/hospital/` |
| **Waste Collector** | `collector_demo` | `MEDWASTE_DEMO_COLLECTOR_PASSWORD` or generated on seed | EcoLogistics Transport Fleet #4 | `/accounts/dashboard/collector/` |
| **Treatment Facility** | `facility_demo` | `MEDWASTE_DEMO_FACILITY_PASSWORD` or generated on seed | Metro Biomedical Incineration Plant | `/accounts/dashboard/facility/` |

---

## 4. Recommended 10–15 Minute Evaluator Demonstration Sequence

### Phase 1: Public Experience & Design Architecture (2 Minutes)
1. **Homepage Showcase (`/`)**:
   - Point out the approved cinematic 2.5D healthcare scene, ambulance/van animation, and 4 statutory biohazard bins.
   - Show the strict 6-color healthcare design palette (`#005B50`, `#007A68`, `#071F1D`, `#F36B21`, `#F7F7F5`, `#EEF1EF`).
   - Highlight the live India GIS analytics map showing medical hubs.
   - Click the **Role Portals** pill in the navbar.

### Phase 2: Hospital Waste Generation & Dynamic QR (3 Minutes)
1. Log in as `hospital_demo` (using your configured `MEDWASTE_DEMO_HOSPITAL_PASSWORD` or generated seed output).
2. Land on the **Hospital Waste Inventory Dashboard** (`/waste/`).
3. Click **Register Waste Batch** (`/waste/register/`):
   - Select Ward (e.g. `ICU Ward - 3rd Floor`), Department (`Intensive Care`).
   - Register a **Yellow** anatomical bag (`4.50 kg`) and a **Red** plastic recyclable bag (`6.00 kg`).
   - Click **Submit Registration**.
4. Show the automatically generated Bag ID (e.g., `MWC-BAG-20261111-XXXXXX`) and the high error-correction QR code.
5. Click **Print Label**: Show the thermal label layout formatted with `@media print` for **100mm × 75mm** adhesive barcode printers.
6. Click **Request Collection** (`/collection/requests/new/`):
   - Select the newly registered bags.
   - Choose pickup date and Morning window. Submit the request.

### Phase 3: Platform Directorate Dispatch (2 Minutes)
1. Log out or open an Incognito tab; log in as `admin_demo` (using your configured `MEDWASTE_DEMO_ADMIN_PASSWORD` or generated seed output).
2. Show the **Central Admin Directorate Dashboard** (`/accounts/dashboard/admin/`):
   - Review platform KPIs (registered facilities, active custody bags, pending approvals).
3. Navigate to **Collection Logistics** (`/collection/admin/`):
   - View the hospital's newly submitted request.
   - Click **Schedule & Assign**: Confirm pickup date/time, assign `collector_demo`, and designate `Metro Biomedical Incineration Plant`.
   - Submit assignment.

### Phase 4: Waste Collector Pickup & Digital Tare Verification (3 Minutes)
1. Log in as `collector_demo` (using your configured `MEDWASTE_DEMO_COLLECTOR_PASSWORD` or generated seed output).
2. Land on the **Logistics Route Dashboard** (`/collection/collector/`):
   - View the newly assigned pickup order. Click **Accept Assignment**, then **Start Collection Trip**.
3. Access the **Collection Scanner** (`/tracking/scan/`):
   - Scan or enter the Bag ID. Show digital barcode verification confirming the bag is on the manifest.
4. Click **Complete Pickup**: The system logs digital handover `MWC-HND-XXXX`, updating custody status to `IN_TRANSIT`.

### Phase 5: Treatment Facility Reception & Disposal (3 Minutes)
1. Log in as `facility_demo` (using your configured `MEDWASTE_DEMO_FACILITY_PASSWORD` or generated seed output).
2. Land on the **CBWTF Plant Dashboard** (`/treatment/facility/dashboard/`).
3. Navigate to **Incoming Manifests** (`/treatment/facility/incoming/`):
   - Open the arriving manifest. Click **Receive Consignment**.
   - Inspect individual bags at the receiving bay. Click **Verify & Accept** for treatment.
4. Navigate to **Treatment Batches** (`/treatment/facility/treatment/new/`):
   - Select **High-Temperature Incineration (>1050°C)** for the Yellow bag.
   - Input operational parameters (Primary Chamber: 850°C, Secondary Chamber: 1050°C).
   - Start the batch, then conclude it.
5. Record **Residue Final Disposal**:
   - Document residual ash transfer to authorized hazardous waste secure landfill.
   - Click **Print Certificate**: Show the A4 statutory Disposal Certificate layout.

### Phase 6: Chain of Custody, Regulatory Returns & Security Audit (2 Minutes)
1. Log in as `admin_demo`.
2. Inspect **Chain of Custody Timeline** (`/tracking/`):
   - Select the treated bag: Showcase the complete chronological audit trail logging all 16 custody milestones from creation to verified disposal.
3. Test **Public QR Tracking** (`/waste/track/<qr_token>/`):
   - Demonstrate privacy sanitization: Only statutory validation fields are displayed. Ward names, staff identities, and internal IDs are completely hidden.
4. Show **Compliance & Statutory Reports** (`/reports/`):
   - Generate official **CPCB Form IV** (Annual Bio-Medical Waste Return) and **Form II** (Accident Log).
5. Open **Unified Security Audit Ledger** (`/accounts/admin/audit-logs/`):
   - Show immutable administrative, facility, and custody action logs.
   - Click **Export CSV**: Open downloaded CSV to confirm formula injection defense (`'` prepended to `=`, `+`, `-`, `@`).

---

## 5. Important Pages to Show Evaluators

| Feature Area | URL | Evaluation Highlight |
| :--- | :--- | :--- |
| **Landing Scene** | `/` | Cinematic 2.5D animation, floating navbar, Leaflet GIS map. |
| **Hospital Dashboard** | `/accounts/dashboard/hospital/` | CPCB color segregation KPIs, bag inventory search, weight totals. |
| **Printable Label** | `/waste/bags/<bag_id>/label/` | `@media print` thermal 100mm × 75mm sticker format with QR. |
| **Collection Dispatch** | `/collection/admin/` | Route scheduling, collector overlap detection, facility allocation. |
| **Collector Portal** | `/collection/collector/` | Mobile-responsive dispatch cards, scan validation dock. |
| **Facility Operations**| `/treatment/facility/dashboard/` | Plant capacity gauge, autoclave/incinerator batch staging. |
| **Disposal Certificate**| `/treatment/facility/disposals/` | Official statutory compliance document with verification stamp. |
| **Custody Timeline** | `/tracking/` | Immutable end-to-end custody events with timestamps & actors. |
| **CPCB Form IV** | `/reports/form-iv/` | Statutory annual return compliant with Bio-Medical Waste Rules 2016. |
| **Audit Ledger** | `/accounts/admin/audit-logs/` | 5-source chronological audit browser with CSV export. |

---

## 6. Common Local Setup Errors & Troubleshooting

| Error Symptom | Cause | Proven Solution |
| :--- | :--- | :--- |
| `DisallowedHost: Invalid HTTP_HOST header: 'testserver'` | Host not in `ALLOWED_HOSTS` | Ensure `.env` contains `ALLOWED_HOSTS=127.0.0.1,localhost,testserver`. |
| `Execution of scripts is disabled on this system` | Windows PowerShell ExecutionPolicy | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the current terminal. |
| `Port 8000 is already in use` | Another process holds port 8000 | Run `python manage.py runserver 127.0.0.1:8080` or kill the process: `Get-Process python \| Stop-Process`. |
| `PermissionDenied (403)` on login | User account is suspended | Suspended users are denied dashboard access. Reactivate via Admin at `/accounts/admin/users/` or re-run `python manage.py seed_users`. |
| `Managed facility: None` for Facility role | Demo plant not linked | Run `python manage.py seed_users` to initialize the pre-approved demo plant. |

---

## 7. How to Stop and Restart the Application

- **To Stop the Server:** Press `CTRL + C` or `CTRL + BREAK` in the PowerShell terminal running `runserver`.
- **To Restart:** Run `python manage.py runserver 127.0.0.1:8000`.
- **To Clean Up Test Artifacts:** Delete scratch files in the brain directory without modifying project source code.

