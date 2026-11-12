from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import User


class HospitalRegistrationForm(forms.ModelForm):
    """
    Public self-registration form restricted strictly to Hospital and Clinic staff.
    Public registration as Administrator, Collector, or Treatment Facility is prohibited.
    """
    organization_name = forms.CharField(
        max_length=150,
        required=True,
        label="Hospital / Clinic Name",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. City Care Specialty Hospital",
        })
    )
    phone = forms.CharField(
        max_length=20,
        required=True,
        label="Contact Phone",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. +91 98765 43210",
        })
    )
    email = forms.EmailField(
        required=True,
        label="Official Email Address",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. waste-admin@citycare.org",
        })
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Create a secure password",
            "id": "reg_password",
        })
    )
    password_confirm = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Confirm your password",
            "id": "reg_password_confirm",
        })
    )

    class Meta:
        model = User
        fields = ("organization_name", "username", "email", "phone")
        widgets = {
            "username": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Choose a username (e.g. citycare_admin)",
            }),
        }

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("A user with this username already exists.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")

        if password and password_confirm:
            if password != password_confirm:
                self.add_error("password_confirm", "Passwords do not match.")
            else:
                validate_password(password)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        # Strictly enforce HOSPITAL role for public registration
        user.role = User.Role.HOSPITAL
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user

