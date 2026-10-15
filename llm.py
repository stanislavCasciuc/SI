"""Conectorul către modelul de limbaj.

Toți furnizorii de mai jos expun un API compatibil cu cel OpenAI, deci folosim
același client și schimbăm doar adresa de bază, cheia și numele modelului.
"""
import os
import time

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI

load_dotenv()

PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "model": "gemini-3.5-flash-lite",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY",
        "model": "mistral-small-latest",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env": None,  # serverul local nu validează cheia
        "model": "qwen3:1.7b",
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "key_env": None,
        "model": None,  # se specifică în LLM_MODEL (modelul încărcat în LM Studio)
    },
}

# Erori care merită reîncercate: serverele supraîncărcate dau de obicei 503 (uneori și 403),
# iar 429 înseamnă cotă depășită.
RETRY_STATUS = {403, 429, 500, 503}


def make_client():
    """Returnează (client, model, nume_furnizor), conform variabilelor de mediu."""
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
    if provider == "mock":  # NOU: model fals, fără rețea și fără cheie
        from mock_llm import MockClient  # NOU
        return MockClient(), "mock-1", "mock"  # NOU
    if provider not in PROVIDERS:
        raise SystemExit(f"LLM_PROVIDER necunoscut: {provider}. Alegeți: {', '.join(PROVIDERS)}")
    cfg = PROVIDERS[provider]

    if cfg["key_env"]:
        api_key = os.environ.get(cfg["key_env"])
        if not api_key:
            raise SystemExit(f"Variabila {cfg['key_env']} nu este setată (vezi fișierul .env).")
    else:
        api_key = "local"  # valoare oarecare, cerută de biblioteca clientului

    model = os.environ.get("LLM_MODEL") or cfg["model"]
    if not model:
        raise SystemExit("Setați LLM_MODEL (numele modelului încărcat în serverul local).")

    base_url = os.environ.get("LLM_BASE_URL") or cfg["base_url"]
    # max_retries=0: reîncercările le facem noi, în create_chat, ca să le vedem.
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=120, max_retries=0)
    return client, model, provider


def create_chat(client, retries=3, **kwargs):
    """Apelează modelul; la erori temporare reîncearcă cu pauze tot mai lungi."""
    delay = 2
    for attempt in range(retries + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except APIStatusError as e:
            if e.status_code in RETRY_STATUS and attempt < retries:
                print(f"[conector] HTTP {e.status_code}; reîncerc peste {delay} s...")
                time.sleep(delay)
                delay *= 2
            else:
                raise
