import re

from django import forms

from campaigns.services import STATUS_CHOICES
from core.forms import HttpURLField, collapse_spaces


class CampaignForm(forms.Form):
    """Field order is the on-screen order (matches the campaign modal design)."""

    primary_keyword = forms.CharField(label="Primary keyword", max_length=120)
    secondary_keywords = forms.CharField(
        label="Secondary keywords",
        required=False,
        max_length=3000,
        widget=forms.Textarea(attrs={"rows": 2, "data-keyword-input": "", "placeholder": "One keyword per line"}),
        help_text="Type a keyword and press Enter to add it (up to 30).",
    )
    brand_id = forms.ChoiceField(label="Brand")
    target_location = forms.CharField(label="Location", max_length=120, required=False)
    landing_page_url = HttpURLField(label="Landing page URL", required=False)
    end_date = forms.DateField(label="Target deadline", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    active = forms.ChoiceField(label="Active", choices=[("yes", "Yes"), ("no", "No")], initial="yes")
    assigned_user_ids = forms.TypedMultipleChoiceField(
        label="Assigned team members", required=False, coerce=int, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args, brand_choices=(), member_choices=(), lock_brand=False, current_status=None, start_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_status = current_status
        self.start_date = start_date
        self.fields["brand_id"].choices = [("", "Select a brand")] + list(brand_choices)
        self.fields["assigned_user_ids"].choices = list(member_choices)
        if lock_brand:
            self.fields["brand_id"].disabled = True
            self.fields["brand_id"].help_text = "A campaign always stays connected to its brand."

    def clean_primary_keyword(self):
        keyword = collapse_spaces(self.cleaned_data["primary_keyword"])
        if len(keyword) < 2:
            raise forms.ValidationError("Enter the primary keyword for this campaign.")
        return keyword

    def clean_target_location(self):
        return collapse_spaces(self.cleaned_data["target_location"])

    def clean_secondary_keywords(self):
        raw = self.cleaned_data["secondary_keywords"]
        seen, keywords = set(), []
        for part in re.split(r"[,\n]", raw):
            keyword = collapse_spaces(part)
            if keyword and keyword.lower() not in seen:
                if len(keyword) > 120:
                    raise forms.ValidationError("Each keyword must be 120 characters or fewer.")
                seen.add(keyword.lower())
                keywords.append(keyword)
        if len(keywords) > 30:
            raise forms.ValidationError("Please enter at most 30 secondary keywords.")
        return keywords

    def clean(self):
        cleaned = super().clean()
        # "Active: Yes/No" maps onto the existing statuses. "No" keeps a non-active status
        # (draft/paused/completed) if the campaign already had one, otherwise pauses it.
        if cleaned.get("active") == "yes":
            cleaned["status"] = "active"
        elif cleaned.get("active") == "no":
            cleaned["status"] = self.current_status if self.current_status in ("draft", "paused", "completed") else "paused"
        end = cleaned.get("end_date")
        if self.start_date and end and end < self.start_date:
            self.add_error("end_date", "The deadline cannot be before the campaign's start date.")
        return cleaned
