from django import forms

from approvals.services import ACTIONS


class DecisionForm(forms.Form):
    action = forms.ChoiceField(choices=[(a, a) for a in ACTIONS])
    feedback = forms.CharField(required=False, max_length=3000, widget=forms.Textarea(attrs={"rows": 4}))
    version_number = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
