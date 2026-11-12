# MedWaste Connect — Comprehensive Platform User Guide

Welcome to **MedWaste Connect**, an enterprise-grade digital platform engineered for end-to-end biomedical waste segregation, dynamic QR tracking, chain-of-custody verification, treatment facility lifecycle management, and statutory regulatory compliance according to the **Bio-Medical Waste Management Rules, 2016** (Schedule I, Schedule II, Form II, and Form IV).

---

## 1. System Roles & Access Control

MedWaste Connect operates an authoritative, role-based access model across 4 distinct stakeholders:

| Role | Primary Responsibility | Key Workspaces & Actions |
| :--- | :--- | :--- |
| **HOSPITAL** | Waste Generator | Segregation logging, batch registration, dynamic QR printing, collection requests, hospital inventory. |
| **COLLECTOR** | Transport & Logistics | Assignment review, pickup execution, mobile barcode scan verification, transit tracking, facility handovers. |
| **FACILITY** | Treatment Plant Operator | Manifest reception, bag quarantine/acceptance, treatment batch processing, residue disposal documentation. |
| **ADMIN** | Regulatory Directorate | Platform KPI oversight, user lifecycle control, facility accreditation, discrepancy review, audit ledger, CPCB reporting. |

---

## 2. Hospital Operations Guide

### 2.1 Segregating & Registering Biomedical Waste
1. Log in to your hospital account at `/login` or via the public portal login pill.
2. From the **Hospital Dashboard** (`/dashboard/`), click **Register Waste** or navigate to `/waste/register/`.
3. Choose either **Single Bag Registration** or **Batch Registration**:
   - **Category Selection**: Choose from statutory CPCB categories:
     - 🟡 **Yellow**: Anatomical waste, soiled cotton/dressings, expired pharmaceuticals, chemical waste.
     - 🔴 **Red**: Contaminated plastic recyclables (tubing, bottles, IV sets, catheters, syringes without needles).
     - ⚪ **White (Translucent)**: Needles, scalpels, blades, fixed-needle syringes (puncture-proof containers).
     - 🔵 **Blue**: Broken glassware, medicine vials, metallic orthopedic implants.
   - **Weight**: Enter measured net weight in kilograms (calibrated scale validation between `0.05 kg` and `25.00 kg`).
   - **Origin Details**: Select originating department/ward (e.g., ICU, OT-3, Emergency, Pathology) and container type.
4. Click **Submit Registration**. The system automatically assigns an immutable statutory Bag ID (e.g., `WB-APEX-202611-0001`) and cryptographically signs a unique UUID `qr_token`.

### 2.2 QR Code Label Printing
1. Upon registration, click **Print Label** or navigate to `/waste/bags/<bag_id>/label/`.
2. The print view is pre-formatted with `@media print` rules calibrated for standard **100mm × 75mm** adhesive thermal label printers.
3. Affix the printed QR barcode securely to the external surface of the primary biohazard receptacle before collection transfer.
4. If a label is soiled or damaged, hospital staff or platform administrators can trigger **Regenerate QR** from `/waste/bags/<bag_id>/` without altering the underlying bag custody history.

### 2.3 Scheduling a Waste Pickup
1. In the hospital workspace, click **Request Collection** (`/collection/request/`).
2. Select your registered inventory bags awaiting pickup.
3. Pick your preferred collection date, pickup window (Morning / Afternoon / Evening), and add any specific access instructions (e.g., "Basement Biohazard Ramp Bay 2").
4. Submit the request. Track status transitions (`REQUESTED` → `SCHEDULED` → `ASSIGNED` → `IN_TRANSIT` → `COMPLETED`) in real time from your collection history ledger.

---

## 3. Waste Collector & Logistics Guide

### 3.1 Viewing Assigned Pickups
1. Log in to your collector portal (`/dashboard/`).
2. The **Logistics Dashboard** displays all assigned collection manifests, scheduled pickup times, hospital addresses, and emergency dispatch contact numbers.
3. Tap on any assigned request to open the **Collection Work Order**.

### 3.2 Executing Pickups & Digital QR Verification
1. Upon arrival at the healthcare facility, access the **Collection Scanner** (`/tracking/scan/`).
2. Point the device camera at the bag's affixed QR barcode (or enter the statutory Bag ID).
3. The system pulls bag records and prompts for field re-weighing:
   - If the recorded weight matches the physical tare reading, confirm custody handover.
   - If a significant deviation is detected (> ±10% by default), the platform triggers an automatic **Weight Discrepancy Alert** and logs a flagged event to the Directorate ledger.
4. Both parties digitally confirm custody. The bag status updates to `COLLECTED` and `IN_TRANSIT`.

### 3.3 Facility Handover
1. Transport the consignment to the designated Common Biomedical Waste Treatment Facility (CBWTF).
2. Present the consignment manifest reference number to the facility reception officer.
3. Confirm transfer into facility custody via the Handover Verification interface.

---

## 4. Treatment Facility Operations Guide

### 4.1 Consignment Reception & Verification
1. Log in to the facility workspace (`/dashboard/`).
2. Navigate to **Inbound Receptions** (`/treatment/manifests/`).
3. Scan each incoming bag. Verify physical integrity, category coding, and digital seal:
   - **Accept**: Bag matches manifest specifications and enters facility storage/treatment staging.
   - **Reject / Quarantine**: If a bag is leaking, miscategorized, or unaccounted for, reject it with specific statutory remarks (e.g., "Suspected cytotoxic vial in red bag"). Rejection automatically triggers an immutable **Discrepancy Investigation Record**.

### 4.2 Treatment Batch Processing
1. Navigate to **Treatment Batches** (`/treatment/batches/create/`).
2. Select the approved statutory treatment method based on waste category:
   - **Incineration** (High-temperature dual chamber ≥ 1050°C for Yellow anatomical waste).
   - **Autoclaving / Retorting** (High-pressure saturated steam sterilization ≥ 121°C for Red plastics).
   - **Microwaving** (High-frequency thermal disinfection).
   - **Shredding / Mutilation** (For autoclaved plastics and sharps).
3. Assign accepted waste bags to the batch.
4. Input operational chamber cycle parameters (starting temperature, cycle duration, operating pressure).
5. Start batch processing. Upon cycle completion, record operator credentials and post-treatment weight.

### 4.3 Final Residue Disposal Documentation
1. Navigate to **Disposal Records** (`/treatment/disposals/`).
2. Log disposal of residual inert ash or shredded sterilized scrap (e.g., secure sanitary landfill, certified recycling plant).
3. Click **Print Certificate** to generate an official statutory **Disposal Certificate** with embedded verification metadata.

---

## 5. Administrative & Directorate Control Guide

### 5.1 Platform Governance & User Lifecycle
1. Access the **Directorate Control Center** (`/dashboard/`).
2. Manage platform users at `/accounts/admin/users/`:
   - Filter by role (`HOSPITAL`, `COLLECTOR`, `FACILITY`, `ADMIN`) and account status.
   - Onboard pending applications with formal review (`/accounts/admin/users/<id>/approve/` or `reject/`).
   - Instantly suspend non-compliant or compromised accounts with mandatory audit reasoning (`/accounts/admin/users/<id>/suspend/`). Suspended accounts are immediately blocked with HTTP 403.

### 5.2 Facility Capacity & Environmental Thresholds
1. Monitor registered CBWTF treatment capacities at `/facilities/admin/facilities/`.
2. Set and update regulatory threshold parameters (daily incineration caps, autoclave run tolerances).
3. Review facility operational status (Active, Maintenance, Suspended).

### 5.3 Unified Audit Ledger & Data Exports
1. Navigate to the **Unified Security Ledger** (`/accounts/admin/audit-logs/`).
2. Browse across 5 chronological log streams:
   - **Admin Interventions** (User suspensions, policy updates, setting modifications).
   - **Chain of Custody Events** (Bag registration, collection, transfer, treatment).
   - **Facility Audits** (Capacity updates, maintenance downtime, compliance checks).
   - **Collection Status History** (Logistics milestone transitions).
   - **Regulatory Report Logs** (Export records, timestamps, record counts).
3. Click **Export CSV** to stream sanitized ledgers protected against spreadsheet formula injection.

### 5.4 Statutory Compliance Reports (CPCB Form IV & Form II)
1. Navigate to **Compliance Reports** (`/reports/`).
2. Generate official statutory returns:
   - **Form IV Annual Bio-Medical Waste Return**: Categorized aggregate generation, collection, and treatment totals.
   - **Form II Accident & Incident Record**: Spillages, needle-stick injuries, transport delays.
   - **Custom Date Range Reports**: Exportable to CSV and printable PDF formats.

---

## 6. Security & Privacy Protections

- **Public Bag Tracking Privacy**: The public QR verification page (`/waste/track/<qr_token>/`) displays only statutory verification fields (Bag ID, waste category, net weight, container type, and custody status). Ward names, internal staff names, hospital internal notes, and administrative IDs are strictly omitted.
- **CSRF & Injection Defense**: Every state-changing form is protected with standard Django CSRF tokens. All CSV exports sanitize cells starting with `=`, `+`, `-`, `@`, `\t`, or `\r`.
- **Tenant Isolation**: Database queries strictly filter records by the authenticated user's organization, preventing cross-tenant data access.

