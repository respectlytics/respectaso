from django import forms

from aso import countries

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


# The storefront list lives in aso/countries.py, the one place allowed to
# define one. This name is kept because a dozen modules and templates import
# it, and it still answers (code, "flag Name") tuples.
COUNTRY_CHOICES = countries.choices()


class KeywordSearchForm(forms.Form):
    """Form for searching keywords."""

    # A textarea that looks like a one-line input: whole keyword lists are
    # pasted here, comma-separated or one per line. It grows with the content
    # up to five lines, then scrolls inside (static/js/keyword-search-job.js).
    # Same height as the Search button beside it. The limit per search
    # depends on the edition and is enforced by aso.search_jobs, never here.
    keywords = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": "block w-full bg-slate-700 border border-white/10 rounded-lg px-3 py-2.5 text-sm leading-5 text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500 resize-none overflow-hidden",
                "placeholder": "meditation app, fitness tracker, sleep sounds",
                "autofocus": True,
                "rows": 1,
                "id": "id_keywords",
            }
        ),
        label="Keywords",
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
        codes = countries.clean(raw.split(","))
        if not codes:
            return ["us"]
        return codes[:5]  # Max 5 countries


class OpportunitySearchForm(forms.Form):
    """The Country Opportunity Finder: one keyword, the countries you pick."""

    keyword = forms.CharField(
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "w-full bg-slate-700 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500",
                "placeholder": "fitness tracker",
                "autofocus": True,
            }
        ),
    )
    app_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
    )
    countries = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        help_text="Comma-separated storefront codes. No cap: a scan may cover "
                  "every storefront.",
    )

    def clean_countries(self):
        """Parse and validate the picked storefronts.

        Unlike the keyword search there is no maximum and nothing is silently
        truncated: a scan may cover all 175 storefronts, and the page states
        what that costs in time before you start it. An empty selection is an
        error in the view, not a quiet fallback to the US.
        """
        raw = self.cleaned_data.get("countries", "").strip()
        return countries.clean(raw.split(",")) if raw else []
