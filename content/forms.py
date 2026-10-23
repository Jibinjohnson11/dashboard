from django import forms

from content.services import CONTENT_TYPES
from core.forms import OptionDataSelect, collapse_spaces


class ContentForm(forms.Form):
    brand_id = forms.ChoiceField(label="Brand")
    campaign_id = forms.ChoiceField(label="Campaign", widget=OptionDataSelect(attrs={"data-campaign-select": ""}))
    title = forms.CharField(label="Content title", max_length=200)
    keyword = forms.CharField(label="Primary keyword", max_length=120)
    content_type = forms.ChoiceField(label="Content type", choices=CONTENT_TYPES)
    writer_user_id = forms.TypedChoiceField(label="Writer", coerce=int, required=False)
    body_html = forms.CharField(
        label="Content body",
        required=False,
        max_length=400_000,
        widget=forms.Textarea(attrs={"rows": 16, "data-editor-source": ""}),
    )
    internal_notes = forms.CharField(
        label="Internal notes",
        required=False,
        max_length=3000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Visible to your team only, never to clients.",
    )
    edit_seq = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, campaigns=(), writer_choices=(), is_admin=False, is_edit=False, **kwargs):
        super().__init__(*args, **kwargs)
        brands = {}
        for campaign in campaigns:
            brand = campaign.get("brand") or {}
            brands[str(campaign["brand_id"])] = brand.get("name", "Brand")
        self.fields["brand_id"].choices = [("", "Select a brand")] + sorted(brands.items(), key=lambda kv: kv[1].lower())
        self.fields["campaign_id"].choices = [("", "Select a campaign")] + [(c["id"], c["name"]) for c in campaigns]
        self.fields["campaign_id"].widget.option_data = {
            c["id"]: {"brand": str(c["brand_id"]), "keyword": c.get("primary_keyword", "")} for c in campaigns
        }
        self.fields["brand_id"].widget.attrs["data-brand-select"] = ""
        if is_admin:
            self.fields["writer_user_id"].choices = list(writer_choices)
            self.fields["writer_user_id"].required = True
        else:
            del self.fields["writer_user_id"]
        if is_edit:
            self.fields["brand_id"].disabled = True
            self.fields["campaign_id"].disabled = True

    def clean_title(self):
        title = collapse_spaces(self.cleaned_data["title"])
        if len(title) < 3:
            raise forms.ValidationError("Title must be at least 3 characters.")
        return title

    def clean_keyword(self):
        return collapse_spaces(self.cleaned_data["keyword"])

    def clean_internal_notes(self):
        return self.cleaned_data["internal_notes"].strip()

    def clean_edit_seq(self):
        return self.cleaned_data.get("edit_seq") or 0
