"""Callers that ask about one node at the same moment share one read of its live status."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from skylink_care.adapters.models import LineTestDto, NodeStatusDto
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result
from skylink_care.adapters.shared import SharedNodeStatus

OBSERVED = datetime(2026, 11, 15, 4, 31, 7, tzinfo=UTC)


def answer(node_id: str = "N1") -> Ok[NodeStatusDto]:
    dto = NodeStatusDto(
        node_id=node_id,
        status="OPERATIONAL",
        outage=None,
        active_alarms=(),
        observed_at=OBSERVED,
        feed_seq=1,
    )
    return Ok(dto, status_code=200, request_id=f"req-{node_id}")


class Reader:
    """Stands for the gateway's status API: counts the calls and answers when let go."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.go = asyncio.Event()
        self.go.set()
        self.outcomes: list[Result[NodeStatusDto] | Exception] = []
        self.line_tests = 0

    async def node_status(self, node_id: str, *, correlation_id: str) -> Result[NodeStatusDto]:
        self.calls.append((node_id, correlation_id))
        await self.go.wait()
        outcome = self.outcomes.pop(0) if self.outcomes else answer(node_id)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def line_test(self, service_id: str, *, correlation_id: str) -> Result[LineTestDto]:
        self.line_tests += 1
        return Err(ErrorKind.UNAVAILABLE, "not part of this test")


@pytest.fixture
def reader() -> Reader:
    return Reader()


async def ask_together(
    status: SharedNodeStatus, reader: Reader, callers: int
) -> list[Result[NodeStatusDto] | BaseException]:
    """This many callers ask about node N1 while the read is still on its way."""
    reader.go.clear()
    asking = [
        asyncio.create_task(status.node_status("N1", correlation_id=f"corr-{n}"))
        for n in range(callers)
    ]
    await asyncio.sleep(0)
    reader.go.set()
    return await asyncio.gather(*asking, return_exceptions=True)


async def test_everyone_asking_about_one_node_at_once_shares_one_read(reader: Reader) -> None:
    status = SharedNodeStatus(reader)

    results = await ask_together(status, reader, callers=50)

    assert len(reader.calls) == 1
    assert isinstance(results[0], Ok)
    assert all(result is results[0] for result in results)
    # The answer is the gateway's, with the time the gateway observed it.
    assert results[0].value.observed_at == OBSERVED


async def test_each_node_is_read_for_itself(reader: Reader) -> None:
    status = SharedNodeStatus(reader)

    first, second = await asyncio.gather(
        status.node_status("N1", correlation_id="a"), status.node_status("N2", correlation_id="b")
    )

    assert [node for node, _ in reader.calls] == ["N1", "N2"]
    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert (first.value.node_id, second.value.node_id) == ("N1", "N2")


async def test_an_answer_that_has_arrived_is_not_kept_for_the_next_caller(reader: Reader) -> None:
    """Only a read on its way is shared. Whoever asks afterwards gets a read of their own:
    the feed may have reported a change in between."""
    status = SharedNodeStatus(reader)

    first = await status.node_status("N1", correlation_id="a")
    second = await status.node_status("N1", correlation_id="b")

    assert len(reader.calls) == 2
    assert second is not first
    assert status.in_flight == 0


async def test_a_failure_is_shared_by_those_waiting_and_the_next_caller_asks_again(
    reader: Reader,
) -> None:
    status = SharedNodeStatus(reader)
    reader.outcomes = [Err(ErrorKind.TIMEOUT, "slow")]

    failed = await ask_together(status, reader, callers=5)
    next_one = await status.node_status("N1", correlation_id="later")

    assert all(isinstance(result, Err) and result.kind is ErrorKind.TIMEOUT for result in failed)
    assert isinstance(next_one, Ok)
    assert len(reader.calls) == 2


async def test_one_caller_giving_up_does_not_take_the_read_from_the_others(
    reader: Reader,
) -> None:
    status = SharedNodeStatus(reader)
    reader.go.clear()

    first = asyncio.create_task(status.node_status("N1", correlation_id="first"))
    second = asyncio.create_task(status.node_status("N1", correlation_id="second"))
    await asyncio.sleep(0)
    first.cancel()
    reader.go.set()

    assert isinstance(await second, Ok)
    with pytest.raises(asyncio.CancelledError):
        await first
    assert len(reader.calls) == 1


async def test_a_read_everyone_has_given_up_on_leaves_nothing_behind(reader: Reader) -> None:
    status = SharedNodeStatus(reader)
    reader.go.clear()

    only = asyncio.create_task(status.node_status("N1", correlation_id="only"))
    await asyncio.sleep(0)
    only.cancel()
    with pytest.raises(asyncio.CancelledError):
        await only
    reader.go.set()
    await asyncio.sleep(0.01)

    assert status.in_flight == 0
    assert isinstance(await status.node_status("N1", correlation_id="later"), Ok)


async def test_an_unexpected_error_reaches_everyone_waiting_and_is_not_kept(
    reader: Reader,
) -> None:
    status = SharedNodeStatus(reader)
    reader.outcomes = [RuntimeError("boom")]

    outcomes = await ask_together(status, reader, callers=3)

    assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
    assert status.in_flight == 0
    assert isinstance(await status.node_status("N1", correlation_id="later"), Ok)


async def test_a_line_test_is_never_shared(reader: Reader) -> None:
    status = SharedNodeStatus(reader)

    await asyncio.gather(
        status.line_test("S1", correlation_id="a"), status.line_test("S1", correlation_id="b")
    )

    assert reader.line_tests == 2


async def test_a_read_cancelled_before_it_began_leaves_nothing_behind(reader: Reader) -> None:
    """Found by review: such a read never reached its own clean-up, and every later caller for
    that node was handed the cancelled read."""
    status = SharedNodeStatus(reader)

    waiting = asyncio.create_task(status.node_status("N1", correlation_id="first"))
    await asyncio.sleep(0)
    for read in list(status._reads.values()):
        read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await asyncio.sleep(0)

    assert status.in_flight == 0
    assert isinstance(await status.node_status("N1", correlation_id="later"), Ok)
