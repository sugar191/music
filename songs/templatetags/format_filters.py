import math
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter(name="ceil")
def ceil(value):
    """
    切り上げて整数で表示する。
    カラオケ採点は小数を持つため、合計点をそのまま出すと桁が長くなる。
    floatformat:0 は四捨五入なので、切り上げにはこのフィルタを使う。
    値なし（None / 空文字）はそのまま空表示にする。
    """
    if value is None or value == "":
        return ""
    try:
        # float 経由の誤差で 1 大きくならないよう Decimal で受ける
        return math.ceil(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return value


@register.filter(name="year_with_suffix")
def year_with_suffix(value):
    if value:
        return f"{value}年"
    else:
        return ""
