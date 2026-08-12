from django import template

register = template.Library()


@register.filter(name="year_with_suffix")
def year_with_suffix(value):
    if value:
        return f"{value}年"
    else:
        return ""
