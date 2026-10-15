"""Verifică dacă puteți vorbi cu modelul. Utilizare: python smoke_test.py [--models]"""
import sys

from openai import APIConnectionError, APIStatusError

from llm import create_chat, make_client

PROMPT = "Răspunde cu o singură propoziție scurtă: ce înseamnă MCP?"


def main():
    client, model, provider = make_client()
    print(f"Furnizor: {provider} | model: {model} | adresă: {client.base_url}")
    try:
        if "--models" in sys.argv:
            for m in client.models.list():
                print(" -", m.id)
            return
        reply = create_chat(client, model=model, messages=[{"role": "user", "content": PROMPT}])
        print(reply.choices[0].message.content)
    except APIStatusError as e:
        sys.exit(f"HTTP {e.status_code}: {str(e.body or e.message)[:300]}")
    except APIConnectionError:
        sys.exit("Nu mă pot conecta. Serverul local (Ollama/LM Studio) este pornit? Aveți internet?")


if __name__ == "__main__":
    main()
