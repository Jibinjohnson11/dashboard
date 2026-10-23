from django import forms

from accounts.models import normalize_email
from core.forms import collapse_spaces
from core.permissions import ROLE_CHOICES


class MemberForm(forms.Form):
    full_name = forms.CharField(label="Full name", max_length=150)
    email = forms.EmailField(label="Email", max_length=254)
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    brand_ids = forms.MultipleChoiceField(
        label="Assigned brands",
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Reviewers and clients only see content for the brands selected here.",
    )
    campaign_ids = forms.MultipleChoiceField(
        label="Assigned campaigns",
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Team members can write content for these campaigns.",
    )
    status = forms.ChoiceField(
        label="Account status",
        choices=[("active", "Active"), ("inactive", "Inactive")],
        initial="active",
    )

    def __init__(self, *args, brand_choices=(), campaign_choices=(), is_edit=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand_ids"].choices = list(brand_choices)
        self.fields["campaign_ids"].choices = list(campaign_choices)
        if is_edit:
            self.fields["email"].disabled = True
            self.fields["email"].help_text = "Email addresses cannot be changed here."
        else:
            self.fields["status"].help_text = "Active members receive an email link to set their own password."

    def clean_full_name(self):
        name = collapse_spaces(self.cleaned_data["full_name"])
        if len(name) < 2:
            raise forms.ValidationError("Please enter the member's full name.")
        return name

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if role in ("reviewer", "client") and not cleaned.get("brand_ids"):
            self.add_error("brand_ids", "Assign at least one brand so this member can see content.")
        if role in ("reviewer", "client", "admin") and cleaned.get("campaign_ids"):
            cleaned["campaign_ids"] = []
        return cleaned


class ActivateForm(forms.Form):
    role = forms.ChoiceField(choices=ROLE_CHOICES)
