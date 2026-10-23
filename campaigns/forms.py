import re

from django import forms

from campaigns.services import STATUS_CHOICES
from core.forms import HttpURLField, collapse_spaces


class CampaignForm(forms.Form):
    name = forms.CharField(label="Campaign name", max_length=150)
    brand_id = forms.ChoiceField(label="Brand")
    primary_keyword = forms.CharField(label="Primary keyword", max_length=120)
    secondary_keywords = forms.CharField(
        label="Secondary keywords",
        required=False,
        max_length=3000,
        widget=forms.Textarea(attrs={"rows": 3, "data-keyword-input": ""}),
        help_text="Separate keywords with commas or new lines (up to 30).",
    )
    target_url = HttpURLField(label="Target URL", required=False)
    target_location = forms.CharField(label="Target location", max_length=120, required=False)
    description = forms.CharField(label="Campaign description", max_length=3000, required=False, widget=forms.Textarea(attrs={"rows": 4}))
    start_date = forms.DateField(label="Start date", widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(label="End date", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    assigned_user_ids = forms.TypedMultipleChoiceField(
        label="Assigned team members", required=False, coerce=int, widget=forms.CheckboxSelectMultiple
    )
    status = forms.ChoiceField(choices=STATUS_CHOICES, initial="active")

    def __init__(self, *args, brand_choices=(), member_choices=(), lock_brand=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand_id"].choices = [("", "Select a brand")] + list(brand_choices)
        self.fields["assigned_user_ids"].choices = list(member_choices)
        if lock_brand:
            self.fields["brand_id"].disabled = True
            self.fields["brand_id"].help_text = "A campaign always stays connected to its brand."

    def clean_name(self):
        name = collapse_spaces(self.cleaned_data["name"])
        if len(name) < 2:
            raise forms.ValidationError("Campaign name must be at least 2 characters.")
        return name

    def clean_primary_keyword(self):
        return collapse_spaces(self.cleaned_data["primary_keyword"])

    def clean_target_location(self):
        return collapse_spaces(self.cleaned_data["target_location"])

    def clean_description(self):
        return self.cleaned_data["description"].strip()

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
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before the start date.")
        return cleaned
