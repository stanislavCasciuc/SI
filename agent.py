"""Agentul v2: buclă între model și instrumentele unui server MCP, cu fișier de log,
confirmare pentru instrumentele cu efecte și gestiunea istoricului."""
import json
import time
from datetime import datetime
from pathlib import Path

from fastmcp import Client

from llm import create_chat, make_client

SERVER = Path(__file__).parent / "server.py"
LOG_DIR = Path(__file__).parent / "logs"

MAX_STEPS = 6           # limită de siguranță pentru bucla de apeluri într-un singur răspuns
MAX_REPEAT = 2          # de câte ori poate fi cerut ACELAȘI apel (același nume și aceleași argumente)
MAX_TURNS = 6           # câte întrebări (ture) rămân în istoric
MAX_TOOL_CHARS = 1500   # lungimea maximă a unui rezultat de instrument păstrat în istoric
TOOL_CHOICE = "auto"    # auto | none | required - se aplică doar primului pas al fiecărei întrebări

SYSTEM_PROMPT = (
    "Ești asistentul unei clădiri inteligente. Răspunde concis, în limba română. "
    "Pentru informații despre dispozitive, conflicte, consum sau starea clădirii folosește "
    "instrumentele disponibile; nu inventa date. Dacă un instrument returnează o eroare sau "
    "utilizatorul refuză un apel, spune-o clar."
)


class JsonlLog:
    """Fișier de log în format JSONL: un eveniment = o linie JSON, adăugată la sfârșit."""

    def __init__(self, directory: Path = LOG_DIR):
        directory.mkdir(exist_ok=True)
        self.path = directory / f"agent-{datetime.now():%Y%m%d-%H%M%S}.jsonl"

    def write(self, tip: str, **date):
        eveniment = {"ts": datetime.now().isoformat(timespec="milliseconds"), "tip": tip, **date}
        # Deschidem și închidem fișierul la fiecare eveniment: nu se pierde nimic la o oprire bruscă.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(eveniment, ensure_ascii=False) + "\n")


def mcp_to_openai_tool(tool) -> dict:
    """Descrierea unui instrument MCP, în formatul cerut de modelul de limbaj."""
    # versiunile noi de FastMCP folosesc input_schema, cele vechi inputSchema
    schema = getattr(tool, "input_schema", None) or tool.inputSchema
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


def _hint(ann, snake: str, camel: str):
    """Valoarea unui indiciu; numele câmpului diferă între versiunile SDK-ului MCP."""
    if ann is None:
        return None
    for nume in (snake, camel):
        if hasattr(ann, nume):
            return getattr(ann, nume)
    return None


def tool_flags(tool) -> dict:
    """Indiciile (adnotările) unui instrument. Lipsa lor înseamnă: poate modifica și poate distruge."""
    ann = getattr(tool, "annotations", None)
    read_only = bool(_hint(ann, "read_only_hint", "readOnlyHint"))
    destructive = (not read_only) and _hint(ann, "destructive_hint", "destructiveHint") is not False
    return {"read_only": read_only, "destructive": destructive}


def truncate(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    """Taie un rezultat prea lung, spunând modelului că a fost tăiat."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... trunchiat: {len(text) - limit} caractere omise]"


def trim_history(messages: list[dict], max_turns: int = MAX_TURNS) -> list[dict]:
    """Păstrează mesajul system și ultimele `max_turns` ture.

    O tură începe la un mesaj `user` și conține tot ce urmează până la următorul `user`
    (inclusiv cererile de apel și rezultatele lor). Tăind doar la limita dintre ture,
    nu rămâne niciodată un rezultat de instrument fără cererea lui.
    """
    starts = [i for i, m in enumerate(messages) if m["role"] == "user"]
    if len(starts) <= max_turns:
        return messages
    return messages[:1] + messages[starts[-max_turns]:]


class Agent:
    def __init__(self, confirm=None):
        """`confirm(nume, argumente, distructiv) -> bool` decide dacă un instrument cu efecte
        poate rula. Fără `confirm`, instrumentele cu efecte sunt refuzate."""
        self.llm, self.model, self.provider = make_client()
        self.mcp = Client(SERVER)  # pornește server.py ca proces copil (stdio)
        self.confirm = confirm
        self.log = JsonlLog()
        self.tools = []
        self.flags = {}
        self.reset()

    def reset(self):
        """Conversație nouă: păstrăm doar instrucțiunile de sistem."""
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.log.write("reset")

    async def __aenter__(self):
        await self.mcp.__aenter__()
        found = await self.mcp.list_tools()
        self.tools = [mcp_to_openai_tool(t) for t in found]
        self.flags = {t.name: tool_flags(t) for t in found}
        self.log.write("pornire", furnizor=self.provider, model=self.model,
                       instrumente=sorted(self.flags))
        return self

    async def __aexit__(self, *exc):
        await self.mcp.__aexit__(*exc)

    async def call_tool(self, call, pas: int) -> str:
        """Execută un apel cerut de model: verificări, confirmare, apel MCP, log."""
        nume = call.function.name
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError as e:
            return self._rezultat(pas, nume, {}, f"Eroare: argumentele nu sunt JSON valid ({e}).", True)
        self.log.write("apel", pas=pas, nume=nume, argumente=args)

        flags = self.flags.get(nume)
        if flags is None:  # instrument care nu există în lista serverului
            text = f"Instrument necunoscut: {nume}. Disponibile: {', '.join(sorted(self.flags))}."
            return self._rezultat(pas, nume, args, text, True)

        if not flags["read_only"]:  # instrument cu efecte: cerem acordul utilizatorului
            aprobat = bool(self.confirm and self.confirm(nume, args, flags["destructive"]))
            self.log.write("confirmare", pas=pas, nume=nume, aprobat=aprobat)
            if not aprobat:
                return self._rezultat(pas, nume, args, "Utilizatorul a refuzat acest apel; nu a fost executat.", True)

        try:
            result = await self.mcp.call_tool(nume, args)
            text = "\n".join(b.text for b in result.content if hasattr(b, "text"))
            eroare = False
        except Exception as e:  # eroarea ajunge la model, care o poate explica
            text, eroare = f"Eroare la apelul {nume}: {e}", True
        return self._rezultat(pas, nume, args, text, eroare)

    def _rezultat(self, pas, nume, args, text, eroare) -> str:
        scurt = truncate(text)
        self.log.write("rezultat", pas=pas, nume=nume, eroare=eroare, text=scurt,
                       trunchiat=len(scurt) != len(text))
        print(f"  [instrument] {nume}({args}) -> {scurt[:150]}")
        return scurt

    def _final(self, text: str, **date) -> str:
        """Încheie întrebarea cu un mesaj al asistentului (și îl pune în istoric)."""
        self.messages.append({"role": "assistant", "content": text})
        self.log.write("raspuns", text=text, **date)
        return text

    async def ask(self, text: str) -> str:
        self.messages.append({"role": "user", "content": text})
        inainte = len(self.messages)
        self.messages = trim_history(self.messages)
        self.log.write("intrebare", text=text, mesaje_taiate=inainte - len(self.messages))

        vazute: dict[tuple, int] = {}  # (nume, argumente) -> de câte ori a fost cerut
        for pas in range(1, MAX_STEPS + 1):
            extra = {"tool_choice": TOOL_CHOICE} if pas == 1 and TOOL_CHOICE != "auto" else {}
            t0 = time.perf_counter()
            reply = create_chat(
                self.llm, model=self.model, messages=self.messages, tools=self.tools, **extra
            )
            usage = getattr(reply, "usage", None)
            self.log.write(
                "cerere_model", pas=pas, mesaje=len(self.messages),
                durata_ms=round((time.perf_counter() - t0) * 1000),
                prompt_tokens=getattr(usage, "prompt_tokens", None),
            )
            msg = reply.choices[0].message
            if not msg.tool_calls:  # răspuns final, în text
                return self._final(msg.content or "", pas=pas)

            # Detecția buclei: același apel cerut de prea multe ori în aceeași întrebare.
            chei = [(c.function.name, c.function.arguments) for c in msg.tool_calls]
            for cheie in chei:
                vazute[cheie] = vazute.get(cheie, 0) + 1
            if any(vazute[c] > MAX_REPEAT for c in chei):
                self.log.write("bucla_detectata", pas=pas, apeluri=[c[0] for c in chei])
                return self._final("(M-am oprit: modelul cere în mod repetat același apel.)", pas=pas)

            # Modelul cere instrumente: le păstrăm în istoric, le executăm, repetăm.
            self.messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [c.model_dump(exclude_none=True) for c in msg.tool_calls],
            })
            for call in msg.tool_calls:
                rezultat = await self.call_tool(call, pas)
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": rezultat})

        self.log.write("limita_pasi", pasi=MAX_STEPS)
        return self._final("(Am atins limita de pași a agentului.)")
