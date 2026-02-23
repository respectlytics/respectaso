from django import forms

from .models import App


class AppForm(forms.ModelForm):
    """Form for creating/editing an App."""

    class Meta:
        model = App
        fields = ["name", "bundle_id"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full bg-slate-700 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500",
                    "placeholder": "My iOS App",
                }
            ),
            "bundle_id": forms.TextInput(
                attrs={
                    "class": "w-full bg-slate-700 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500",
                    "placeholder": "com.example.myapp (optional)",
                }
            ),
        }


COUNTRY_CHOICES = [
    ("us", "🇺🇸 United States"),
    ("gb", "🇬🇧 United Kingdom"),
    ("ca", "🇨🇦 Canada"),
    ("au", "🇦🇺 Australia"),
    ("de", "🇩🇪 Germany"),
    ("fr", "🇫🇷 France"),
    ("jp", "🇯🇵 Japan"),
    ("kr", "🇰🇷 South Korea"),
    ("cn", "🇨🇳 China"),
    ("br", "🇧🇷 Brazil"),
    ("in", "🇮🇳 India"),
    ("mx", "🇲🇽 Mexico"),
    ("es", "🇪🇸 Spain"),
    ("it", "🇮🇹 Italy"),
    ("nl", "🇳🇱 Netherlands"),
    ("se", "🇸🇪 Sweden"),
    ("no", "🇳🇴 Norway"),
    ("dk", "🇩🇰 Denmark"),
    ("fi", "🇫🇮 Finland"),
    ("pt", "🇵🇹 Portugal"),
    ("ru", "🇷🇺 Russia"),
    ("tr", "🇹🇷 Turkey"),
    ("sa", "🇸🇦 Saudi Arabia"),
    ("ae", "🇦🇪 UAE"),
    ("sg", "🇸🇬 Singapore"),
    ("th", "🇹🇭 Thailand"),
    ("id", "🇮🇩 Indonesia"),
    ("ph", "🇵🇭 Philippines"),
    ("vn", "🇻🇳 Vietnam"),
    ("tw", "🇹🇼 Taiwan"),
]


class KeywordSearchForm(forms.Form):
    """Form for searching keywords."""

    keywords = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "class": "w-full bg-slate-700 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500",
                "placeholder": "meditation app, fitness tracker, sleep sounds",
                "autofocus": True,
            }
        ),
        label="Keywords",
        help_text="Enter one or more keywords, separated by commas (max 20).",
    )
    app_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
    )
    countries = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        help_text="Comma-separated country codes (max 5).",
    )

    def clean_countries(self):
        """Parse and validate comma-separated country codes."""
        raw = self.cleaned_data.get("countries", "").strip()
        if not raw:
            return ["us"]
        valid_codes = {code for code, _ in COUNTRY_CHOICES}
        codes = [c.strip().lower() for c in raw.split(",") if c.strip()]
        codes = [c for c in codes if c in valid_codes]
        if not codes:
            return ["us"]
        return codes[:5]  # Max 5 countries


class OpportunitySearchForm(forms.Form):
    """Form for the Country Opportunity Finder — single keyword, all countries."""

    keyword = forms.CharField(
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "w-full bg-slate-700 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500",
                "placeholder": "fitness tracker",
                "autofocus": True,
            }
        ),
    )
    app_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
    )
