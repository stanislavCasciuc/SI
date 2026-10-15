"""Furnizorul "mock": un model de limbaj FALS, fără rețea și fără cheie API.

Nu înțelege limba română; decide ce instrument să ceară după câteva reguli simple
(cuvinte-cheie) sau după o comandă explicită. Servește pentru teste deterministe.
IMPORTANT: nu citește descrierile instrumentelor, deci nu poate arăta efectul unui
docstring bun sau prost - pentru asta aveți nevoie de un model real.
"""
import json
import re
import unicodedata
from itertools import count
from types import SimpleNamespace

# nume canonic -> forme (fără diacritice) sub care apare în întrebări
DISPOZITIVE = {
    "lampa_led": ["lampa", "led"],
    "radiator": ["radiator", "calorifer"],
    "aer_conditionat": ["aer conditionat", "aerul conditionat", "aer_conditionat"],
    "jaluzele": ["jaluzele", "jaluzelele"],
    "ventilatie": ["ventilatie", "ventilatia"],
    "frigider": ["frigider", "frigiderul"],  # nu există în server: pentru testul erorilor
}
_ids = count(1)


def norm(text: str) -> str:
    """Litere mici, fără diacritice."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def gaseste_dispozitive(t: str) -> list[str]:
    """Dispozitivele din text normalizat, în ordinea apariției."""
    gasite = []
    for nume, forme in DISPOZITIVE.items():
        poz = [m.start() for f in forme for m in re.finditer(r"\b" + re.escape(f), t)]
        if poz:
            gasite.append((min(poz), nume))
    return [nume for _, nume in sorted(gasite)]


def numar(pattern: str, t: str):
    m = re.search(pattern, t)
    return float(m.group(1).replace(",", ".")) if m else None


def planifica(text: str, unelte: set[str]) -> list[tuple[str, dict]]:
    """Lista de apeluri (nume, argumente) pe care le 'decide' mock-ul pentru o întrebare."""
    t = norm(text)

    # 1. Comandă explicită:  apel: nume {"arg": 1} ; alt_nume {...}
    if t.startswith("apel:"):
        apeluri = []
        for parte in text.split(":", 1)[1].split(";"):
            nume, _, rest = parte.strip().partition(" ")
            apeluri.append((nume, json.loads(rest) if rest.strip() else {}))
        return apeluri

    # 2. Buclă artificială (pentru testul de detecție a buclei)
    if "bucla" in t:
        return [("list_devices", {})]

    # 3. Reguli cu cuvinte-cheie
    dispozitive = gaseste_dispozitive(t)
    ore = numar(r"(\d+(?:[.,]\d+)?)\s*(?:de\s+)?or[ae]?\b", t)
    apeluri = []
    if ("conflict" in t or "acelasi timp" in t or "impreuna" in t) and len(dispozitive) >= 2:
        apeluri.append(("check_conflict", {"device_a": dispozitive[0], "device_b": dispozitive[1]}))
    if any(c in t for c in ("consum", "kwh", "energie")) and dispozitive:
        for d in dispozitive[:2]:
            apeluri.append(("estimate_energy", {"name": d, "hours": ore if ore is not None else 1.0}))
    if dispozitive and any(c in t for c in ("porneste", "aprinde", "activeaza", "deschide")):
        apeluri.append(("set_device_state", {"name": dispozitive[0], "on": True}))
    if dispozitive and any(c in t for c in ("opreste", "stinge", "inchide", "dezactiveaza")):
        apeluri.append(("set_device_state", {"name": dispozitive[0], "on": False}))
    grade = numar(r"(\d+(?:[.,]\d+)?)\s*(?:de\s+)?(?:grade|°)", t)
    if grade is not None and ("termostat" in t or "temperatura" in t or "grade" in t):
        apeluri.append(("set_thermostat", {"temp_c": grade}))
    if "reseteaza" in t or "resetare" in t:
        apeluri.append(("reset_building", {}))
    if "stare" in t or "ce este pornit" in t or "ce e pornit" in t:
        apeluri.append(("get_building_state", {}))
    if "ce dispozitive" in t or "lista" in t:
        apeluri.append(("list_devices", {}))
    if dispozitive and ("informatii" in t or "despre" in t) and not apeluri:
        apeluri.append(("get_device_info", {"name": dispozitive[0]}))
    # Regulile cer doar instrumente care există; comanda explicită (1) nu filtrează.
    return [(n, a) for n, a in apeluri if n in unelte]


class _Apel:
    """Un apel de instrument, cu aceleași câmpuri ca în răspunsul unui model real."""

    def __init__(self, nume: str, argumente: dict):
        self.id = f"mock_{next(_ids)}"
        self.type = "function"
        self.function = SimpleNamespace(
            name=nume, arguments=json.dumps(argumente, ensure_ascii=False)
        )

    def model_dump(self, exclude_none=True):
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


def _raspuns(mesaje, continut=None, apeluri=None):
    mesaj = SimpleNamespace(role="assistant", content=continut, tool_calls=apeluri or None)
    chars = len(json.dumps(mesaje, ensure_ascii=False, default=str))
    usage = SimpleNamespace(prompt_tokens=chars // 4, completion_tokens=20)  # estimare grosieră
    return SimpleNamespace(choices=[SimpleNamespace(message=mesaj)], usage=usage)


class _Completions:
    def create(self, model=None, messages=None, tools=None, tool_choice=None, **_):
        unelte = {t["function"]["name"] for t in (tools or [])}
        ultimul = messages[-1]

        # a) Tocmai au sosit rezultate de instrumente: formulăm răspunsul final.
        if ultimul["role"] == "tool":
            ultimul_user = next(m for m in reversed(messages) if m["role"] == "user")
            if "bucla" in norm(ultimul_user["content"]) and "list_devices" in unelte:
                return _raspuns(messages, apeluri=[_Apel("list_devices", {})])
            rezultate = []
            for m in reversed(messages):
                if m["role"] != "tool":
                    break
                rezultate.append(m["content"])
            linii = "\n".join(f"- {r}" for r in reversed(rezultate))
            return _raspuns(messages, continut=f"[mock] Rezultatele instrumentelor:\n{linii}")

        # b) Mesaj nou de la utilizator.
        text = ultimul["content"]
        if tool_choice == "none":
            return _raspuns(messages, continut="[mock] (am răspuns fără instrumente, tool_choice=none)")
        try:
            apeluri = planifica(text, unelte)
        except (ValueError, json.JSONDecodeError):
            return _raspuns(messages, continut="[mock] Comandă 'apel:' invalidă. Format: apel: nume {json}")
        if not apeluri and tool_choice == "required" and "list_devices" in unelte:
            apeluri = [("list_devices", {})]  # 'required' obligă modelul să ceară ceva
        if not apeluri:
            return _raspuns(messages, continut="[mock] Nu am nevoie de instrumente pentru asta.")
        return _raspuns(messages, apeluri=[_Apel(n, a) for n, a in apeluri])


class MockClient:
    """Imită partea din clientul OpenAI pe care o folosim: chat.completions.create."""

    base_url = "mock://local"

    def __init__(self):
        self.chat = SimpleNamespace(completions=_Completions())
        self.models = SimpleNamespace(list=lambda: [SimpleNamespace(id="mock-1")])
