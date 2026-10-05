"""Remove secrets from text before it is archived."""

import re

REDACTED = "[redacted]"

# Tokens whose shape alone identifies them.
_TOKENS = re.compile(
    r"""
      sk-ant-[A-Za-z0-9_-]{20,}
    | sk-(?:proj-)?[A-Za-z0-9_-]{32,}
    | gh[pousr]_[A-Za-z0-9]{36,}
    | github_pat_[A-Za-z0-9_]{40,}
    | (?:AKIA|ASIA)[0-9A-Z]{16}
    | xox[abprs]-[A-Za-z0-9-]{10,}
    | [sr]k_live_[A-Za-z0-9]{16,}
    | AIza[0-9A-Za-z_-]{35}
    | eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}
    | -----BEGIN[ A-Z]*PRIVATE[ ]KEY-----.*?-----END[ A-Z]*PRIVATE[ ]KEY-----
    """,
    re.VERBOSE | re.DOTALL,
)
# NAME=value, where the name says the value is a secret. Short values are left
# alone so that settings such as MAX_TOKENS = 4096 survive.
_ASSIGNED = re.compile(
    r"\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*\s*[:=]\s*[\"']?)"
    r"[A-Za-z0-9+/_.=-]{12,}"
)
# scheme://user:password@host
_URL_PASSWORD = re.compile(r"(://[^/\s:@]+:)[^@\s/]{3,}(?=@)")


def redact(text: str) -> tuple[str, int]:
    """The text with each secret replaced by a marker, and how many were replaced."""
    text, tokens = _TOKENS.subn(REDACTED, text)
    text, assigned = _ASSIGNED.subn(rf"\1{REDACTED}", text)
    text, passwords = _URL_PASSWORD.subn(rf"\1{REDACTED}", text)
    return text, tokens + assigned + passwords
