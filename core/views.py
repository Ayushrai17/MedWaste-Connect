from django.shortcuts import render


def home(request):
    """
    Landing page view for Medical Waste Collection and Segregation System.
    Provides live analytics statistics, route highlights, platform features,
    benefits, and quick stakeholder role entry points.
    """
    # Sample numbers passed from context (demonstration metrics; will be queried dynamically in later steps)
    stats = {
        "waste_collected_kg": 4820,
        "pickups_completed": 142,
        "hospitals_connected": 58,
        "facilities_active": 12,
    }

    # Demonstration live route manifests displayed beside the mini Leaflet map
    active_routes = [
        {
            "code": "RT-204",
            "hospital": "Apex Super-Specialty Hospital",
            "destination": "Metro Bio-Incineration Hub",
            "status": "In Transit",
            "weight": "142 kg",
            "bags": 18,
        },
        {
            "code": "RT-205",
            "hospital": "City Care Trauma Centre",
            "destination": "North Autoclave Facility",
            "status": "Collected",
            "weight": "68 kg",
            "bags": 9,
        },
        {
            "code": "RT-206",
            "hospital": "Greenwood Memorial Clinic",
            "destination": "Central Eco-Treatment Plant",
            "status": "En Route",
            "weight": "210 kg",
            "bags": 27,
        },
    ]

    # Sample map coordinate points for Leaflet map (centred on India)
    sample_hospitals = [
        {
            "name": "Apex Super-Specialty Hospital, Mumbai",
            "coords": [19.0760, 72.8777],
            "details": "Demonstration: 18 Bags Dispatched • Manifest In Transit",
        },
        {
            "name": "Central Memorial Hospital, Delhi",
            "coords": [28.6139, 77.2090],
            "details": "Demonstration: Manifest Verified • Driver En Route",
        },
        {
            "name": "Sunrise Healthcare Centre, Bengaluru",
            "coords": [12.9716, 77.5946],
            "details": "Demonstration: Bags Segregated & Tagged • Pickup Scheduled",
        },
        {
            "name": "East Coast General Hospital, Kolkata",
            "coords": [22.5726, 88.3639],
            "details": "Demonstration: Disposal Recorded • Certificate Generated",
        },
    ]

    # Six planned platform features (Phase 4, Section 4)
    features = [
        {
            "title": "Smart Waste Segregation",
            "icon": "bi-tags",
            "description": "Standardised category categorization (Yellow, Red, White, Blue) per biomedical guidelines, minimising segregation errors at the ward.",
        },
        {
            "title": "QR-Based Waste Identification",
            "icon": "bi-qr-code-scan",
            "description": "Tamper-evident dynamic QR codes created per bag containing source hospital, ward, category, weight, and timestamp.",
        },
        {
            "title": "Scheduled Waste Collection",
            "icon": "bi-calendar-check",
            "description": "Seamless pickup request dispatching, driver vehicle assignments, and real-time pickup status confirmations.",
        },
        {
            "title": "Digital Chain of Custody",
            "icon": "bi-shield-check",
            "description": "Cryptographically auditable handovers between ward staff, transport drivers, and plant operators preventing unauthorized dumping.",
        },
        {
            "title": "Treatment Documentation",
            "icon": "bi-fire",
            "description": "Automated verification of incineration, autoclaving, or shredding, with verifiable digital compliance certificates.",
        },
        {
            "title": "Centralised Analytics",
            "icon": "bi-bar-chart-line",
            "description": "Executive compliance dashboards, weight discrepancy alerts, and CPCB regulatory reporting exports.",
        },
    ]

    # Four key benefits (Phase 4, Section 6)
    benefits = [
        {
            "title": "Transparent Waste Movement",
            "icon": "bi-arrow-left-right",
            "description": "Every kilogram is tracked from hospital bedside to incinerator chimney, eliminating unrecorded disposal.",
        },
        {
            "title": "Improved Accountability",
            "icon": "bi-person-check",
            "description": "Staff digital signatures and GPS check-ins fix individual responsibility at each handover stage.",
        },
        {
            "title": "Centralised Digital Records",
            "icon": "bi-database-check",
            "description": "Replace paper logs and loose slips with cloud-hosted, tamper-proof audit trails ready for statutory inspections.",
        },
        {
            "title": "Better Operational Visibility",
            "icon": "bi-eye",
            "description": "Real-time visibility for hospital administrators, municipal regulators, and treatment facility operators.",
        },
    ]

    context = {
        "stats": stats,
        "active_routes": active_routes,
        "sample_hospitals": sample_hospitals,
        "features": features,
        "benefits": benefits,
    }
    return render(request, "core/home.html", context)
