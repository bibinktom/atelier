"""LLM Guard scanner singletons + secret redaction for the firewall sidecar.

Scanners are built once (model load is expensive). `warm()` runs during the
Docker build so the HuggingFace weights are baked into the image — the running
container then needs no network egress (read-only rootfs + offline env).

v1 controls (deliberately Presidio-free so the build stays robust):
  - input:  PromptInjection (DeBERTa, the headline ML control) + optional Toxicity.
            A plain length cap lives in main.py instead of the tiktoken-based
            TokenLimit scanner, so the container needs no tiktoken download.
  - output: secret redaction (our own global re.sub — see below) + Toxicity flag.

We do NOT use llm-guard's Regex output scanner for secret redaction: it replaces
only the FIRST match in the text (verified empirically), so a message leaking two
keys would keep the second. Our `redact_secrets()` uses re.subn for a true global
scrub. Presidio-based PII redaction (Sensitive/Anonymize) is a phase-2 follow-up.
"""
import os
import re

from llm_guard.input_scanners import PromptInjection
from llm_guard.input_scanners import Toxicity as InputToxicity
from llm_guard.input_scanners.prompt_injection import MatchType as PIMatchType
from llm_guard.output_scanners import Sensitive
from llm_guard.output_scanners import Toxicity as OutputToxicity

INPUT_THRESHOLD = float(os.environ.get("FIREWALL_INPUT_THRESHOLD", "0.85"))
TOXICITY_INPUT = os.environ.get("FIREWALL_TOXICITY_INPUT", "0") not in {"0", "false", "False", ""}
PII_OUTPUT = os.environ.get("FIREWALL_PII_OUTPUT", "1") not in {"0", "false", "False", ""}

# Curated high-confidence PII entities for output redaction. PERSON/LOCATION/DATE
# are deliberately EXCLUDED — redacting every name in a normal answer ("tell me
# about Einstein") would wreck the assistant. These are the categories where a hit
# is almost always genuinely sensitive.
_PII_ENTITIES = [
    "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN",
    "IBAN_CODE", "CRYPTO", "IP_ADDRESS",
]

# Credential/secret shapes to scrub from model output (most-specific first).
_SECRET_ALTERNATIVES = [
    r"sk-or-v1-[A-Za-z0-9]{16,}",                          # OpenRouter keys
    r"sk-[A-Za-z0-9]{16,}",                                # OpenAI-style keys
    r"nvapi-[A-Za-z0-9_\-]{16,}",                          # NVIDIA NIM keys
    r"tvly-[A-Za-z0-9_\-]{16,}",                           # Tavily keys
    r"gh[pousr]_[A-Za-z0-9]{20,}",                         # GitHub tokens
    r"AKIA[0-9A-Z]{16}",                                   # AWS access key id
    r"AIza[0-9A-Za-z_\-]{35}",                             # Google API key
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",                      # Slack tokens
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",  # private keys
]
SECRET_RE = re.compile("|".join(f"(?:{p})" for p in _SECRET_ALTERNATIVES))

# Presidio's US_SSN recognizer scores bare SSNs too low to redact without context,
# so we catch the standard NNN-NN-NNNN shape deterministically as a PII backstop.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

_pi_scanner = None          # shared DeBERTa PromptInjection (loaded once)
_input_scanners = None
_output_scanners_pii = None      # toxicity + Sensitive (PII)
_output_scanners_nopii = None    # toxicity only
_tool_scanners = None


def redact_secrets(text: str) -> tuple[str, int]:
    """Globally replace every credential-shaped substring with [REDACTED].
    Returns (sanitized_text, num_redactions)."""
    return SECRET_RE.subn("[REDACTED]", text or "")


def redact_ssn(text: str, pii: bool | None = None) -> tuple[str, int]:
    """Deterministic PII backstop for US SSNs. Returns (sanitized, count).
    `pii` overrides the global default per request (per-user policy)."""
    enabled = PII_OUTPUT if pii is None else bool(pii)
    if not enabled:
        return text or "", 0
    return _SSN_RE.subn("<US_SSN>", text or "")


def _prompt_injection():
    """One shared PromptInjection instance — used by both input and tool scanning
    so the DeBERTa model is loaded into memory only once."""
    global _pi_scanner
    if _pi_scanner is None:
        _pi_scanner = PromptInjection(threshold=INPUT_THRESHOLD, match_type=PIMatchType.FULL)
    return _pi_scanner


def input_scanners():
    global _input_scanners
    if _input_scanners is None:
        scanners = [_prompt_injection()]
        if TOXICITY_INPUT:
            scanners.append(InputToxicity())
        _input_scanners = scanners
    return _input_scanners


def tool_scanners():
    """Prompt-injection only — for scanning untrusted tool-result text (indirect
    injection). No toxicity (fetched pages legitimately contain blunt language)."""
    global _tool_scanners
    if _tool_scanners is None:
        _tool_scanners = [_prompt_injection()]
    return _tool_scanners


def output_scanners(pii: bool | None = None):
    """Output scanners. `pii` overrides the global PII_OUTPUT default per request:
    True → toxicity + Sensitive (PII redaction), False → toxicity only. Two cached
    lists so the Sensitive model is still loaded once and shared."""
    global _output_scanners_pii, _output_scanners_nopii
    enabled = PII_OUTPUT if pii is None else bool(pii)
    if enabled:
        if _output_scanners_pii is None:
            _output_scanners_pii = [
                OutputToxicity(),
                Sensitive(entity_types=_PII_ENTITIES, redact=True),
            ]
        return _output_scanners_pii
    if _output_scanners_nopii is None:
        _output_scanners_nopii = [OutputToxicity()]
    return _output_scanners_nopii


def warm():
    """Instantiate every scanner so model weights download into the baked HF cache."""
    input_scanners()
    tool_scanners()
    output_scanners(pii=True)    # bakes both toxicity + Sensitive (PII NER) weights
    output_scanners(pii=False)
    redact_secrets("warm sk-0000000000000000")
    print("[guard] scanners warmed")
