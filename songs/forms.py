from django import forms
from .models import Rating


class InlineRatingForm(forms.ModelForm):
    score = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={"step": 1, "min": 0, "max": 100}),
    )

    class Meta:
        model = Rating
        fields = ["score"]
