"""Taxonomy normalization copied behaviorally from the locked P3 contract."""
import re
import unicodedata


def normalize(value):
    """Return (display name, casefold key); reject invalid term names."""
    if not isinstance(value, str) or '\0' in value:
        raise ValueError('词条必须是无 NUL 的字符串')
    value = unicodedata.normalize('NFKC', value).strip()
    value = re.sub(r'[ \t\r\n\v\f\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]+', ' ', value)
    if (not value or len(value) > 80 or len(value.casefold()) > 80
            or any(unicodedata.category(x) in ('Cc', 'Cf', 'Cs') for x in value)):
        raise ValueError('词条为空、超过 80 码位或含控制字符')
    return value, value.casefold()
