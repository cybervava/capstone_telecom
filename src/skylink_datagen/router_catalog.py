"""The 12 fictional router models and their checkable facts (no real trademarks)."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RouterFacts:
    admin_url: str
    default_ssid_pattern: str
    power_led: str
    internet_led_name: str
    internet_led_ok: str
    internet_led_fault: str
    los_led_name: str | None
    reset_hold_seconds: int
    reboot_wait_seconds: int
    wps_button: bool
    firmware: str


@dataclass(frozen=True)
class RouterModel:
    model_id: str
    vendor: str
    name: str
    type: str
    facts: RouterFacts

    @property
    def has_ont(self) -> bool:
        return self.type == "ont_router"

    def as_json(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "vendor": self.vendor,
            "name": self.name,
            "type": self.type,
            "facts": asdict(self.facts),
        }


def _m(model_id: str, vendor: str, name: str, kind: str, *facts: object) -> RouterModel:
    return RouterModel(model_id, vendor, name, kind, RouterFacts(*facts))  # type: ignore[arg-type]


ONT, RTR = "ont_router", "router"

ROUTER_MODELS: tuple[RouterModel, ...] = (
    _m("AL-AX18", "Aerolink", "Aerolink AX1800", ONT, "http://192.168.1.1", "Aerolink-XXXX",
       "solid white", "INTERNET", "solid white", "solid red", "LOS", 10, 120, True, "AX18-2.4.7"),
    _m("AL-FX30", "Aerolink", "Aerolink FiberMax 3000", ONT, "http://192.168.0.1",
       "AL_FX30_XXXXXX", "solid green", "WAN", "solid green", "blinking amber", "PON-ALM", 15, 150,
       True, "FX30-1.9.2"),
    _m("AL-R12", "Aerolink", "Aerolink HomeRoute R12", RTR, "http://192.168.10.1",
       "AeroHome-XXXX", "solid blue", "GLOBE", "solid blue", "solid amber", None, 7, 60, False,
       "R12-3.0.5"),
    _m("NX-G520", "Nirvex", "Nirvex G520 Fibre Gateway", ONT, "http://192.168.29.1",
       "NIRVEX-G520-XXXX", "solid green", "INTERNET", "solid green", "not lit", "LOS", 20, 180,
       True, "G520-5.1.0"),
    _m("NX-R300", "Nirvex", "Nirvex R300 Mesh Router", RTR, "http://10.0.0.1", "Nirvex_Mesh_XXXX",
       "solid white", "STATUS", "solid white", "pulsing red", None, 12, 90, True, "R300-2.2.18"),
    _m("KV-ON10", "Kestrava", "Kestrava ON10 Optical Router", ONT, "http://192.168.100.1",
       "KESTRAVA-XXXXXX", "solid green", "NET", "solid green", "blinking red", "OPTICAL", 5, 240,
       False, "ON10-4.0.3"),
    _m("KV-HX6", "Kestrava", "Kestrava HX6 Wi-Fi 6 Router", RTR, "http://192.168.8.1",
       "Kestrava-HX6-XXXX", "solid green", "WAN", "solid green", "solid orange", None, 25, 75,
       True, "HX6-1.4.11"),
    _m("VT-F100", "Veltro", "Veltro F100 Fibre Hub", ONT, "http://192.168.2.1", "Veltro-F100-XXXX",
       "solid white", "INTERNET", "solid green", "blinking red", "LOS", 30, 45, True, "F100-7.3.1"),
    _m("VT-L5", "Veltro", "Veltro Linkstar L5", RTR, "http://192.168.31.1", "VeltroL5_XXXX",
       "solid green", "UPLINK", "solid white", "solid red", None, 8, 100, False, "L5-2.0.9"),
    _m("QL-ON4", "Quillon", "Quillon ON4 Home Gateway", ONT, "http://192.168.50.1",
       "QUILLON-ON4-XXXX", "solid green", "WAN", "solid blue", "blinking amber", "ALARM", 18, 210,
       True, "ON4-3.6.2"),
    _m("QL-M5", "Quillon", "Quillon M5 Dual-band Router", RTR, "http://172.16.0.1",
       "Quillon-M5-XXXX", "solid blue", "INTERNET", "solid blue", "blinking red", None, 35, 30,
       True, "M5-1.1.30"),
    _m("TG-FG7", "Tarang", "Tarang FG7 Fibre Router", ONT, "http://10.10.10.1", "TARANG-FG7-XXXX",
       "solid green", "INTERNET", "solid green", "solid red", "LOS", 40, 300, False, "FG7-6.2.4"),
)  # fmt: skip

MODEL_IDS: tuple[str, ...] = tuple(m.model_id for m in ROUTER_MODELS)
# Fibre customers mostly get an ONT-router; a minority have a separate ONT plus a plain router.
MODEL_WEIGHTS: tuple[int, ...] = tuple(3 if m.has_ont else 1 for m in ROUTER_MODELS)
