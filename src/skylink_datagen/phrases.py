"""Phrase banks for synthetic care transcripts.

Outage and device contacts deliberately draw openings from the SAME connectivity banks,
so text alone cannot separate them. Billing has its own vocabulary.
Connectivity phrases must never contain billing words (bill, charge, plan, fee, paid, ...).
"""

from __future__ import annotations

GREETINGS: tuple[str, ...] = (
    "hi", "hello", "hi there", "hello sir", "good morning", "good evening", "hey", "namaste",
    "hello team", "hi skylink", "excuse me", "", "", "",
)  # fmt: skip

BROADBAND_SYMPTOMS: tuple[str, ...] = (
    "my internet is not working", "internet not working since morning", "net nahi chal raha",
    "wifi is connected but there is no internet", "there is a red light on the router",
    "the router is showing a red light", "no internet at home", "my broadband is down",
    "internet keeps dropping every few minutes", "the internet is very slow today",
    "wifi is not showing up on my phone", "can't connect to anything at home",
    "my fibre connection is dead", "internet band ho gaya hai", "router pe red light aa rahi hai",
    "the LOS light is blinking red", "no internet since last night",
    "wifi chal nahi raha", "internet is not working on any device",
    "pages are not loading at all", "my laptop says no internet access",
    "the internet light on the router is off", "speed is very slow, videos keep buffering",
    "broadband not working", "the router lights are blinking orange",
    "connection keeps going on and off", "net bahut slow hai",
)  # fmt: skip

MOBILE_SYMPTOMS: tuple[str, ...] = (
    "there is no signal on my phone", "mobile data is not working", "my calls keep dropping",
    "network nahi aa raha", "my phone shows no service", "i can't make any calls",
    "4G is not working", "internet not working on my phone", "phone pe signal nahi hai",
    "calls are not connecting", "my mobile internet is very slow", "only SOS showing on my phone",
    "data is on but nothing loads",
)  # fmt: skip

CONNECTIVITY_TAILS: tuple[str, ...] = (
    "", "", "i already restarted it twice", "please check", "i work from home, it's urgent",
    "kids have online classes", "it was fine yesterday", "it's been 2 hours now",
    "please fix asap", "kya problem hai?", "this is the second time this week",
    "is something wrong in my area?", "what should i do?", "jaldi theek karo please",
    "my neighbour also has some problem i think", "i have an important meeting",
    "nothing has changed at my end",
)  # fmt: skip

BILLING_SYMPTOMS: tuple[str, ...] = (
    "i was charged twice this month", "my bill is higher than usual",
    "why is there an extra charge on my bill", "i need a refund", "bill mein extra charge hai",
    "payment got deducted but it is not showing", "i want to change my plan",
    "there is a late fee on my bill but i paid on time", "my auto debit payment failed",
    "the bill amount is wrong", "i was charged for a plan i never took",
    "i want an invoice for last month", "my recharge is not reflecting",
    "paisa kat gaya but recharge nahi hua", "why did my monthly fee go up",
    "i got a bill after i cancelled", "the discount is missing on my bill",
    "i paid the bill but still got a reminder", "need a copy of my bill for office",
    "can you explain the charges on my bill",
)  # fmt: skip

BILLING_TAILS: tuple[str, ...] = (
    "", "", "please check", "this is not fair", "i need this sorted today",
    "this happened last month also", "can you help?", "kya ho raha hai?",
    "i have the receipt", "please reverse it",
)  # fmt: skip

AGENT_OPENERS: tuple[str, ...] = (
    "Thank you for contacting SkyLink{name}. I'm sorry to hear that.",
    "Hello{name}, thanks for reaching out. Let me help you with that.",
    "Welcome to SkyLink support{name}. I understand the concern.",
    "Thanks for your patience{name}. I'll look into this for you.",
)  # fmt: skip

Probe = tuple[str, tuple[str, ...]]
BROADBAND_PROBES: tuple[Probe, ...] = (
    ("Could you tell me which lights you can see on the router right now?",
     ("the power light is on and one light is red", "lights are blinking, not sure which one",
      "red light hai ek", "everything looks normal but no net")),
    ("Since when have you been facing this, and are all devices affected?",
     ("since morning, all devices", "since about an hour, phone and laptop both",
      "kal raat se, sab devices pe")),
    ("Is the router powered on, and are any lights red or blinking?",
     ("yes it's on, one light is red", "it's on, an orange light is blinking",
      "on hai, lights normal lag rahi hain")),
)  # fmt: skip
MOBILE_PROBES: tuple[Probe, ...] = (
    ("Are you seeing any signal bars on the phone?",
     ("no bars at all", "it comes and goes", "ek bhi bar nahi hai")),
    ("Is this happening everywhere or only at home?",
     ("only at home i think", "everywhere i go nearby", "pata nahi, at home for sure")),
)  # fmt: skip

REBOOT_BROADBAND: tuple[str, ...] = (
    "Please switch off the router, wait 30 seconds and switch it back on.",
    "Let's restart the router: power it off at the wall, wait a minute, then power it on.",
    "Could you reboot the router once and wait for the lights to settle?",
)  # fmt: skip
REBOOT_MOBILE: tuple[str, ...] = (
    "Please restart your phone once and check again.",
    "Could you switch flight mode on and off, or restart the phone?",
)  # fmt: skip
STILL_BROKEN: tuple[str, ...] = (
    "done, still not working", "ok restarted it, same problem", "still the same yaar",
    "i did that already, nothing changed", "abhi bhi nahi chal raha",
)  # fmt: skip

CLOSERS: dict[str, tuple[str, ...]] = {
    "reboot_script": (
        "Please give it some time to settle. If it still doesn't work, call us back.",
        "It may take a little while to come back. Please reach out again if it persists.",
    ),
    "technician_visit": (
        "I have booked a technician visit for tomorrow between 10 am and 2 pm.",
        "I've raised a request; a technician will visit you within 24 to 48 hours.",
    ),
    "escalated": (
        "I'm escalating this to our technical team. They will call you back within 24 hours.",
        "I have raised this with the specialist team; you'll get an update by SMS.",
    ),
    "resolved_on_call": (
        "Great, I'm glad it's working now.",
        "Perfect, good to hear it's back. Anything else I can help with?",
    ),
}
CUSTOMER_ACKS: dict[str, tuple[str, ...]] = {
    "reboot_script": ("ok", "fine, i'll wait", "ok thanks"),
    "technician_visit": ("ok, please make sure he comes", "fine", "theek hai"),
    "escalated": ("please do it fast", "ok", "hope they call soon"),
    "resolved_on_call": ("oh wait, it's working now", "yes it's back now, thanks"),
    "billing_adjustment": ("ok thanks", "fine, thank you"),
}

BILLING_PROBES: tuple[str, ...] = (
    "Let me pull up your account. Could you tell me which bill you mean?",
    "I can check that for you. Which month is this about?",
)  # fmt: skip
BILLING_DETAILS: tuple[str, ...] = (
    "the latest one", "this month's", "last month, it was much lower before",
    "the one i got yesterday", "pichle mahine wala",
)  # fmt: skip
BILLING_EXPLAIN: tuple[str, ...] = (
    "I can see a pro-rata charge from the change made mid-cycle.",
    "There is a late payment fee because the payment reached us after the due date.",
    "It looks like the payment was received but not yet adjusted against the account.",
)  # fmt: skip
BILLING_CLOSERS: dict[str, tuple[str, ...]] = {
    "billing_adjustment": (
        "I have applied an adjustment; it will reflect on your next bill.",
        "I've reversed that charge for you. You'll see it in the next cycle.",
    ),
    "resolved_on_call": ("Happy to help. Anything else today?",),
    "escalated": ("I'm escalating this to the billing team; they will respond within 48 hours.",),
}
BILLING_UNDERSTOOD: tuple[str, ...] = ("ok that makes sense, thanks", "achha, ok got it")

PII_ASK = "Could you please confirm your registered details so I can check the account?"
PII_TEMPLATES: dict[str, tuple[str, ...]] = {
    "phone": ("my number is {value}", "registered mobile is {value}", "it's {value}"),
    "customer_id": ("customer id {value}", "my customer id is {value}"),
    "email": ("my email is {value}", "registered email {value}"),
}
SMALL_TALK: tuple[tuple[str, str], ...] = (
    ("Agent: Is there anything else I can help you with?", "Customer: no, that's all"),
    ("Agent: Thank you for your patience today.", "Customer: ok bye"),
)
