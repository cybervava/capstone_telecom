# Load test: the surge of the brief

The brief asks that the platform stay available through a tenfold rise in contacts. This
offers that rise to the running stack twice, once with every dependency up and once with the
Network Monitoring API down (the curveball under load), and writes a report.

```bash
make dev              # the stack, freshly seeded from data/generated
make loadtest-quick   # the shape of the surge in two minutes a run; report in var/loadtest/
make loadtest         # the profile of the brief, both runs: about two hours
make stop
```

`make loadtest` writes `loadtest/reports/surge-brief.md` and `.json`. The command exits
non-zero when a target was missed. Tests, evaluations and the stack share one database: stop the
stack before running either of the others, and expect them to clear what a load run left.

In `loadtest/reports/` today: `surge-brief-before-fix.md` is the half-hour profile as it ran on
16 November, before the rule for failing lines was changed (the wave after its surge is what led
to the change). `surge-quick.md` is the two-minute profile on the code as it is, and
`surge-simulated-model.md` is described below. The half-hour profile needs the machine awake for
two hours: a laptop that goes to sleep stops the run where it is, and a run that slept must be
repeated, because every age the platform judges by jumps with the clock.

It runs only against a stack on this machine: it resets the simulator, makes a dependency fail
and signs in with the local staff key.

### Surge mode under load

With the default provider no language model takes part, so there is no model for surge mode to
spare, and at ten contacts a second this machine keeps up. To see what surge mode does, give
the orchestrator a stand-in for a model: the rules' answer after a model's seconds, no model
called. Its cost is on every turn until the orchestrator is behind and sheds it.

```bash
SIMULATED_MODEL_SECONDS=1.5 make dev
uv run --env-file .env python -m loadtest --profile quick --runs baseline \
    --name surge-simulated-model --out loadtest/reports
make stop
```

## What is offered

| | |
|---|---|
| Profile | 1 contact a second rising to 10 within 15 minutes, held for 15 minutes: 13,950 contacts a run, arriving at random at that rate (callers come in bunches) |
| Outage | Enough aggregation nodes fail for half of their customers to make up seven callers in ten; the NOC declares the outages 20 seconds before the first call |
| Callers | Each a different synthetic customer making a first contact. Seven in ten sign in on the web and stay on the page for the reply, asking as the page does. Three in ten are relayed by a contact centre's integration, which states the caller's line |
| Second run | Other nodes, other customers, and the Network Monitoring API down from before the first call to after the last. The event feed stays healthy |
| After each run | The dependency is brought back, the NOC resolves the outages, and the time for the news to reach everyone who waits is measured. Every waiting line is tested before its customer is told, so this wave runs at the pace of the line test |

The schedule is fixed before the run and does not slow down when the platform does: a customer
does not wait to call because the one before is on hold. Every time is measured from the
instant a contact was due, so time lost queueing in the generator counts against the platform.
A request that fails is sent again with the same request id until two minutes after the contact
was due; a 429 is not sent again, because a first contact turned away is the failure looked for.

## Targets

At least 99.5% of the offered contacts accepted, every complaint reference in the caller's hands
within 120 seconds, no first contact turned away by a rate limit; in both runs. The first three
are the plan's. Two more keep a run honest: every contact has an answer when the surge is over,
and when the outage ends every customer who waited on it is told within the 30 minutes a run
allows. With the Network Monitoring API down there is a sixth: no message says that the network
is clear.

The regulator's rule read strictly allows nobody to be left without a reference after two
minutes. That count is in the report beside the share.

## Reading the report

- **What the callers saw** is measured by the generator, as a client. **What the platform
  recorded** is read from its database for the contacts of the run: it covers every contact,
  including those whose caller rang off with the reference.
- **The backlog as staff saw it** is what `/v1/ops/load` gave every five seconds: the figure on
  the staff desk. "Behind" is surge mode: callers are told that the reply will take longer and
  messages are read by the rules. The generator's own lag is given there too; it is inside
  every time the callers measured.
- **After the surge** accounts for everyone who waited on the outage: told that it is over, or
  standing where the platform left them when the time was up. It also gives how many were told
  meanwhile that the status cannot be verified, how many of their lines failed the test (faults
  of their own, which the data pack has at a rate of about one in seventy) and whether the
  platform raised a ticket for a suspected fault on a node that had just been restored. In the
  simulator nothing is wrong with those nodes, so such a ticket is the platform doubting a
  healthy node: rare by design, never impossible (see `line_own_fault_rate` in the settings).
- **Where the time of a turn goes** lists the steps the audit trail timed. The simulator answers
  in milliseconds; real enterprise systems will not.

The figures say that the design holds at this rate on one machine, which also ran the generator,
the simulator and PostgreSQL. They are not a capacity figure for a deployment.

## Parts

| File | |
|---|---|
| `profile.py` | The schedule of arrivals |
| `population.py` | Which nodes fail and who calls, from the data pack |
| `callers.py` | What a caller does: sign in, open the contact, send again on failure, wait for the reply |
| `stack.py` | Staff sign-in, the simulator's admin API as the NOC and as the failing dependency, the watch on the backlog |
| `run.py` | One run, from the declared outage to the facts |
| `facts.py` | What the platform recorded |
| `outcome.py`, `report.py` | The verdict and the report |

The schedule, the cast, the callers and the verdict have unit tests in `tests/unit/loadtest/`.
