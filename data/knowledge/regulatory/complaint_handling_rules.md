---
doc_id: rules-complaint-handling
doc_type: complaint_rules
title: Complaint handling rules for outage contacts (synthetic placeholder)
version: "v0-placeholder"
effective_from: 2026-04-01
access_roles: [agent, supervisor]
---

# Complaint handling rules for outage contacts (synthetic placeholder)

> **This is not a regulation and not SkyLink's real procedure.** The capstone brief says regulatory complaint rules are supplied; they have not been received. The two-minute reference rule comes from the capstone brief itself. Every other number is an assumption to be replaced by Regulatory Affairs.

## Complaint reference {#reference}

1. Every contact receives a complaint reference number, whatever it turns out to be about.
2. The reference is given to the customer within 2 minutes of the contact being received. This applies during outages, surges and failures of other systems.
3. The reference is issued by the care platform's own intake ledger, so that it does not depend on any other system being available. It is registered with Incident Management as soon as that system can be reached.
4. A retry of the same contact returns the same reference. A different contact gets a different reference, even from the same customer about the same outage.
5. Holding a reference does not prove identity. Account details are released only after the customer is verified.

## What the customer is told {#customer-messages}

1. The customer is told they are dealing with an automated assistant.
2. When a network outage affecting the customer is confirmed, the customer is told the outage reference, when it began, the restoration estimate and where that estimate came from.
3. When the network status cannot be verified, the customer is told exactly that. They are never told the network is working unless a fresh check of the network confirms it, and then the wording is "no outage has been reported", not "there is no outage".
4. A restoration time is never invented. If there is no estimate, the customer is told there is none yet.
5. A customer who asks for a person is passed to one, with a summary, without having to repeat themselves.

## Outage complaints and the shared incident {#shared-incident}

1. All complaints caused by the same outage on the same network node are attached to one incident for that node. A second incident is not opened for the same node while the first is open.
2. Each complaint keeps its own reference inside the shared incident.
3. A technician visit is not booked for a customer while an outage is confirmed or suspected on their node.

## Resolution and closure {#closure}

1. An outage complaint is resolved when service is restored and that is confirmed, not when the complaint was acknowledged.
2. Target for confirming closure after restoration: 4 hours (placeholder).
3. A repeat complaint within 7 days about the same service is flagged for priority human review.

## Records {#records}

1. For every complaint the following are recorded: when it was received, when the reference was issued, each step taken and by whom or what, the evidence used, and the outcome.
2. Records are kept for 24 months (placeholder), then deleted or anonymised.
3. Records cannot be altered after the fact.
