"""The report of a load test: the targets, what the callers saw, what the platform recorded.

Every figure comes from the runs handed in. The report says what the runs were made on, so
that nobody reads a laptop's numbers as a production capacity.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from typing import Any

from evals.report import Report

from loadtest.outcome import Criterion, Targets, judge, summarise
from loadtest.profile import Profile
from loadtest.run import Result
from loadtest.stack import LoadSample

NOTHING = "—"
ANSWERED = "Contacts answered when the surge was over"
TOLD_OF_THE_END = "Waiting customers told that the outage is over"


def criteria_of(result: Result, targets: Targets) -> list[Criterion]:
    """The plan's three targets; that every contact is answered and everyone who waited on
    the outage hears of its end; and, with the Network API down, the one thing that must not
    be said whatever the load."""
    criteria = judge(result.offered, targets)
    contacts, answered = int(result.facts["contacts"]), int(result.facts["replied"])
    criteria.append(
        Criterion(
            ANSWERED,
            "all",
            f"{answered:,} of {contacts:,}",
            contacts > 0 and answered >= contacts,
        )
    )
    wave = result.restoration
    waiting = int(wave["waiting_on_the_outage"])
    if waiting:
        told = int(wave["told_it_is_over"])
        criteria.append(
            Criterion(
                TOLD_OF_THE_END,
                f"all, within {_minutes(wave['allowed_seconds'])} of its end",
                f"{told:,} of {waiting:,}",
                told >= waiting,
            )
        )
    if result.plan.fault:
        said = int(result.facts["said_clear"])
        criteria.append(
            Criterion(
                "Messages saying the network is clear while its status could not be read",
                "none",
                str(said),
                said == 0,
            )
        )
    return criteria


def build_report(
    name: str,
    results: Sequence[Result],
    *,
    profile: Profile,
    targets: Targets,
    context: Mapping[str, str],
) -> Report:
    judged = [criteria_of(result, targets) for result in results]
    met = bool(results) and all(criterion.met for criteria in judged for criterion in criteria)
    report = Report(
        name=name,
        title="Surge: a tenfold rise in contacts, with and without the Network Monitoring API",
        proves=(
            "That the platform stays available while contacts rise tenfold during a live outage:"
            " every caller gets a complaint reference at once, nobody's first contact is turned"
            " away, and the replies keep coming. That this holds with the Network Monitoring"
            " API down, without anyone being told that the network is fine. And that when the"
            " outage ends, everyone who waited on it hears of it."
        ),
        target=(
            f"Contacts offered at {profile.start_rate:g} a second rising to {profile.peak_rate:g}"
            f" within {_minutes(profile.ramp_seconds)} and held for"
            f" {_minutes(profile.hold_seconds)}, once with every dependency up and once with"
            f" the Network Monitoring API down. In both runs: at least"
            f" {targets.accepted_share:.1%} of the offered contacts accepted, every complaint"
            f" reference within {targets.reference_seconds:.0f} s, no first contact turned away"
            f" by a rate limit, every contact answered.{_told_target(results)}"
        ),
        headline=_headline(results, judged),
        met=met,
        provider=_read_by(results),
        assumptions=_assumptions(context, results),
    )
    summaries = [summarise(result.offered, targets) for result in results]
    _add_tables(report, results, judged, summaries, targets)
    report.numbers = {
        result.plan.name: _numbers(result, criteria, summary)
        for result, criteria, summary in zip(results, judged, summaries, strict=True)
    }
    return report


def _add_tables(
    report: Report,
    results: Sequence[Result],
    judged: Sequence[Sequence[Criterion]],
    summaries: Sequence[Mapping[str, Any]],
    targets: Targets,
) -> None:
    titles = [result.plan.title for result in results]
    report.table("Targets", ["Target", "Wanted", *titles], _target_rows(judged))
    report.table("What the callers saw", ["", *titles], _caller_rows(summaries, targets))
    report.table("What the platform recorded", ["", *titles], _platform_rows(results))
    report.table("The backlog as staff saw it", ["", *titles], _backlog_rows(results))
    report.table("After the surge: the outage ends", ["", *titles], _restoration_rows(results))
    for result in results:
        report.table(
            f"Where the time of a turn goes ({result.plan.title})",
            ["Step", "Times", "Median ms", "95th ms", "Slowest ms"],
            [
                [step["step"], f"{step['calls']:,}", step["p50_ms"], step["p95_ms"], step["max_ms"]]
                for step in result.facts["turn_steps"]
            ],
        )


def _numbers(
    result: Result, criteria: Sequence[Criterion], summary: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "fault": result.plan.fault,
        "started_at": result.started_at.isoformat(),
        "ended_at": result.ended_at.isoformat(),
        "outages": [outage.outage_id for outage in result.outages],
        "nodes_out": sum(len(outage.nodes) for outage in result.outages),
        "targets": [criterion.__dict__ for criterion in criteria],
        "callers": dict(summary),
        "platform": dict(result.facts),
        "backlog": _backlog(result),
        "restoration": dict(result.restoration),
    }


def _told_target(results: Sequence[Result]) -> str:
    allowed = next((result.restoration["allowed_seconds"] for result in results), None)
    if allowed is None:
        return ""
    return (
        " When the outage ends, every customer who waited on it is told within"
        f" {_minutes(allowed)}."
    )


def _headline(results: Sequence[Result], judged: Sequence[Sequence[Criterion]]) -> str:
    parts = []
    for result, criteria in zip(results, judged, strict=True):
        missed = [criterion.name for criterion in criteria if not criterion.met]
        verdict = "every target met" if not missed else "NOT met: " + "; ".join(missed)
        accepted, reference = criteria[0], criteria[1]
        parts.append(
            f"{result.plan.title}: {accepted.measured} accepted, slowest reference"
            f" {reference.measured}, {verdict}."
        )
    return " ".join(parts)


def _target_rows(judged: Sequence[Sequence[Criterion]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    names = list(dict.fromkeys(criterion.name for criteria in judged for criterion in criteria))
    for name in names:
        cells, wanted = [], NOTHING
        for criteria in judged:
            found = next((criterion for criterion in criteria if criterion.name == name), None)
            if found is None:
                cells.append("does not apply")
                continue
            wanted = found.target
            cells.append(f"{found.measured} ({'met' if found.met else 'NOT met'})")
        rows.append([name, wanted, *cells])
    return rows


def _caller_rows(summaries: Sequence[Mapping[str, Any]], targets: Targets) -> list[list[Any]]:
    def row(label: str, cell: Any) -> list[Any]:
        return [label, *(cell(summary) for summary in summaries)]

    late = f"Left without a reference after {targets.reference_seconds:.0f} s"
    return [
        row("Contacts offered", lambda s: f"{s['offered']:,}"),
        row("of them on the nodes of the outage", lambda s: f"{s['affected']:,}"),
        row("by the web / through a contact centre", lambda s: _split(s["by_route"])),
        row("Accepted", lambda s: f"{s['accepted']:,}"),
        row(late, lambda s: f"{s['without_reference_in_time']:,}"),
        row("Turned away by a rate limit", lambda s: f"{s['rate_limited']:,}"),
        row(
            "Reference in hand, from when the contact was due: median / 95th / 99th / slowest",
            lambda s: _spread(s["reference_seconds"], "s"),
        ),
        row("Sent more than once", lambda s: f"{s['sent_more_than_once']:,}"),
        row("Told that the reply will take longer", lambda s: f"{s['told_delayed']:,}"),
        row(
            "Web callers who stayed for the reply / saw it before leaving",
            lambda s: f"{s['stayed_for_the_reply']:,} / {s['saw_a_reply']:,}",
        ),
        row(
            "Reply seen on the page, from when the contact was due: median / 95th / 99th / slowest",
            lambda s: _spread(s["reply_seconds"], "s"),
        ),
        row("Not accepted because", lambda s: _counts(s["not_accepted_because"])),
    ]


def _platform_rows(results: Sequence[Result]) -> list[list[Any]]:
    def row(label: str, cell: Any) -> list[Any]:
        return [label, *(cell(result.facts) for result in results)]

    return [
        row("Contacts received", lambda f: f"{f['contacts']:,}"),
        row(
            "Answered / not answered when the run was read",
            lambda f: f"{f['replied']:,} / {f['contacts'] - f['replied']:,}",
        ),
        row(
            "First reply after receipt, all contacts: median / 95th / 99th / slowest",
            lambda f: _spread(f["first_reply_seconds"], "s"),
        ),
        row(
            "Reference issued, inside the API: median / 99th / slowest",
            lambda f: _spread(f["reference_ms_at_the_server"], "ms"),
        ),
        row("The first reply was", lambda f: _counts(f["first_reply"])),
        row("Contacts stood, when read, as", lambda f: _counts(f["by_status"])),
        row("Handed to a person for", lambda f: _counts(f["handoffs"])),
        row("Messages were read by", lambda f: _counts(f["read_by"])),
        row("Messages saying the network is clear", lambda f: f"{f['said_clear']:,}"),
        row("Contacts whose runs were given up on", lambda f: f"{f['dead_lettered']:,}"),
        row("Writes for these contacts", lambda f: _counts(f["writes"])),
        row("Notices about these outages to customers", lambda f: _counts(f["notifications"])),
        row("Items raised for operations staff", lambda f: _counts(f["ops_items"])),
    ]


def _backlog(result: Result) -> dict[str, Any]:
    samples: Sequence[LoadSample] = result.samples
    gaps = [later.at - earlier.at for earlier, later in itertools.pairwise(samples)]
    behind = sum(gap for gap, sample in zip(gaps, samples, strict=False) if sample.behind)
    return {
        "readings": len(samples),
        "readings_missed": result.missed_readings,
        "generator_lag_seconds": result.generator_lag,
        "most_waiting_for_a_turn": max((sample.waiting for sample in samples), default=0),
        "longest_wait_seconds": max((sample.oldest_seconds for sample in samples), default=0.0),
        "seconds_behind": round(behind),
        "drained_after_seconds": result.drained_after,
    }


def _backlog_rows(results: Sequence[Result]) -> list[list[Any]]:
    backlogs = [_backlog(result) for result in results]

    def row(label: str, cell: Any) -> list[Any]:
        return [label, *(cell(backlog) for backlog in backlogs)]

    return [
        row("Readings taken / missed", lambda b: f"{b['readings']:,} / {b['readings_missed']:,}"),
        row(
            "Latest the generator offered a contact after it was due (inside every time above)",
            lambda b: f"{b['generator_lag_seconds']:g} s",
        ),
        row(
            "Most contacts waiting for a turn at once",
            lambda b: f"{b['most_waiting_for_a_turn']:,}",
        ),
        row("Longest wait for a turn seen", lambda b: f"{b['longest_wait_seconds']:.0f} s"),
        row("Time spent behind (surge mode on)", lambda b: f"{b['seconds_behind']:,} s"),
        row(
            "Keeping up again after the last caller left",
            lambda b: (
                "not within the time allowed"
                if b["drained_after_seconds"] is None
                else f"after {b['drained_after_seconds']:.0f} s"
            ),
        ),
    ]


def _restoration_rows(results: Sequence[Result]) -> list[list[Any]]:
    def row(label: str, cell: Any) -> list[Any]:
        return [label, *(cell(result.restoration) for result in results)]

    return [
        row(
            "Contacts waiting to hear that the outage is over",
            lambda r: f"{r['waiting_on_the_outage']:,}",
        ),
        row("Told that it is over", lambda r: f"{r['told_it_is_over']:,}"),
        row("All of them told after", _all_told_after),
        row("Those not told stood at", lambda r: _counts(r["not_told_stand_as"])),
        row(
            "Told meanwhile that the status cannot be verified",
            lambda r: f"{r['told_unverified_meanwhile']:,}",
        ),
        row(
            "Their lines tested / found failing",
            lambda r: f"{r['lines_tested']:,} / {r['lines_failing']:,}",
        ),
        row(
            "Tickets raised for a suspected fault on the restored nodes",
            lambda r: f"{r['suspected_tickets']:,}",
        ),
        row(
            "Most contacts waiting for a turn meanwhile",
            lambda r: f"{r['most_waiting_for_a_turn']:,}",
        ),
        row("Longest wait for a turn meanwhile", lambda r: f"{r['longest_wait_seconds']:.0f} s"),
    ]


def _all_told_after(wave: Mapping[str, Any]) -> str:
    if not wave["waiting_on_the_outage"]:
        return "nobody waited"
    if wave["seconds"] is None:
        return "not within the time allowed"
    return f"{wave['seconds']:.0f} s"


RULES_ONLY = "rules-v1"


def _read_by(results: Sequence[Result]) -> str:
    """Who read the customers' messages, as the platform recorded it for every message."""
    readers: dict[str, int] = {}
    for result in results:
        for reader, count in result.facts["read_by"].items():
            readers[reader] = readers.get(reader, 0) + count
    if set(readers) <= {RULES_ONLY}:
        return "offline (keyword rules stand in for the language model; none was called)"
    said = " · ".join(f"{reader} {count:,}" for reader, count in readers.items())
    stand_ins_only = all("simulated" in reader or RULES_ONLY in reader for reader in readers)
    if not stand_ins_only:
        return f"as recorded for each message read: {said}"
    return (
        f"as recorded for each message read: {said}. No model was called: `simulated` is the"
        " rules' answer after the time a model call takes"
    )


def _assumptions(context: Mapping[str, str], results: Sequence[Result]) -> list[str]:
    any_contacts = any(result.facts["contacts"] for result in results)
    rules_only = all(set(result.facts["read_by"]) <= {RULES_ONLY} for result in results)
    notes = [
        f"Run on {context.get('machine', 'one machine')}, which also ran the load generator, the"
        " simulator and PostgreSQL: the figures say that the design holds at this rate, not what"
        " a production deployment can carry.",
        f"The stack was {context.get('stack', 'the local processes of `make dev`')}: one process"
        f" of each kind, the API with a pool of {context.get('api_pool', '?')} database"
        f" connections, the orchestrator running {context.get('orchestrator_concurrency', '?')}"
        " contacts at a time.",
        f"Data: the synthetic pack of {context.get('customers', '?')} customers. The OSS/BSS"
        " gateway is the simulator, which answers in a few milliseconds; real systems will be"
        " slower, and the time of a turn is mostly theirs.",
        "Contacts were offered on a schedule fixed in advance (arrivals at random at the"
        f" profile's rate, seed {context.get('seed', '?')}), each by a different customer. Every"
        " time is measured from the instant the contact was due, so the generator's own lag is"
        " inside it.",
        "About seven callers in ten were customers on the failed nodes and the rest were"
        " elsewhere with an ordinary mix of problems. About seven in ten came by the web with"
        " their own sign-in and stayed on the page for the reply, asking as the page does; the"
        " others were relayed by a contact centre's integration, which states the caller's line."
        " The first table has the counts.",
        "A request that failed was sent again with the same request id until two minutes after"
        " the contact was due. A contact whose reference was not in the caller's hands by then"
        " counts as not accepted, also when the API issued one later.",
        f"With the Network Monitoring API down ({context.get('fault', 'down')} in the simulator)"
        " nothing else was made to fail. That the event feed went on working is seen in the"
        " first replies: an outage can be told to a customer only on proof, and with the status"
        " unreadable the feed is the only proof there is.",
        "Not covered: several replicas of a process, a real language model, real enterprise"
        " systems, a network between the parts.",
    ]
    if rules_only and any_contacts:
        notes.insert(
            3,
            "No language model took part, so surge mode had no model to spare: these runs show"
            " what the platform carries when a turn costs what the rules cost. What surge mode"
            " does when a model's seconds are on every turn is shown by a run with the simulated"
            " model (`SIMULATED_MODEL_SECONDS=1.5 make dev`), reported separately.",
        )
    return notes


def _minutes(seconds: float) -> str:
    minutes = seconds / 60
    return f"{minutes:g} minute{'' if minutes == 1 else 's'}"


def _spread(values: Mapping[str, float | None], unit: str) -> str:
    keys = [key for key in ("p50", "p95", "p99", "max") if key in values]
    if all(values[key] is None for key in keys):
        return NOTHING
    return (
        " / ".join("?" if values[key] is None else f"{values[key]:g}" for key in keys) + f" {unit}"
    )


def _counts(counts: Mapping[str, int]) -> str:
    return " · ".join(f"{name} {count:,}" for name, count in counts.items()) or NOTHING


def _split(by_route: Mapping[str, int]) -> str:
    return f"{by_route.get('web', 0):,} / {by_route.get('channel', 0):,}"
