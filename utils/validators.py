"""utils/validators.py — small reusable input validators."""
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[6-9]\d{9}$")  # Indian mobile numbers


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def is_valid_indian_phone(phone: str) -> bool:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return bool(PHONE_RE.match(digits))


def sanitize_text(text: str, max_len: int = 2000) -> str:
    if not text:
        return ""
    text = text.strip()[:max_len]
    return text
