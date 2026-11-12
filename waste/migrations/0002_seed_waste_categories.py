from django.db import migrations


def seed_waste_categories(apps, schema_editor):
    WasteCategory = apps.get_model("waste", "WasteCategory")

    categories = [
        {
            "code": "YELLOW",
            "name": "Yellow",
            "display_color": "#FFD200",
            "container_type": "Yellow-coloured non-chlorinated plastic bags",
            "treatment_description": (
                "Incineration or Plasma Pyrolysis or deep burial. For human anatomical waste, "
                "animal anatomical waste, soiled waste, expired/discarded medicines, and chemical waste "
                "(Schedule I, BMW Rules 2016)."
            ),
            "is_active": True,
            "display_order": 1,
        },
        {
            "code": "RED",
            "name": "Red",
            "display_color": "#E53935",
            "container_type": "Red-coloured non-chlorinated plastic bags or autoclavable containers",
            "treatment_description": (
                "Autoclaving / microwaving / hydroclaving followed by shredding or mutilation. "
                "For contaminated recyclable plastic waste such as tubing, bottles, intravenous tubes and sets, "
                "catheters, urine bags, syringes without needles, and vaccutainers."
            ),
            "is_active": True,
            "display_order": 2,
        },
        {
            "code": "WHITE",
            "name": "White / Translucent",
            "display_color": "#F5F5F5",
            "container_type": "Puncture-proof, leak-proof, tamper-proof translucent container",
            "treatment_description": (
                "Autoclaving or dry heat sterilization followed by shredding or mutilation, or encapsulation. "
                "For waste sharps including metals: needles, scalpels, blades, and contaminated sharp objects."
            ),
            "is_active": True,
            "display_order": 3,
        },
        {
            "code": "BLUE",
            "name": "Blue",
            "display_color": "#1E88E5",
            "container_type": "Puncture-proof and leak-proof boxes or cardboard with blue marking",
            "treatment_description": (
                "Disinfection by soaking/washing with sodium hypochlorite or autoclaving/microwaving, "
                "then sent for glass recycling. For glassware, medicine vials and ampoules, and orthopedic implants."
            ),
            "is_active": True,
            "display_order": 4,
        },
    ]

    for cat_data in categories:
        WasteCategory.objects.update_or_create(
            code=cat_data["code"],
            defaults=cat_data,
        )


def reverse_seed_waste_categories(apps, schema_editor):
    WasteCategory = apps.get_model("waste", "WasteCategory")
    WasteCategory.objects.filter(code__in=["YELLOW", "RED", "WHITE", "BLUE"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("waste", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_waste_categories, reverse_code=reverse_seed_waste_categories),
    ]

