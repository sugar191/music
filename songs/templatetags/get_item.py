# songs/templatetags/get_item.py
from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def attr(obj, attr_name):
    return getattr(obj, attr_name, "")
