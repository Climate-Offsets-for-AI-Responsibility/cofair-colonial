import re
 
ANTHROPIC = re.compile(r"^sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}$")
OPENAI = re.compile(r"^sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}$")
GOOGLE = re.compile(r"^AIza[0-9A-Za-z_-]{35}$")
MICROSOFT = re.compile(r"^(?:[0-9a-fA-F]{32}|[A-Za-z0-9]{80,90})$")
 
PATTERNS = {
    "anthropic": ANTHROPIC,
    "openai": OPENAI,
    "google": GOOGLE,
    'microsoft': MICROSOFT
}
 
 
def validate_key_format(key: str, provider: str) -> bool:
    # Return True if key matches the format for provider.
    provider = provider.strip().lower()
    if provider not in PATTERNS:
        raise ValueError(f"Unsupported provider '{provider}'. Use one of: {', '.join(PATTERNS)}")
    
    if not isinstance(key, str):
        return False
 
    key = key.strip()  # pasted keys often carry a trailing newline or space
 
    # Anthropic keys also start with "sk-", so an OpenAI pattern alone would
    # accept them. Rule them out explicitly.
    if provider == "openai" and key.startswith("sk-ant-"):
        return False
 
    return bool(PATTERNS[provider].fullmatch(key))
 
 
def detect_provider(key: str):
    # Guess which provider a key belongs to. Returns the name or None.
    for provider in PATTERNS:
        if validate_key_format(key, provider):
            return provider
    return None