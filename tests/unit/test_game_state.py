"""Deterministic sports game-state normalization, identity, budgets, and fallback tests."""

import asyncio
import base64
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from market_agent.providers.game_state import (
    BaseballSituation,
    FootballSituation,
    GameState,
    IdentityMismatchError,
    ProviderUnavailableError,
    SportsStateClient,
    SportsStateError,
    StateValidationError,
    _status,
    observations_conflict,
)

pytestmark = pytest.mark.unit


def team(team_id, display, abbreviation):
    city, nickname = display.rsplit(" ", 1)
    return {
        "id": str(team_id),
        "displayName": display,
        "shortDisplayName": nickname,
        "name": nickname,
        "abbreviation": abbreviation,
        "location": city,
    }


def status(name="STATUS_IN_PROGRESS", state="in", description="In Progress", **values):
    return {
        "displayClock": values.pop("clock", "7:21"),
        "period": values.pop("period", 3),
        "type": {
            "name": name,
            "state": state,
            "completed": values.pop("completed", False),
            "description": description,
            "detail": values.pop("detail", "7:21 - 3rd Quarter"),
            **values,
        },
    }


def competitors(
    home="Atlanta Falcons",
    away="Carolina Panthers",
    home_id="1",
    away_id="29",
    home_score="10",
    away_score="17",
):
    return [
        {
            "homeAway": "home",
            "score": home_score,
            "team": team(home_id, home, "ATL" if home_id == "1" else "NYY"),
        },
        {
            "homeAway": "away",
            "score": away_score,
            "team": team(away_id, away, "CAR" if away_id == "29" else "SD"),
        },
    ]


def scoreboard_event(
    *,
    event_id="401000001",
    start="2026-09-20T17:00:00Z",
    game_status=None,
    teams=None,
    situation=None,
):
    game_status = game_status or status()
    return {
        "id": event_id,
        "date": start,
        "status": game_status,
        "competitions": [
            {
                "id": event_id,
                "date": start,
                "status": game_status,
                "competitors": teams or competitors(),
                "situation": situation
                or {
                    "down": 2,
                    "distance": 7,
                    "possession": "29",
                    "possessionText": "ATL 21",
                    "isRedZone": False,
                    "homeTimeouts": 3,
                    "awayTimeouts": 2,
                    "downDistanceText": "2nd & 7 at ATL 21",
                    "lastPlay": {"text": "Pass complete for three yards."},
                },
            }
        ],
    }


def football_summary(event=None):
    event = deepcopy(event or scoreboard_event())
    competition = event["competitions"][0]
    return {
        "header": {"id": event["id"], "competitions": [competition]},
        "drives": {
            "current": {
                "team": {"id": "29"},
                "plays": [
                    {
                        "text": "Pass complete for three yards.",
                        "end": {
                            "down": 2,
                            "distance": 7,
                            "possessionText": "ATL 21",
                            "team": {"id": "29"},
                        },
                    }
                ],
            }
        },
    }


def mlb_event(*, event_id="401000002", start="2026-09-20T20:10:00Z"):
    game_status = status(detail="Bottom 6th", period=6, clock="0:00")
    return scoreboard_event(
        event_id=event_id,
        start=start,
        game_status=game_status,
        teams=competitors(
            home="New York Yankees",
            away="San Diego Padres",
            home_id="10",
            away_id="25",
            home_score="3",
            away_score="2",
        ),
        situation={"lastPlay": {"text": "Pitch 5: foul ball."}},
    )


def mlb_summary(event=None):
    event = deepcopy(event or mlb_event())
    return {
        "header": {"id": event["id"], "competitions": event["competitions"]},
        "situation": {
            "balls": 1,
            "strikes": 2,
            "outs": 1,
            "onFirst": True,
            "onSecond": False,
            "onThird": False,
            "batter": {"athlete": {"displayName": "Aaron Judge"}},
            "pitcher": {"athlete": {"displayName": "Yu Darvish"}},
            "lastPlay": {"text": "Pitch 5: foul ball."},
        },
    }


def client_with(handler, *, now=None, max_response_bytes=5 * 1024 * 1024):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        SportsStateClient(
            http_client=http,
            max_attempts=2,
            retry_backoff_seconds=0,
            now=now,
            max_response_bytes=max_response_bytes,
        ),
        http,
    )


async def discover(client, query="Falcons", league="nfl"):
    return await client.find_games(
        query,
        league=league,
        timezone="America/Los_Angeles",
        local_date=date(2026, 9, 20),
    )


@pytest.mark.parametrize(
    "provider_status,expected",
    [
        (status("STATUS_SCHEDULED", "pre", "Scheduled", period=0), "scheduled"),
        (status("STATUS_PRE_GAME", "pre", "Pre-Game", period=0), "pregame"),
        (status(), "live"),
        (status(detail="Halftime"), "halftime"),
        (status("STATUS_DELAYED", "in", "Delayed"), "delayed"),
        (status("STATUS_SUSPENDED", "in", "Suspended"), "suspended"),
        (status("STATUS_POSTPONED", "post", "Postponed"), "postponed"),
        (status("STATUS_CANCELED", "post", "Canceled"), "cancelled"),
        (status("STATUS_FINAL", "post", "Final", completed=True), "final"),
        (status("STATUS_MYSTERY", "mystery", "Mystery"), "unknown"),
    ],
)
def test_lifecycle_uses_explicit_status(provider_status, expected):
    assert _status(provider_status)[0] == expected


def test_baseball_phase_rejects_contradictory_state():
    with pytest.raises(ValueError, match="inning and half"):
        BaseballSituation(phase="active", half="top")
    with pytest.raises(ValueError, match="counts exceed"):
        BaseballSituation(phase="active", inning=1, half="top", balls=4)
    with pytest.raises(ValueError, match="must not contain"):
        BaseballSituation(phase="not_started", inning=1, half="top")


async def test_discovery_normalizes_identity_timezone_state_and_bounds_requests():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"events": [scoreboard_event()]})

    client, http = client_with(handler)
    try:
        result = await discover(client)
    finally:
        await http.aclose()
    assert len(calls) == 1
    assert len(result.games) == 1
    game = result.games[0]
    assert (game.home_team, game.away_team) == (
        "Atlanta Falcons",
        "Carolina Panthers",
    )
    assert game.local_date == date(2026, 9, 20)
    assert game.scheduled_start_local.utcoffset() == timedelta(hours=-7)
    assert game.home_score == 10 and game.away_score == 17
    assert game.lifecycle == "live" and game.period == 3
    assert "lightweight scoreboard snapshot" in result.usage_note
    assert "scoped to the requested timezone" in result.usage_note
    assert result.coverage.scoreboard_requests == 1
    assert result.coverage.events_scanned == 1


async def test_utc_boundary_requests_stop_when_adjacent_page_matches():
    calls = []
    late = scoreboard_event(start="2026-09-21T06:30:00Z")

    def handler(request):
        calls.append(request.url.params["dates"])
        payload = [late] if request.url.params["dates"] == "20260921" else []
        return httpx.Response(200, json={"events": payload})

    client, http = client_with(handler)
    try:
        result = await discover(client)
    finally:
        await http.aclose()
    assert calls == ["20260920", "20260921"]
    assert len(result.games) == 1
    assert result.games[0].local_date == date(2026, 9, 20)
    assert result.coverage.scoreboard_requests == 2


async def test_ambiguity_invalid_timezone_and_limit_fail_before_provider_io():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    client, http = client_with(handler)
    try:
        unclear = await client.find_games(
            "OSU", league="ncaa_football", timezone="UTC", local_date=date(2026, 9, 20)
        )
        assert unclear.clarification and unclear.games == []
        assert 'query="Ohio State Buckeyes"' in unclear.clarification
        assert "keep league, timezone, and local_date unchanged" in unclear.clarification
        assert unclear.suggested_queries == unclear.choices
        with pytest.raises(SportsStateError, match="IANA") as timezone_error:
            await client.find_games("Falcons", league="nfl", timezone="Nowhere/Local")
        assert timezone_error.value.code == "invalid_timezone"
        with pytest.raises(SportsStateError) as limit_error:
            await client.find_games("Falcons", league="nfl", timezone="UTC", limit=11)
        assert limit_error.value.code == "invalid_limit"
    finally:
        await http.aclose()
    assert calls == []


async def test_scheduled_discovery_and_detail_remove_provider_state_placeholders():
    scheduled_status = status(
        "STATUS_SCHEDULED",
        "pre",
        "Scheduled",
        detail="Scheduled",
        period=1,
        clock="0:00",
    )
    event = scoreboard_event(
        event_id="401000002",
        start="2026-09-20T20:10:00Z",
        game_status=scheduled_status,
        teams=competitors(
            home="New York Yankees",
            away="San Diego Padres",
            home_id="10",
            away_id="25",
            home_score="0",
            away_score="0",
        ),
        situation={"lastPlay": {"text": "Provider placeholder."}},
    )
    summary = mlb_summary(event)

    def handler(request):
        payload = summary if request.url.path.endswith("/summary") else {"events": [event]}
        return httpx.Response(200, json=payload)

    client, http = client_with(handler)
    try:
        result = await discover(client, "Yankees", "mlb")
        discovered = result.games[0]
        state = await client.get_game_state(discovered.game_ref)
    finally:
        await http.aclose()

    for snapshot in (discovered, state):
        assert snapshot.lifecycle == "scheduled"
        assert snapshot.home_score is None and snapshot.away_score is None
        assert snapshot.period is None and snapshot.period_label is None
        assert snapshot.clock is None and snapshot.last_play is None
    assert "authoritative normalized sporting-state snapshot" in state.usage_note
    assert isinstance(state.situation, BaseballSituation)
    assert state.situation.model_dump() == BaseballSituation(phase="not_started").model_dump()


async def test_bounded_schedule_query_lists_games_without_team_wordle():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"events": [scoreboard_event()]})

    client, http = client_with(handler)
    try:
        result = await client.find_games(
            "all",
            league="nfl",
            timezone="America/Los_Angeles",
            local_date=date(2026, 9, 20),
        )
    finally:
        await http.aclose()

    assert result.discovery_mode == "schedule"
    assert len(result.games) == 1
    assert result.games[0].home_team == "Atlanta Falcons"
    assert len(calls) == 1


async def test_malformed_sibling_is_discarded_but_valid_game_survives():
    malformed = scoreboard_event(event_id="401000000")
    malformed["competitions"][0]["competitors"][0]["score"] = "minus one"

    def handler(request):
        return httpx.Response(200, json={"events": [malformed, scoreboard_event()]})

    client, http = client_with(handler)
    try:
        result = await discover(client)
    finally:
        await http.aclose()
    assert len(result.games) == 1
    assert result.coverage.discarded_event_count == 1
    assert result.coverage.warnings[0].code == "discarded_provider_event"


@pytest.mark.parametrize("root", [{}, {"events": False}, {"events": [1]}])
async def test_malformed_scoreboard_root_fails_safely(root):
    def handler(request):
        return httpx.Response(200, json=root)

    client, http = client_with(handler)
    try:
        with pytest.raises(StateValidationError):
            await discover(client)
    finally:
        await http.aclose()


async def test_detail_exposes_explicit_football_state_and_cache_identity():
    current = [datetime(2026, 9, 20, 18, 0, tzinfo=UTC)]
    calls = []

    def handler(request):
        calls.append(request.url.path)
        payload = (
            football_summary()
            if request.url.path.endswith("/summary")
            else {"events": [scoreboard_event()]}
        )
        return httpx.Response(200, json=payload, headers={"Cache-Control": "max-age=60"})

    client, http = client_with(handler, now=lambda: current[0])
    try:
        result = await discover(client)
        first = await client.get_game_state(result.games[0].game_ref)
        current[0] += timedelta(seconds=2)
        cached = await client.get_game_state(result.games[0].game_ref)
    finally:
        await http.aclose()
    assert isinstance(first.situation, FootballSituation)
    assert first.situation.possession_team == "Carolina Panthers"
    assert (first.situation.down, first.situation.distance) == (2, 7)
    assert first.situation.field_position == "ATL 21"
    assert first.situation.red_zone is False
    assert first.situation.home_timeouts == 3
    assert first.last_play == "Pass complete for three yards."
    assert cached.cache_hit is True and cached.cache_age_ms == 2000
    assert cached.observation_id == first.observation_id
    assert cached.retrieved_at == first.retrieved_at
    assert calls.count("/apis/site/v2/sports/football/nfl/summary") == 1


@pytest.mark.parametrize(
    "game_status,cache_seconds",
    [
        (status(), 5),
        (status("STATUS_SCHEDULED", "pre", "Scheduled", period=0), 30),
        (status("STATUS_FINAL", "post", "Final", completed=True), 300),
    ],
)
async def test_cache_lifetime_caps_follow_lifecycle(game_status, cache_seconds):
    current = [datetime(2026, 9, 20, 18, 0, tzinfo=UTC)]
    event = scoreboard_event(game_status=game_status)
    summary = football_summary(event)
    detail_calls = 0

    def handler(request):
        nonlocal detail_calls
        if request.url.path.endswith("/summary"):
            detail_calls += 1
            return httpx.Response(200, json=summary, headers={"Cache-Control": "max-age=9999"})
        return httpx.Response(200, json={"events": [event]})

    client, http = client_with(handler, now=lambda: current[0])
    try:
        result = await discover(client)
        ref = result.games[0].game_ref
        await client.get_game_state(ref)
        current[0] += timedelta(seconds=cache_seconds - 1)
        assert (await client.get_game_state(ref)).cache_hit is True
        current[0] += timedelta(seconds=2)
        assert (await client.get_game_state(ref)).cache_hit is False
    finally:
        await http.aclose()
    assert detail_calls == 2


async def test_detail_exposes_mlb_inning_count_outs_bases_and_players():
    def handler(request):
        payload = (
            mlb_summary() if request.url.path.endswith("/summary") else {"events": [mlb_event()]}
        )
        return httpx.Response(200, json=payload)

    client, http = client_with(handler)
    try:
        result = await discover(client, "Yankees", "mlb")
        state = await client.get_game_state(result.games[0].game_ref)
    finally:
        await http.aclose()
    assert isinstance(state.situation, BaseballSituation)
    assert state.situation.phase == "active"
    assert (state.situation.inning, state.situation.half) == (6, "bottom")
    assert (state.situation.balls, state.situation.strikes, state.situation.outs) == (1, 2, 1)
    assert (state.situation.on_first, state.situation.on_second, state.situation.on_third) == (
        True,
        False,
        False,
    )
    assert state.situation.batter == "Aaron Judge"
    assert state.situation.pitcher == "Yu Darvish"


async def test_missing_situation_fields_are_null_not_inferred():
    summary = football_summary()
    summary.pop("drives")
    summary["header"]["competitions"][0].pop("situation", None)

    def handler(request):
        payload = (
            summary if request.url.path.endswith("/summary") else {"events": [scoreboard_event()]}
        )
        return httpx.Response(200, json=payload)

    client, http = client_with(handler)
    try:
        result = await discover(client)
        state = await client.get_game_state(result.games[0].game_ref)
    finally:
        await http.aclose()
    situation = state.situation
    assert isinstance(situation, FootballSituation)
    assert situation.model_dump(exclude={"sport"}) == {
        "possession_team": None,
        "down": None,
        "distance": None,
        "field_position": None,
        "red_zone": None,
        "home_timeouts": None,
        "away_timeouts": None,
        "down_distance_label": None,
    }


async def test_espn_negative_down_transition_sentinel_becomes_warned_null():
    summary = football_summary()
    summary["header"]["competitions"][0].pop("situation", None)
    last = summary["drives"]["current"]["plays"][-1]
    last["end"]["down"] = -1

    def handler(request):
        payload = (
            summary if request.url.path.endswith("/summary") else {"events": [scoreboard_event()]}
        )
        return httpx.Response(200, json=payload)

    client, http = client_with(handler)
    try:
        result = await discover(client)
        state = await client.get_game_state(result.games[0].game_ref)
    finally:
        await http.aclose()
    assert isinstance(state.situation, FootballSituation)
    assert state.situation.down is None
    assert state.warnings[0].code == "provider_transition_sentinel"


async def test_reference_tampering_and_raw_provider_id_are_rejected_before_io():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"events": [scoreboard_event()]})

    client, http = client_with(handler)
    try:
        with pytest.raises(SportsStateError) as raw:
            await client.get_game_state("401000001")
        assert raw.value.code == "invalid_game_ref"
        result = await discover(client)
        call_count = len(calls)
        ref = result.games[0].game_ref
        changed = ref[:-1] + ("A" if ref[-1] != "A" else "B")
        with pytest.raises(SportsStateError) as tampered:
            await client.get_game_state(changed)
        assert tampered.value.code == "invalid_game_ref"
        assert len(calls) == call_count
    finally:
        await http.aclose()


async def test_forged_reference_with_recomputed_checksum_still_validates_catalog_and_source():
    def handler(request):
        return httpx.Response(200, json={"events": [scoreboard_event()]})

    client, http = client_with(handler)
    try:
        result = await discover(client)
        ref = result.games[0].game_ref
        envelope = json.loads(base64.urlsafe_b64decode(ref + "=" * (-len(ref) % 4)))
        envelope["payload"]["source"] = "arbitrary"
        forged = base64.urlsafe_b64encode(json.dumps(envelope).encode()).decode().rstrip("=")
        with pytest.raises(SportsStateError) as error:
            await client.get_game_state(forged)
        assert error.value.code == "invalid_game_ref"
    finally:
        await http.aclose()


async def test_detail_identity_conflicts_fail_without_fallback():
    wrong = football_summary()
    wrong["header"]["id"] = "999999999"
    calls = []

    def handler(request):
        calls.append(request.url.path)
        payload = (
            wrong if request.url.path.endswith("/summary") else {"events": [scoreboard_event()]}
        )
        return httpx.Response(200, json=payload)

    client, http = client_with(handler)
    try:
        result = await discover(client)
        with pytest.raises(IdentityMismatchError) as error:
            await client.get_game_state(result.games[0].game_ref)
        assert error.value.code == "response_identity_mismatch"
    finally:
        await http.aclose()
    assert not any("statsapi.mlb.com" in path for path in calls)


@pytest.mark.parametrize("bad_value", ["-1", "NaN", "3.5"])
async def test_impossible_or_noninteger_scores_are_rejected(bad_value):
    event = scoreboard_event()
    event["competitions"][0]["competitors"][0]["score"] = bad_value

    def handler(request):
        return httpx.Response(200, json={"events": [event]})

    client, http = client_with(handler)
    try:
        result = await discover(client)
    finally:
        await http.aclose()
    assert result.games == [] and 1 <= result.coverage.discarded_event_count <= 3


@pytest.mark.parametrize(
    "mode,expected_calls", [("timeout", 2), ("429", 2), ("503", 2), ("403", 1)]
)
async def test_transport_http_retries_are_bounded(mode, expected_calls):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive")
        return httpx.Response(int(mode))

    client, http = client_with(handler)
    try:
        with pytest.raises(ProviderUnavailableError) as error:
            await discover(client)
        assert "sensitive" not in str(error.value)
    finally:
        await http.aclose()
    assert calls == expected_calls


async def test_oversized_response_rejected_before_json_use():
    def handler(request):
        return httpx.Response(200, content=b'{"events":[]}' + b" " * 100)

    client, http = client_with(handler, max_response_bytes=20)
    try:
        with pytest.raises(StateValidationError) as error:
            await discover(client)
        assert error.value.code == "response_too_large"
    finally:
        await http.aclose()


async def test_external_cancellation_propagates():
    async def handler(request):
        raise asyncio.CancelledError

    client, http = client_with(handler)
    try:
        with pytest.raises(asyncio.CancelledError):
            await discover(client)
    finally:
        await http.aclose()


def fallback_schedule(*, duplicate=False):
    games = [
        {
            "gamePk": 777,
            "gameDate": "2026-09-20T20:10:00Z",
            "teams": {
                "home": {"team": {"name": "New York Yankees"}},
                "away": {"team": {"name": "San Diego Padres"}},
            },
        }
    ]
    if duplicate:
        games.append({**deepcopy(games[0]), "gamePk": 778, "gameDate": "2026-09-20T20:20:00Z"})
    return {"dates": [{"games": games}]}


def fallback_feed():
    return {
        "gameData": {
            "game": {"pk": 777},
            "datetime": {"dateTime": "2026-09-20T20:10:00Z"},
            "teams": {
                "home": {"name": "New York Yankees"},
                "away": {"name": "San Diego Padres"},
            },
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
        },
        "liveData": {
            "linescore": {
                "currentInning": 6,
                "currentInningOrdinal": "6th",
                "inningHalf": "Bottom",
                "balls": 2,
                "strikes": 1,
                "outs": 2,
                "teams": {"home": {"runs": 3}, "away": {"runs": 2}},
                "offense": {
                    "first": {"id": 1},
                    "batter": {"fullName": "Aaron Judge"},
                },
                "defense": {"pitcher": {"fullName": "Yu Darvish"}},
            },
            "plays": {"currentPlay": {"result": {"description": "Single to left."}}},
        },
    }


async def test_mlb_fallback_requires_exact_match_then_returns_state():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "site.api.espn.com" and request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json={"events": [mlb_event()]})
        if request.url.host == "site.api.espn.com":
            return httpx.Response(503)
        if request.url.path.endswith("/schedule"):
            return httpx.Response(200, json=fallback_schedule())
        return httpx.Response(200, json=fallback_feed(), headers={"Cache-Control": "max-age=2"})

    client, http = client_with(handler)
    try:
        result = await discover(client, "Yankees", "mlb")
        state = await client.get_game_state(result.games[0].game_ref)
    finally:
        await http.aclose()
    assert state.source == "mlb_statsapi" and state.provider_game_id == "777"
    assert state.warnings[0].code == "mlb_fallback_used"
    assert isinstance(state.situation, BaseballSituation)
    assert (state.situation.balls, state.situation.outs, state.situation.on_first) == (2, 2, True)
    assert sum("/summary" in call for call in calls) == 2
    assert sum("/schedule" in call for call in calls) == 1
    assert sum("/feed/live" in call for call in calls) == 1


async def test_mlb_fallback_rejects_ambiguous_doubleheader():
    def handler(request):
        if request.url.host == "site.api.espn.com" and request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json={"events": [mlb_event()]})
        if request.url.host == "site.api.espn.com":
            return httpx.Response(503)
        return httpx.Response(200, json=fallback_schedule(duplicate=True))

    client, http = client_with(handler)
    try:
        result = await discover(client, "Yankees", "mlb")
        with pytest.raises(SportsStateError) as error:
            await client.get_game_state(result.games[0].game_ref)
        assert error.value.code == "game_state_unavailable"
        assert error.value.fields["fallback_error"] == "mlb_fallback_ambiguous"
    finally:
        await http.aclose()


async def test_mlb_fallback_conflict_preserves_primary_observation_with_warning():
    feed = fallback_feed()
    feed["liveData"]["linescore"]["teams"]["home"]["runs"] = 4

    def handler(request):
        if request.url.host == "site.api.espn.com" and request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json={"events": [mlb_event()]})
        if request.url.host == "site.api.espn.com":
            return httpx.Response(503)
        if request.url.path.endswith("/schedule"):
            return httpx.Response(200, json=fallback_schedule())
        return httpx.Response(200, json=feed)

    client, http = client_with(handler)
    try:
        result = await discover(client, "Yankees", "mlb")
        state = await client.get_game_state(result.games[0].game_ref)
    finally:
        await http.aclose()
    assert state.source == "espn"
    assert state.home_score == 3
    assert state.situation == BaseballSituation()
    assert state.warnings[-1].code == "provider_state_conflict"


async def test_nfl_failure_never_calls_mlb_fallback():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json={"events": [scoreboard_event()]})
        return httpx.Response(503)

    client, http = client_with(handler)
    try:
        result = await discover(client)
        with pytest.raises(ProviderUnavailableError):
            await client.get_game_state(result.games[0].game_ref)
    finally:
        await http.aclose()
    assert set(calls) == {"site.api.espn.com"}


def sample_state(source, score):
    return GameState(
        league="mlb",
        game_ref="ref",
        source=source,
        provider_game_id="1",
        home_team="New York Yankees",
        away_team="San Diego Padres",
        raw_home_team="New York Yankees",
        raw_away_team="San Diego Padres",
        scheduled_start=datetime(2026, 9, 20, 20, 10, tzinfo=UTC),
        timezone="UTC",
        local_date=date(2026, 9, 20),
        scheduled_start_local=datetime(2026, 9, 20, 20, 10, tzinfo=UTC),
        home_score=score,
        away_score=2,
        lifecycle="live",
        period=6,
        retrieved_at=datetime.now(UTC),
        observation_id="obs",
        source_url="https://example.test",
        situation=BaseballSituation(inning=6),
    )


def test_provider_conflict_is_explicit_and_never_averaged():
    primary = sample_state("espn", 3)
    fallback = sample_state("mlb_statsapi", 4)
    warning = observations_conflict(primary, fallback)
    assert warning and warning.code == "provider_state_conflict"
    assert primary.home_score == 3 and fallback.home_score == 4
    assert observations_conflict(primary, sample_state("mlb_statsapi", 3)) is None


def test_provider_conflict_rejects_different_identity():
    primary = sample_state("espn", 3)
    fallback = sample_state("mlb_statsapi", 3).model_copy(update={"away_team": "Boston Red Sox"})
    with pytest.raises(IdentityMismatchError):
        observations_conflict(primary, fallback)
