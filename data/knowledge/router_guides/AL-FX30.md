---
doc_id: guide-al-fx30
doc_type: device_guide
title: Aerolink FiberMax 3000 troubleshooting guide
device_model: AL-FX30
vendor: Aerolink
version: "1.2"
effective_from: 2026-03-01
access_roles: [customer, agent, supervisor]
---

# Aerolink FiberMax 3000 troubleshooting guide

## Overview {#overview}

Use this guide for the Aerolink FiberMax 3000 (model code AL-FX30) from Aerolink. It is a combined fibre ONT and Wi-Fi router: the green fibre cable plugs straight into it.

- Current SkyLink firmware: **FX30-1.9.2**. Updates are pushed automatically.
- Admin page: `http://192.168.0.1` (works only from a device on your home network).
- The model code and serial number are printed on the label underneath the unit.

Safety first:

- Never open the casing. There are no user-serviceable parts inside.
- Never look into a fibre connector or the end of the fibre cable. The laser light is invisible and can damage your eyes.
- Use only the supplied power adapter and keep the unit somewhere dry and ventilated.

## LED indicators {#leds}

Lights on the front panel:

| Light | What you see | Meaning |
|---|---|---|
| Power | solid green | The unit is powered on normally. |
| WAN | solid green | Connected to the SkyLink network. |
| WAN | blinking amber | No internet connection. |
| PON-ALM | red or blinking red | Fibre signal lost (LOS). |

Power light: solid green when everything is normal. The internet light is labelled **WAN**: solid green means connected, blinking amber means there is no internet connection.

The PON-ALM light is normally off. If the PON-ALM light is red or blinking, the optical signal is not reaching your home.

## Power cycle {#power-cycle}

A power cycle clears most temporary faults. Try it once before any other step.

1. Switch the router off at the wall socket and unplug the power adapter.
2. Leave it unplugged for a short while. Do not unplug the fibre cable.
3. Plug the adapter back in and switch on at the wall.
4. Wait 150 seconds for the router to finish starting up. Do not press any buttons during this time.
5. Check that the WAN light shows solid green.

## No internet {#no-internet}

Work out whether the problem is the fibre line or the router itself.

1. Check the WAN light. If it shows solid green, the line is up; see Wi-Fi problems.
2. If the WAN light is blinking amber, note the time it started.
3. Look at the PON-ALM light. If the PON-ALM light is red or blinking, this is a fibre (LOS) fault on the line, not a router fault. Stop here: do not reset the router, and report the fault to SkyLink.
4. Check that the fibre cable is not bent sharply or crushed. Do not disconnect it.
5. Do one power cycle (see Power cycle).
6. If the WAN light is still blinking amber and the fibre signal is fine, the router may be faulty. Stop and contact SkyLink support rather than trying a factory reset yourself.

If neighbours on the same street have the same problem, it is likely a line fault.

## Wi-Fi problems {#wifi}

If the internet light is normal but devices cannot connect over Wi-Fi:

1. Find the network name. The default name follows the pattern `AL_FX30_XXXXXX`, where X is replaced by characters printed on the label.
2. Check the password on the label, or the one you set yourself. Passwords are case-sensitive.
3. This model has a WPS button: press the WPS button, then choose WPS on your device within two minutes.
4. Move closer to the router. Walls, mirrors and microwaves weaken the signal.
5. Forget the network on your device, restart the device and join again.

The 5 GHz band is faster but has shorter range than 2.4 GHz.

## Slow speed {#slow-speed}

Speeds over Wi-Fi are always lower than the speed delivered to the router.

1. Run a speed test on a laptop connected by an Ethernet cable, close to the router.
2. Open the admin page at `http://192.168.0.1` and check how many devices are connected.
3. Pause large downloads, cloud backups and video streams on other devices.
4. Do one power cycle if the router has been on for many weeks.
5. Compare the wired result with the speed of your SkyLink package. If the wired result is far below it at different times of day, contact support.

Older phones and laptops may not reach full speed over Wi-Fi.

## Factory reset {#factory-reset}

**Warning:** a factory reset erases all your settings: the Wi-Fi name and password, port forwarding and any parental controls. Every device will need to reconnect. Only reset when SkyLink support asks you to, and never when the fibre signal light shows a fault.

1. Leave the router switched on.
2. Find the small recessed RESET hole on the back panel.
3. Press and hold the button with a straightened paperclip for 15 seconds, until the lights flash.
4. Release the button and wait for the router to restart fully.
5. Reconnect using the default network name and password printed on the label.

A shorter press only restarts the router and keeps your settings.

## When to call a technician {#escalation}

A technician visit is needed when the fault is in the line or the hardware, not in settings. Request one if:

1. The fibre signal light still shows a fault after one power cycle.
2. The fibre cable or wall socket is visibly damaged.
3. The power light stays off with a socket you know works.
4. The router keeps restarting by itself.

Have this ready when you contact SkyLink:

1. Your registered mobile number or customer ID.
2. The model code (AL-FX30) and serial number from the label.
3. Which lights are on, off or blinking, and their colours.
4. What you have already tried, and when the problem started.

If there is a reported outage in your area, wait for it to be restored first.
