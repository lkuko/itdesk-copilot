"""Small local credential detector for text submitted through the workbench.

This is deliberately limited to explicit credential labels and familiar token
formats. It is not a general personal-information detector or a DLP system.
"""

import re

SENSITIVE_INPUT_MESSAGE = "检测到疑似密码、密钥或访问令牌，请删除敏感内容后重试。"
_TOKEN = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{16,}|"
    r"github_pat_[A-Za-z0-9_]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,})"
    r"|-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----"
)
_LABELED = re.compile(
    r"(?i)(?:API[ _-]?KEY|access[ _-]?token|refresh[ _-]?token|client[ _-]?secret|"
    r"password|passwd|token|secret|密码|口令|密钥|令牌|验证码|恢复码)"
    r"\s*(?:是|为|[:：=])\s*[\"'`]?([^\s，。；,;\"'`]+)"
)
_BENIGN = re.compile(
    r"^(?:错误|不正确|忘记|未知|未设置|未配置|已遮蔽|已隐藏|已删除|"
    r"incorrect|invalid|unknown|missing|redacted|hidden|none|null|false|true)$",
    re.I,
)
_CREDENTIAL_FIELD = re.compile(
    r"^(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"authorization|password|passwd|token|secret|密码|口令|密钥|令牌|验证码|恢复码)$",
    re.I,
)


def sensitive_input_reason(text: str) -> str | None:
    """Return a fixed explanation without including the matched credential."""
    if _TOKEN.search(text):
        return SENSITIVE_INPUT_MESSAGE
    for match in _LABELED.finditer(text):
        candidate = match.group(1).strip("[]【】<>《》")
        if candidate and not _BENIGN.fullmatch(candidate):
            return SENSITIVE_INPUT_MESSAGE
    return None


def redact_sensitive_text(text: str) -> str:
    """Mask familiar credentials in old records and upstream display content."""
    text = re.sub(
        r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----[\s\S]*?"
        r"(?:-----END(?: [A-Z]+)? PRIVATE KEY-----|$)",
        "[已遮蔽的私钥]",
        text,
    )

    def mask_labeled(match):
        candidate = match.group(1).strip("[]【】<>《》")
        if not candidate or _BENIGN.fullmatch(candidate):
            return match.group(0)
        return match.group(0)[:match.start(1) - match.start()] + "[已遮蔽]"

    return _TOKEN.sub("[已遮蔽]", _LABELED.sub(mask_labeled, text))


def redact_sensitive_value(value):
    """Recursively sanitize JSON display values without modifying their source."""
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {
            redact_sensitive_text(key) if isinstance(key, str) else key:
            "[已遮蔽]" if isinstance(key, str) and _CREDENTIAL_FIELD.fullmatch(key) and item
            else redact_sensitive_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)
    return value
