from datetime import datetime, timezone as dt_timezone

from django import template
from django.utils import timezone

register = template.Library()


@register.filter(name="microtime")
def microtime(value):
    """把推进法 mtime 的微秒时间戳格式化成可读时刻；零尺位显示为初始尺位。"""
    if not value:
        return "初始尺位 0"
    try:
        dt = datetime.fromtimestamp(value / 1_000_000, tz=dt_timezone.utc)
        return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(value)
