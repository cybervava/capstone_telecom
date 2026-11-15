"""Router troubleshooting guides: same structure for every model, different checkable facts.

Fact -> section placement (each guide states the fact in that section):
overview: firmware, admin_url | leds: power_led, internet_led_*, los_led_name |
power-cycle: reboot_wait_seconds | no-internet: internet_led_fault, los_led_name |
wifi: default_ssid_pattern, wps_button | slow-speed: admin_url | factory-reset: reset_hold_seconds
"""

from __future__ import annotations

import random

from skylink_datagen.router_catalog import RouterModel

SECTIONS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("leds", "LED indicators"),
    ("power-cycle", "Power cycle"),
    ("no-internet", "No internet"),
    ("wifi", "Wi-Fi problems"),
    ("slow-speed", "Slow speed"),
    ("factory-reset", "Factory reset"),
    ("escalation", "When to call a technician"),
)
SAFETY = (
    "Safety first:\n\n"
    "- Never open the casing. There are no user-serviceable parts inside.\n"
    "- Never look into a fibre connector or the end of the fibre cable. The laser light is "
    "invisible and can damage your eyes.\n"
    "- Use only the supplied power adapter and keep the unit somewhere dry and ventilated."
)


def _steps(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))


def _blocks(*parts: str) -> str:
    return "\n\n".join(parts)


def _overview(m: RouterModel, rng: random.Random) -> str:
    f = m.facts
    if m.has_ont:
        kind = "a combined fibre ONT and Wi-Fi router: the green fibre cable plugs straight into it"
    else:
        kind = "a Wi-Fi router that connects by Ethernet to a separate SkyLink fibre ONT box"
    lead = rng.choice(("This guide covers", "Use this guide for"))
    facts = "\n".join(
        [
            f"- Current SkyLink firmware: **{f.firmware}**. Updates are pushed automatically.",
            f"- Admin page: `{f.admin_url}` (works only from a device on your home network).",
            "- The model code and serial number are printed on the label underneath the unit.",
        ]
    )
    intro = f"{lead} the {m.name} (model code {m.model_id}) from {m.vendor}. It is {kind}."
    return _blocks(intro, facts, SAFETY)


def _los_note(m: RouterModel) -> str:
    los = m.facts.los_led_name
    if los:
        return (
            f"The {los} light is normally off. If the {los} light is red or blinking, the "
            "optical signal is not reaching your home."
        )
    return (
        "This model has no LOS light. The fibre signal is shown on the separate SkyLink ONT "
        "box, so check the lights on that box as well."
    )


def _leds(m: RouterModel, rng: random.Random) -> str:
    f = m.facts
    rows = [
        "| Light | What you see | Meaning |",
        "|---|---|---|",
        f"| Power | {f.power_led} | The unit is powered on normally. |",
        f"| {f.internet_led_name} | {f.internet_led_ok} | Connected to the SkyLink network. |",
        f"| {f.internet_led_name} | {f.internet_led_fault} | No internet connection. |",
    ]
    if f.los_led_name:
        rows.append(f"| {f.los_led_name} | red or blinking red | Fibre signal lost (LOS). |")
    summary = (
        f"Power light: {f.power_led} when everything is normal. The internet light is labelled "
        f"**{f.internet_led_name}**: {f.internet_led_ok} means connected, "
        f"{f.internet_led_fault} means there is no internet connection."
    )
    intro = rng.choice(("Lights on the front panel:", "What the front-panel lights mean:"))
    return _blocks(intro, "\n".join(rows), summary, _los_note(m))


def _power_cycle(m: RouterModel, rng: random.Random) -> str:
    f = m.facts
    steps = [
        "Switch the router off at the wall socket and unplug the power adapter.",
        "Leave it unplugged for a short while. Do not unplug the fibre cable.",
        "Plug the adapter back in and switch on at the wall.",
        f"Wait {f.reboot_wait_seconds} seconds for the router to finish starting up. "
        "Do not press any buttons during this time.",
        f"Check that the {f.internet_led_name} light shows {f.internet_led_ok}.",
    ]
    once = rng.choice(("Do it once", "Try it once"))
    intro = f"A power cycle clears most temporary faults. {once} before any other step."
    return _blocks(intro, _steps(steps))


def _fibre_check(m: RouterModel) -> str:
    los = m.facts.los_led_name
    if los:
        return (
            f"Look at the {los} light. If the {los} light is red or blinking, this is a fibre "
            "(LOS) fault on the line, not a router fault. Stop here: do not reset the router, "
            "and report the fault to SkyLink."
        )
    return (
        "Look at the LOS light on the separate SkyLink ONT box. If it is red or blinking, this "
        "is a fibre fault on the line, not a router fault. Stop here and report the fault; "
        "resetting this router will not help."
    )


def _no_internet(m: RouterModel, rng: random.Random) -> str:
    f = m.facts
    name, fault = f.internet_led_name, f.internet_led_fault
    steps = [
        f"Check the {name} light. If it shows {f.internet_led_ok}, the line is up; "
        "see Wi-Fi problems.",
        f"If the {name} light is {fault}, note the time it started.",
        _fibre_check(m),
        "Check that the fibre cable is not bent sharply or crushed. Do not disconnect it.",
        "Do one power cycle (see Power cycle).",
        f"If the {name} light is still {fault} and the fibre signal is fine, the router may be "
        "faulty. Stop and contact SkyLink support rather than trying a factory reset yourself.",
    ]
    tip = rng.choice(
        (
            "If several neighbours are affected too, it is almost certainly a line fault.",
            "If neighbours on the same street have the same problem, it is likely a line fault.",
        )
    )
    intro = "Work out whether the problem is the fibre line or the router itself."
    return _blocks(intro, _steps(steps), tip)


def _wifi(m: RouterModel, rng: random.Random) -> str:
    f = m.facts
    if f.wps_button:
        wps = (
            "This model has a WPS button: press the WPS button, then choose WPS on your device "
            "within two minutes."
        )
    else:
        wps = "This model has no WPS button, so connect by typing the Wi-Fi password."
    steps = [
        f"Find the network name. The default name follows the pattern "
        f"`{f.default_ssid_pattern}`, where X is replaced by characters printed on the label.",
        "Check the password on the label, or the one you set yourself. Passwords are "
        "case-sensitive.",
        wps,
        "Move closer to the router. Walls, mirrors and microwaves weaken the signal.",
        "Forget the network on your device, restart the device and join again.",
    ]
    tip = rng.choice(
        (
            "The 5 GHz band is faster but has shorter range than 2.4 GHz.",
            "Use 2.4 GHz for range and 5 GHz for speed close to the router.",
        )
    )
    intro = "If the internet light is normal but devices cannot connect over Wi-Fi:"
    return _blocks(intro, _steps(steps), tip)


def _slow_speed(m: RouterModel, rng: random.Random) -> str:
    steps = [
        "Run a speed test on a laptop connected by an Ethernet cable, close to the router.",
        f"Open the admin page at `{m.facts.admin_url}` and check how many devices are connected.",
        "Pause large downloads, cloud backups and video streams on other devices.",
        "Do one power cycle if the router has been on for many weeks.",
        "Compare the wired result with the speed of your SkyLink package. If the wired result "
        "is far below it at different times of day, contact support.",
    ]
    tip = rng.choice(
        (
            "Older phones and laptops may not reach full speed over Wi-Fi.",
            "Older devices may cap Wi-Fi speed regardless of the connection.",
        )
    )
    intro = "Speeds over Wi-Fi are always lower than the speed delivered to the router."
    return _blocks(intro, _steps(steps), tip)


def _factory_reset(m: RouterModel, rng: random.Random) -> str:
    warning = (
        "**Warning:** a factory reset erases all your settings: the Wi-Fi name and password, "
        "port forwarding and any parental controls. Every device will need to reconnect. Only "
        "reset when SkyLink support asks you to, and never when the fibre signal light shows "
        "a fault."
    )
    steps = [
        "Leave the router switched on.",
        "Find the small recessed RESET hole on the back panel.",
        f"Press and hold the button with a straightened paperclip for "
        f"{m.facts.reset_hold_seconds} seconds, until the lights flash.",
        "Release the button and wait for the router to restart fully.",
        "Reconnect using the default network name and password printed on the label.",
    ]
    tip = rng.choice(
        (
            "Holding the button for less time only restarts the router.",
            "A shorter press only restarts the router and keeps your settings.",
        )
    )
    return _blocks(warning, _steps(steps), tip)


def _escalation(m: RouterModel, rng: random.Random) -> str:
    when = [
        "The fibre signal light still shows a fault after one power cycle.",
        "The fibre cable or wall socket is visibly damaged.",
        "The power light stays off with a socket you know works.",
        "The router keeps restarting by itself.",
    ]
    ready = [
        "Your registered mobile number or customer ID.",
        f"The model code ({m.model_id}) and serial number from the label.",
        "Which lights are on, off or blinking, and their colours.",
        "What you have already tried, and when the problem started.",
    ]
    tip = rng.choice(
        (
            "Please do not book a technician during a known area outage; it will not help.",
            "If there is a reported outage in your area, wait for it to be restored first.",
        )
    )
    intro = (
        "A technician visit is needed when the fault is in the line or the hardware, not in "
        "settings. Request one if:"
    )
    have = "Have this ready when you contact SkyLink:"
    return _blocks(intro, _steps(when), have, _steps(ready), tip)


RENDERERS = (
    _overview,
    _leds,
    _power_cycle,
    _no_internet,
    _wifi,
    _slow_speed,
    _factory_reset,
    _escalation,
)


def _front_matter(model: RouterModel, rng: random.Random) -> str:
    version = rng.choice(("1.0", "1.1", "1.2", "1.3"))
    lines = [
        "---",
        f"doc_id: guide-{model.model_id.lower()}",
        "doc_type: device_guide",
        f"title: {model.name} troubleshooting guide",
        f"device_model: {model.model_id}",
        f"vendor: {model.vendor}",
        f'version: "{version}"',
        f"effective_from: 2026-0{rng.randint(1, 4)}-01",
        "access_roles: [customer, agent, supervisor]",
        "---",
    ]
    return "\n".join(lines) + "\n"


def render_guide(model: RouterModel, rng: random.Random) -> str:
    parts = [_front_matter(model, rng), f"# {model.name} troubleshooting guide\n"]
    for (section_id, heading), renderer in zip(SECTIONS, RENDERERS, strict=True):
        parts.append(f"## {heading} {{#{section_id}}}\n\n{renderer(model, rng)}\n")
    return "\n".join(parts)
