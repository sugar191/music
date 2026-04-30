from django import forms
from .models import Rating


class InlineRatingForm(forms.ModelForm):
    score = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={"step": 1, "min": 0, "max": 100}),
    )
    karaoke_score = forms.DecimalField(
        required=False,
        max_digits=6,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"step": "0.001", "min": 0, "max": 100}),
    )

    class Meta:
        model = Rating
        fields = ["score", "karaoke_score"]
