from django import forms
from .models import Rating


class RatingForm(forms.ModelForm):
    class Meta:
        model = Rating
        fields = ["score"]
        widgets = {
            "score": forms.NumberInput(attrs={"min": 0, "max": 100, "step": 1}),
        }


class InlineRatingForm(forms.ModelForm):
    score = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={"step": 1, "min": 0, "max": 100}),
    )

    class Meta:
        model = Rating
        fields = ["score"]
