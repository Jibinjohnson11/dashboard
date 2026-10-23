from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm

from accounts.models import normalize_email

INVALID_LOGIN = "Invalid email or password, or the account is not active yet."


class LoginForm(forms.Form):
    email = forms.EmailField(label="Email address", max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))
    password = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember_me = forms.BooleanField(label="Remember me", required=False)

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])

    def clean(self):
        cleaned = super().clean()
        email, password = cleaned.get("email"), cleaned.get("password")
        if email and password:
            # ModelBackend rejects inactive users and runs the hasher for unknown emails (timing-safe).
            self.user = authenticate(self.request, email=email, password=password)
            if self.user is None:
                raise forms.ValidationError(INVALID_LOGIN, code="invalid_login")
        return cleaned


class RegistrationForm(forms.Form):
    full_name = forms.CharField(label="Full name", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(label="Email address", max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="At least 10 characters, with a letter and a number or symbol. Avoid common passwords.",
    )
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if len(name) < 2:
            raise forms.ValidationError("Please enter your full name.")
        return name

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        if p1:
            from accounts.models import User

            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


class EmailPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(label="Email address", max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])


class AccountSetPasswordForm(SetPasswordForm):
    pass


class ProfileForm(forms.Form):
    full_name = forms.CharField(label="Full name", max_length=150)

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if len(name) < 2:
            raise forms.ValidationError("Please enter your full name.")
        return name
