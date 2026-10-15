"""Laborator 2 SI - serverul MCP al clădirii inteligente, acum cu instrumente care modifică starea."""
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("smart-building-demo")

# Date de test, inventate pentru laborator.
DEVICES = {
    "lampa_led": {"categorie": "iluminat", "putere_w": 10, "note": "Se poate regla pe trepte de intensitate."},
    "radiator": {"categorie": "încălzire", "putere_w": 1500, "note": "Controlat de termostatul camerei."},
    "aer_conditionat": {"categorie": "climatizare", "putere_w": 2000, "note": "Nu se folosește cu ferestrele deschise."},
    "jaluzele": {"categorie": "umbrire", "putere_w": 30, "note": "Motor electric, consum doar la mișcare."},
    "ventilatie": {"categorie": "ventilație", "putere_w": 120, "note": "Aduce aer proaspăt din exterior."},
}
CONFLICTE = {
    frozenset({"radiator", "aer_conditionat"}): "RIDICAT - încălzire și răcire simultană, consum inutil.",
    frozenset({"aer_conditionat", "ventilatie"}): "MEDIU - aerul exterior scade eficiența climatizării.",
}

# Starea clădirii. Trăiește în procesul serverului, NU în conversația cu modelul:
# se pierde doar când serverul este oprit.
PORNITE: set[str] = set()
TERMOSTAT = {"temp_c": 21.0}

# Adnotări (indicii pentru gazdă, nu garanții): vezi secțiunea 4.2.
CITIRE = {"readOnlyHint": True}
MODIFICARE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
DISTRUGERE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}


def _necunoscut(name: str) -> dict:
    return {"eroare": f"Dispozitiv necunoscut: {name}", "cunoscute": sorted(DEVICES)}


# ---------- Instrumente de citire (ca în Laboratorul 1) ----------

@mcp.tool(annotations=CITIRE)
def list_devices() -> list[str]:
    """Returnează numele tuturor dispozitivelor cunoscute din clădire."""
    return sorted(DEVICES)


@mcp.tool(annotations=CITIRE)
def get_device_info(name: str) -> dict:
    """Returnează informații de bază despre un dispozitiv (categorie, putere în W, note)."""
    info = DEVICES.get(name.strip().lower())
    if info is None:
        return _necunoscut(name)
    return {"nume": name.strip().lower(), **info}


@mcp.tool(annotations=CITIRE)
def check_conflict(device_a: str, device_b: str) -> str:
    """Verifică dacă două dispozitive au un conflict de funcționare cunoscut."""
    cheie = frozenset({device_a.strip().lower(), device_b.strip().lower()})
    return CONFLICTE.get(cheie, "Niciun conflict înregistrat în setul de date de test.")


@mcp.tool(annotations=CITIRE)
def estimate_energy(name: str, hours: float) -> dict:
    """Estimează energia consumată (în kWh) de un dispozitiv care funcționează un număr de ore."""
    info = DEVICES.get(name.strip().lower())
    if info is None:
        return _necunoscut(name)
    return {
        "nume": name.strip().lower(),
        "ore": hours,
        "energie_kwh": round(info["putere_w"] * hours / 1000, 3),
    }


@mcp.tool(annotations=CITIRE)
def get_building_state() -> dict:
    """Returnează starea curentă a clădirii: dispozitivele pornite și temperatura dorită a termostatului."""
    return {"pornite": sorted(PORNITE), "termostat_c": TERMOSTAT["temp_c"]}


# ---------- Instrumente noi, cu efecte ----------

@mcp.tool(annotations=MODIFICARE)
def set_device_state(name: str, on: bool) -> dict:
    """Pornește (on=true) sau oprește (on=false) un dispozitiv.

    Refuză pornirea dacă ar crea un conflict de nivel RIDICAT cu un dispozitiv deja pornit.
    """
    nume = name.strip().lower()
    if nume not in DEVICES:
        return _necunoscut(name)
    if on:
        for altul in sorted(PORNITE):
            conflict = CONFLICTE.get(frozenset({nume, altul}), "")
            if conflict.startswith("RIDICAT"):
                return {"ok": False, "motiv": f"Conflict cu {altul}, care este pornit: {conflict}"}
        PORNITE.add(nume)
    else:
        PORNITE.discard(nume)
    return {"ok": True, "nume": nume, "pornit": nume in PORNITE}


@mcp.tool(annotations=MODIFICARE)
def set_thermostat(
    temp_c: Annotated[float, Field(ge=10, le=30, description="Temperatura dorită, în grade Celsius (10-30).")],
) -> dict:
    """Setează temperatura dorită a termostatului camerei."""
    TERMOSTAT["temp_c"] = temp_c
    return {"ok": True, "temp_c": temp_c}


@mcp.tool(annotations=DISTRUGERE)
def reset_building() -> dict:
    """Oprește toate dispozitivele și readuce termostatul la 21 °C. Șterge starea curentă a clădirii."""
    PORNITE.clear()
    TERMOSTAT["temp_c"] = 21.0
    return {"ok": True, "mesaj": "Clădirea a fost resetată."}


# ---------- Resursă și prompt (ca în Laboratorul 1) ----------

@mcp.resource("device://{name}/manual")
def manual(name: str) -> str:
    """Un manual (fictiv) de o linie, expus ca resursă."""
    info = DEVICES.get(name.lower())
    return f"{name}: {info['categorie']}. {info['note']}" if info else f"Nu există manual pentru {name}."


@mcp.prompt
def energy_review(devices: str) -> str:
    """Șablon de prompt: cere unui LLM să analizeze o listă de dispozitive."""
    return (
        f"Analizează această listă de dispozitive: {devices}. "
        "Folosește instrumentele pentru a verifica conflictele și a estima consumul."
    )


if __name__ == "__main__":
    mcp.run(show_banner=False)  # stdio, implicit
