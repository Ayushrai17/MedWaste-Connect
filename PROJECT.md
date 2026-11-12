# MedWaste Connect — Medical Waste Collection and Segregation System

A professional, enterprise-grade Django web application designed for comprehensive tracking, statutory compliance, and safe handling of biomedical waste from hospital wards to certified disposal facilities.

---

## 1. Project Purpose

The primary objective of **MedWaste Connect** is to digitize and enforce the entire biomedical waste chain of custody. By replacing error-prone paper registers with encrypted QR manifests, tamper-evident digital handshakes, and statutory audit dashboards, the platform ensures that no biohazard consignment goes unaccounted for between healthcare sources and treatment centers.

---

## 2. Technology Stack

- **Backend Framework**: Python 3.11+, Django 5.x / 6.x
- **Database**: SQLite (Development) / PostgreSQL-ready
- **Frontend & Styling**: Bootstrap 5.3 (CDN), Bootstrap Icons (CDN), Custom CSS Design System
- **Typography**: Google Fonts — Poppins (Bold 700 headings) and Inter (body)
- **Mapping & Visuals**: Leaflet.js with CartoDB Positron tiles (CDN)
- **Utilities & Security**: `python-decouple`, Pillow (image handling), `qrcode` (manifest generation)
- **Architectural Policy**: Vanilla JavaScript only; no heavy JavaScript SPA frameworks (no React, Next.js, or Vue).

---

## 3. Application Architecture (11 Modular Django Apps)

The system is organized into 11 decoupled Django apps:

| App | Core Responsibility |
| :--- | :--- |
| `core` | Public landing page, global templates, navigation bar, shared partials, and context processors (`site_settings`). |
| `accounts` | Custom `User` model, authentication, hospital registration, session security, and role-based dashboard protection. |
| `hospitals` | Hospital, healthcare clinic, and internal ward/department profile management. |
| `waste` | Bio-medical waste categorization (Yellow, Red, White, Blue), bag logging, scale weight validation, and QR code generation. |
| `collection` | Pickup request scheduling, vehicle dispatch queues, route planning, and driver assignments. |
| `tracking` | Real-time transit checkpoints, chain of custody logging, and custody handover timestamps. |
| `treatment` | Facility reception gate checks, weight reconciliation, treatment execution logs, and compliance certificates. |
| `facilities` | Common Bio-medical Waste Treatment Facility (CBWTF) profiles, incinerator capacities, and plant licenses. |
| `fleet` | Waste transport vehicles, collector driver rosters, and logistics equipment management. |
| `reports` | Executive KPI analytics, CPCB statutory compliance reporting, discrepancy audits, and PDF/CSV data exports. |
| `notifications` | In-app alerts, compliance deadline reminders, and emergency discrepancy warnings. |

---

## 4. Stakeholder Roles & Access Control

The platform enforces strict server-side access control across 4 distinct roles:

1. **Administrator (`ADMIN`)**
   - Central regulatory oversight across all hospital networks, transport fleets, and treatment plants.
   - User account activation, compliance auditing, system configuration, and CPCB regulatory reporting.
2. **Hospital / Clinic Staff (`HOSPITAL`)**
   - Ward-level waste segregation into prescribed colour-coded bags.
   - Dynamic QR manifest generation and bag weight logging.
   - Submission of scheduled pickup requests and receipt verification.
3. **Waste Collector / Driver (`COLLECTOR`)**
   - Mobile barcode/QR bag scanning at hospital pickup points.
   - Actual weight validation and digital custody acceptance.
   - Vehicle transport delivery to designated treatment facilities.
4. **Treatment Facility Operator (`FACILITY`)**
   - Consignment scanning and physical weight reconciliation upon gate arrival.
   - Recording authorized treatment methods (high-temperature incineration, autoclaving, chemical disinfection, shredding).
   - Issuance of tamper-proof digital disposal and compliance certificates.

---

## 5. Planned Waste Lifecycle (7 Lifecycle Stages)

Every biomedical waste bag progresses through 7 consecutive lifecycle stages:

```
[1. Requested] ➔ [2. Assigned] ➔ [3. Collected] ➔ [4. In Transit] ➔ [5. Received] ➔ [6. Treated] ➔ [7. Disposed]
```

1. **Requested**: Hospital staff tags segregated waste bags with unique QR codes and submits a pickup request.
2. **Assigned**: The logistics dispatcher assigns an authorized collection vehicle and driver.
3. **Collected**: Driver scans QR codes at the ward, verifies scale weights, and signs off custody transfer.
4. **In Transit**: Vehicle moves along the approved route toward the authorized treatment center with logged checkpoints.
5. **Received**: Treatment facility verifies bag QR codes, validates gross arrival weight, and flags discrepancies.
6. **Treated**: Waste is subjected to prescribed disposal methods (e.g. incineration at >1050°C, high-pressure autoclaving).
7. **Disposed**: Final residual ash or non-hazardous shredded byproduct is safely landfilled or recycled, with a digital certificate generated.

---

## 6. Design System & Strict Brand Color Palette

Defined centrally as CSS custom variables in `static/css/main.css`:

| Token | HEX | UI Usage Rule |
| :--- | :--- | :--- |
| `--teal-deep` | `#1B6585` | Sticky navbar, primary page headings, and brand surfaces. |
| `--teal-light` | `#3FA3BC` | Interactive hover states, secondary accents, focus rings, and badges. |
| `--cream` | `#F4EBD6` | Warm section backgrounds, hero area, and soft card surfaces. |
| `--orange` | `#F26B21` | High-priority CTA buttons, key alerts, and status highlights. |
| `--navy` | `#141F2B` | Primary typography, dark surface cards, and master footer. |
| `--white` | `#FFFFFF` | Crisp card backgrounds, form input fields, and clean contrast overlays. |

- **Operational Distinction**: Standard biomedical bag colours (Yellow, Red, White, Blue) are strictly operational category markers for waste classification, distinct from the website brand palette.
- **Visual Style**: Clean healthcare technology aesthetic, 16px card corners, 20px glass analytics panel, pill buttons (`border-radius: 50rem`), soft dual shadows, accessible keyboard focus states (`outline: 3px solid var(--teal-light)`), and responsive layout.

---

## 7. Five-Step Build Roadmap

- **Step 1: Project Setup, Design System, Complete Public Website & Auth Skeleton (Current Step)**
  - Decoupled Django configuration (`python-decouple`).
  - Custom `User` model with 4 roles and admin integration.
  - 11-app modular architecture initialized.
  - Comprehensive 8-section homepage with Hero, interactive Leaflet analytics, How It Works, Features, Roles, Benefits, and Final CTA.
  - Role-protected dashboard skeleton with server-side HTTP 403 enforcement and public hospital registration.
- **Step 2: Waste Logging, Bag Categorization & Dynamic QR Code Generation**
  - Category models (Yellow, Red, White, Blue).
  - Dynamic QR code generation with encrypted manifest data.
  - Printable label templates with bag identification tags.
- **Step 3: Pickup Scheduling, Fleet Dispatch & Collector Mobile Scanner**
  - Hospital pickup dispatch request interface.
  - Collector route assignment and van manifest queues.
  - Mobile camera-based barcode/QR scanner for collectors.
- **Step 4: Live Chain-of-Custody Tracking, Gate Reception & Treatment Records**
  - Checkpoint tracking and route logging.
  - Facility gate reception scan-in and weight discrepancy detection.
  - Treatment execution logs (incineration/autoclave) and certificate generation.
- **Step 5: Centralized Analytics, CPCB Regulatory Reports & Polish**
  - City-wide segregation quality and volume metrics.
  - Exportable compliance reports (PDF/CSV) matching statutory guidelines.
  - Comprehensive integration testing and production hardening.
