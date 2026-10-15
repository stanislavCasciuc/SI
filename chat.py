"""Chatbot în linia de comandă, cu confirmare pentru instrumentele cu efecte. Rulare: python chat.py"""
import asyncio

from openai import APIConnectionError, APIStatusError

from agent import Agent


def cere_acord(nume: str, args: dict, distructiv: bool) -> bool:
    """Întreabă utilizatorul dacă agentul are voie să ruleze un instrument cu efecte."""
    eticheta = " [DISTRUCTIV]" if distructiv else ""
    raspuns = input(f"  ? Agentul vrea să ruleze {nume}({args}){eticheta}. Permiteți? [d/N] ")
    return raspuns.strip().lower() in ("d", "da", "y", "yes")


async def main():
    async with Agent(confirm=cere_acord) as agent:
        print(f"Furnizor: {agent.provider} | model: {agent.model}")
        print("Instrumente:", ", ".join(t["function"]["name"] for t in agent.tools))
        print(f"Fișier de log: {agent.log.path}")
        print("Comenzi: /reset (conversație nouă), /istoric, /iesire\n")
        while True:
            try:
                text = input("Tu: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text == "/iesire":
                break
            if text == "/reset":
                agent.reset()
                print("(conversație nouă; starea clădirii din server nu s-a schimbat)")
                continue
            if text == "/istoric":
                print(f"(istoric: {len(agent.messages)} mesaje)")
                continue
            if not text:
                continue
            try:
                print("Agent:", await agent.ask(text), "\n")
            except APIStatusError as e:
                print(f"Eroare HTTP {e.status_code}: {str(e.body or e.message)[:200]}\n")
            except APIConnectionError:
                print("Nu mă pot conecta la model (server oprit sau fără internet).\n")


if __name__ == "__main__":
    asyncio.run(main())
