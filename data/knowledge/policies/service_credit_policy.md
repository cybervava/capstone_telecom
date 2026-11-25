---
doc_id: policy-service-credit
doc_type: credit_policy
title: SkyLink service credit policy (synthetic placeholder)
version: "v0-placeholder"
effective_from: 2026-04-01
access_roles: [customer, agent, supervisor]
---

# SkyLink service credit policy (synthetic placeholder)

> **This is not SkyLink's real policy.** The capstone brief says a service credit policy is supplied; it has not been received. This document and `data/policies/credit_policy.v0.yaml` are a stand-in so the credit engine can be built and tested. Every number here is an assumption. The YAML file is what the engine executes; this document must always say the same thing.

## Scope {#scope}

This policy covers credits for unplanned network outages on broadband and mobile services. It does not cover faults in customer equipment, wiring inside the premises, or slow speeds without an outage.

## Eligibility {#eligibility}

A service earns a credit for an outage when all of the following hold:

1. The service is on a network node affected by the outage.
2. The outage was not planned maintenance notified in advance.
3. The service is active (not suspended).
4. The outage has been resolved, so its duration is known. No final credit is calculated while an outage is still open.
5. The outage lasted at least the minimum duration for the service type: 240 minutes for broadband, 1,440 minutes for mobile.
6. No credit has already been given to this service for this outage.

When several reasons apply, the first one in this list is the reason given to the customer.

## Calculation {#calculation}

The steps are applied in this order:

1. **Duration.** Outage minutes run from the time service was lost to the time it was restored, as recorded by network monitoring. The duration is rounded **up** to whole hours.
2. **Base amount.** Monthly fee ÷ 720 × billable hours.
3. **Duration tier.** The base amount is multiplied according to the total outage duration:

   | Service | From (minutes) | Multiplier |
   |---|---|---|
   | Broadband | 240 | 1.0 |
   | Broadband | 1,440 | 1.5 |
   | Broadband | 4,320 | 2.0 |
   | Mobile | 1,440 | 1.0 |
   | Mobile | 4,320 | 1.5 |

4. **Minimum.** An eligible outage never earns less than $50.00 (broadband) or $25.00 (mobile).
5. **Billing-cycle cap.** Outage credits in one billing cycle never exceed one monthly fee. Credits already given in the cycle are subtracted from the cap. If nothing remains, no credit is given.
6. **Rounding.** The result is rounded once, at the end, to the cent ($0.01), half up.

## Approval {#approval}

A credit above $500.00 needs supervisor approval before it is applied. A credit of exactly $500.00 does not. Until SkyLink confirms that smaller credits may be applied automatically, every credit is reviewed by an agent first.

An approval covers one exact credit: this service, this outage, this amount, this policy version. If any of those change, a new approval is needed.

## Worked examples {#examples}

| Plan fee ($) | Outage | Working | Credit ($) | Supervisor |
|---|---|---|---|---|
| 999.00 | 4 h | 999 ÷ 720 × 4 = 5.55, raised to the minimum | 50.00 | No |
| 3,999.00 | 10 h | 3,999 ÷ 720 × 10 | 55.54 | No |
| 9,999.00 | 26 h | 9,999 ÷ 720 × 26 × 1.5 | 541.61 | Yes |
| 6,000.00 | 40 h | 6,000 ÷ 720 × 40 × 1.5 | 500.00 | No |
| 999.00 | 100 h, $800 already credited this cycle | 277.50, capped at 999 − 800 | 199.00 | No |

## Changes {#changes}

A change to any number is made by adding a new policy version, never by editing this one. Every credit records the version it was calculated under.
