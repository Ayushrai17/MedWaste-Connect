import json
import re
from decimal import Decimal, InvalidOperation
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import WasteCategory, WasteRegistrationBatch, WasteBag, get_min_bag_weight, get_max_bag_weight


class WasteBatchRegistrationForm(forms.Form):
    """
    Form for registering a biomedical waste batch containing one or more physical bags.
    Validates hospital ward, clinical department, and individual bag weights/categories.
    Enforces atomic batch creation and server-side hospital ownership injection.
    """
    ward = forms.CharField(
        max_length=100,
        required=True,
        label="Ward / Unit",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g., ICU-3, General Ward 4B, Trauma Unit",
            "required": "required",
        }),
        help_text="Hospital location where the waste was generated and segregated."
    )
    department = forms.CharField(
        max_length=100,
        required=True,
        label="Clinical Department",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g., Surgery, Intensive Care, Pathology, Emergency",
            "required": "required",
        }),
        help_text="Clinical department responsible for this consignment."
    )
    bags_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        help_text="Hidden payload populated by dynamic row interface."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Load active categories for easy access
        self.active_categories = {
            c.id: c for c in WasteCategory.objects.filter(is_active=True).order_by("display_order")
        }
        self.code_to_category = {
            c.code.upper(): c for c in self.active_categories.values()
        }

    def _extract_raw_bags(self):
        """
        Extracts raw bag tuples from either JSON payload, indexed POST keys, or single bag fallback.
        Returns a list of raw dicts: [{'category_ref': ..., 'weight_raw': ...}, ...]
        """
        raw_bags = []

        # 1. Check if JSON payload was submitted
        bags_json_str = self.data.get("bags_json", "").strip()
        if bags_json_str:
            try:
                parsed = json.loads(bags_json_str)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            raw_bags.append({
                                "category_ref": item.get("category"),
                                "weight_raw": item.get("weight"),
                            })
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # 2. If no JSON or empty, extract from indexed form fields (bag_category_0, bag_weight_0, etc.)
        if not raw_bags:
            # Pattern matches bag_category_0, bag_category_1, category_0, category_1, etc.
            indices = set()
            for key in self.data.keys():
                match = re.match(r"^(?:bag_)?category_(\d+)$", key)
                if match:
                    indices.add(int(match.group(1)))

            for idx in sorted(indices):
                cat_val = self.data.get(f"bag_category_{idx}") or self.data.get(f"category_{idx}")
                weight_val = self.data.get(f"bag_weight_{idx}") or self.data.get(f"weight_{idx}")
                raw_bags.append({
                    "category_ref": cat_val,
                    "weight_raw": weight_val,
                })

        # 3. Fallback: single category and weight keys (e.g. standard single bag form)
        if not raw_bags:
            single_cat = self.data.get("category")
            single_weight = self.data.get("weight")
            if single_cat is not None or single_weight is not None:
                raw_bags.append({
                    "category_ref": single_cat,
                    "weight_raw": single_weight,
                })

        return raw_bags

    def clean(self):
        cleaned_data = super().clean()
        raw_bags = self._extract_raw_bags()

        if not raw_bags:
            raise ValidationError("At least one physical waste bag must be included in the registration batch.")

        if len(raw_bags) > 20:
            raise ValidationError("A single registration batch cannot exceed 20 waste bags.")

        min_weight = get_min_bag_weight()
        max_weight = get_max_bag_weight()

        cleaned_bags = []
        total_weight = Decimal("0.00")
        errors = []

        for idx, bag_raw in enumerate(raw_bags, start=1):
            cat_ref = bag_raw.get("category_ref")
            weight_raw = bag_raw.get("weight_raw")

            # Validate Category
            category_obj = None
            if cat_ref is not None:
                # Can be an integer ID, string ID, or category code (e.g. 'YELLOW')
                try:
                    cat_id = int(cat_ref)
                    category_obj = self.active_categories.get(cat_id)
                except (ValueError, TypeError):
                    category_obj = self.code_to_category.get(str(cat_ref).strip().upper())

            if not category_obj:
                errors.append(f"Bag #{idx}: Please select a valid biomedical waste category.")
                continue

            # Validate Weight
            if weight_raw is None or str(weight_raw).strip() == "":
                errors.append(f"Bag #{idx}: Weight is required.")
                continue

            try:
                weight_str = str(weight_raw).strip()
                weight_decimal = Decimal(weight_str)
            except (InvalidOperation, TypeError, ValueError):
                errors.append(f"Bag #{idx}: Enter a valid numerical weight.")
                continue

            if not weight_decimal.is_finite():
                errors.append(f"Bag #{idx}: Invalid weight format.")
                continue

            if weight_decimal <= Decimal("0.00"):
                errors.append(f"Bag #{idx}: Weight must be strictly positive (greater than 0 kg).")
                continue

            if weight_decimal < min_weight:
                errors.append(f"Bag #{idx}: Weight {weight_decimal} kg is below minimum threshold of {min_weight} kg.")
                continue

            if weight_decimal > max_weight:
                errors.append(
                    f"Bag #{idx}: Weight {weight_decimal} kg exceeds maximum safe manual handling limit of {max_weight} kg."
                )
                continue

            # Quantize weight to 2 decimal places
            weight_quantized = weight_decimal.quantize(Decimal("0.01"))
            total_weight += weight_quantized

            cleaned_bags.append({
                "category": category_obj,
                "weight": weight_quantized,
            })

        if errors:
            raise ValidationError(errors)

        if not cleaned_bags:
            raise ValidationError("No valid waste bags could be verified for this batch.")

        cleaned_data["cleaned_bags"] = cleaned_bags
        cleaned_data["total_weight"] = total_weight
        cleaned_data["bag_count"] = len(cleaned_bags)

        return cleaned_data

    def save(self, hospital, registered_by):
        """
        Atomically saves the batch and all individual waste bags.
        Enforces strict server-side hospital ownership.
        """
        if not self.is_valid():
            raise ValueError("Cannot save invalid registration form.")

        ward = self.cleaned_data["ward"].strip()
        department = self.cleaned_data["department"].strip()
        cleaned_bags = self.cleaned_data["cleaned_bags"]
        total_weight = self.cleaned_data["total_weight"]
        bag_count = self.cleaned_data["bag_count"]

        with transaction.atomic():
            batch = WasteRegistrationBatch.objects.create(
                hospital=hospital,
                registered_by=registered_by,
                ward=ward,
                department=department,
                bag_count=bag_count,
                total_weight=total_weight,
            )

            created_bags = []
            for bag_item in cleaned_bags:
                bag = WasteBag.objects.create(
                    batch=batch,
                    hospital=hospital,
                    registered_by=registered_by,
                    category=bag_item["category"],
                    ward=ward,
                    department=department,
                    weight=bag_item["weight"],
                    status=WasteBag.Status.REGISTERED,
                )
                created_bags.append(bag)

            try:
                from tracking.models import CustodyEvent
                from tracking.services import record_custody_event
                for b in created_bags:
                    record_custody_event(
                        bag=b,
                        event_type=CustodyEvent.EventType.BAG_REGISTERED,
                        new_custody_status=WasteBag.Status.REGISTERED,
                        actor=registered_by,
                        origin_location=f"{hospital.organization_name or hospital.username} ({ward})",
                        destination_location="Hospital Waste Storage Dock",
                        verification_method="WEIGHMENT_AND_TAGGING",
                        weight_at_event=b.weight,
                        remarks=f"Initial registration in batch {batch.batch_number}",
                    )
            except Exception as cust_err:
                # Do not block registration if custody logging encounters transient issue
                import logging
                logging.getLogger(__name__).warning(f"Could not log custody event on registration: {cust_err}")

        return batch, created_bags

