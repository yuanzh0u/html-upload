"""Local field validation for the V2 document contract."""
import re
import unicodedata

MAX_HTML = 2_097_152
MAX_BODY = 2_861_740
MAX_SUMMARY = 1000
MAX_TITLE = 200
MAX_TAGS = 20
MAX_TAG = 40
MAX_SHARE = 50
MAX_SOURCE_INFO = 4096


class FieldError(ValueError):
    """A Chinese validation message associated with a request field."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(message)


def clean_title(value) -> str:
    if not isinstance(value, str):
        raise FieldError('title', '标题必须是文本')
    value = ' '.join(value.split())
    if any(unicodedata.category(c) in {'Cc', 'Cs'}
           or c in '\u200b\u2060\ufeff'
           or 0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069 for c in value):
        raise FieldError('title', '标题含不允许的控制字符')
    if not value:
        raise FieldError('title', '标题不能为空')
    if len(value) > MAX_TITLE:
        raise FieldError('title', '标题超过 200 字')
    return value


def clean_summary(value) -> str:
    if value is None:
        raise FieldError('summary', '摘要不能为空')
    if not isinstance(value, str):
        raise FieldError('summary', '摘要必须是文本')
    if not value.strip():
        raise FieldError('summary', '摘要不能为空')
    if any(unicodedata.category(c) == 'Cc' and c not in '\n\t' for c in value):
        raise FieldError('summary', '摘要含不允许的控制字符')
    value = value.strip()
    if len(value) > MAX_SUMMARY:
        raise FieldError('summary', '摘要超过 1000 字')
    return value


def _clean_names(values, field, label, limit, max_length=None) -> list[str]:
    if not isinstance(values, list):
        raise FieldError(field, f'{label}必须是列表')
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            raise FieldError(field, f'{label}必须是文本')
        value = value.strip()
        if not value:
            raise FieldError(field, f'{label}不能为空')
        if max_length is not None and len(value) > max_length:
            raise FieldError(field, f'{label}超过 {max_length} 字')
        if value not in seen:
            seen.add(value)
            result.append(value)
    if len(result) > limit:
        raise FieldError(field, f'{label}超过 {limit} 项')
    return result


def clean_tags(values) -> list[str]:
    return _clean_names(values, 'tags', '标签', MAX_TAGS, MAX_TAG)


def clean_share(friends, groups) -> dict | None:
    friends = _clean_names(friends, 'share_with', '好友名称', MAX_SHARE)
    groups = _clean_names(groups, 'share_with', '群名称', MAX_SHARE)
    if not friends and not groups:
        return None
    return {'friends': friends, 'groups': groups}


def clean_doc_id(value) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is None:
        raise FieldError('doc_id', '文档 ID 须为 1–128 位字母、数字、下划线或连字符')
    return value
