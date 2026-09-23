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


def completed_status(period=4, detail="Final"):
    return status(
        "STATUS_FINAL", "post", "Final", period=period, detail=detail, completed=True, clock="0:00"
    )


def football_box_summary(event=None):
    event = deepcopy(event or scoreboard_event(game_status=completed_status()))
    competition = event["competitions"][0]
    for competitor in competition["competitors"]:
        competitor["linescores"] = [
            {"displayValue": value}
            for value in ([3, 7, 7, 0] if competitor["homeAway"] == "away" else [0, 3, 7, 0])
        ]
    result = football_summary(event)
    if competition["status"]["type"]["state"] == "post":
        result.pop("drives", None)
    result["boxscore"] = {
        "teams": [
            {
                "team": competitor["team"],
                "statistics": [
                    {"name": "totalYards", "label": "Total Yards", "displayValue": "325"}
                ],
            }
            for competitor in competition["competitors"]
        ],
        "players": [
            {
                "team": competitor["team"],
                "statistics": [
                    {
                        "name": "passing",
                        "keys": ["completions/passingAttempts", "passingYards"],
                        "labels": ["C/ATT", "YDS"],
                        "athletes": [
                            {
                                "athlete": {
                                    "id": f"qb-{competitor['team']['id']}",
                                    "displayName": f"{competitor['team']['displayName']} QB",
                                },
                                "stats": ["20/30", "250"],
                            }
                        ],
                    }
                ],
            }
            for competitor in competition["competitors"]
        ],
    }
    return result


def baseball_box_summary(event=None):
    event = deepcopy(event or mlb_event())
    competition = event["competitions"][0]
    competition["status"]["periodPrefix"] = "Top"
    for competitor in competition["competitors"]:
        competitor["linescores"] = [
            {"displayValue": "0"},
            {"displayValue": "1" if competitor["homeAway"] == "away" else "0"},
            {"displayValue": "0"},
            {"displayValue": "0"},
            {"displayValue": "1" if competitor["homeAway"] == "home" else "0"},
            {"displayValue": "0"},
        ]
    result = mlb_summary(event)
    result["boxscore"] = {
        "teams": [
            {
                "team": competitor["team"],
                "statistics": [
                    {
                        "name": "batting",
                        "stats": [{"name": "hits", "displayValue": "6"}],
                    },
                    {
                        "name": "fielding",
                        "stats": [{"name": "errors", "displayValue": "0"}],
                    },
                ],
            }
            for competitor in competition["competitors"]
        ],
        "players": [
            {
                "team": competitor["team"],
                "statistics": [
                    {
                        "type": "batting",
                        "keys": [
                            "atBats",
                            "runs",
                            "hits",
                            "homeRuns",
                            "RBIs",
                            "walks",
                            "strikeouts",
                        ],
                        "athletes": [
                            {
                                "athlete": {
                                    "id": f"b-{competitor['team']['id']}",
                                    "displayName": "Game Batter",
                                },
                                "batOrder": 1,
                                "starter": True,
                                "position": {"abbreviation": "SS"},
                                "stats": ["2", "1", "1", "0", "1", "0", "1"],
                            }
                        ],
                    },
                    {
                        "type": "pitching",
                        "keys": [
                            "fullInnings.partInnings",
                            "hits",
                            "runs",
                            "earnedRuns",
                            "walks",
                            "strikeouts",
                            "homeRuns",
                            "pitches-strikes",
                        ],
                        "athletes": [
                            {
                                "athlete": {
                                    "id": f"p-{competitor['team']['id']}",
                                    "displayName": "Game Pitcher",
                                },
                                "starter": True,
                                "stats": ["1.1", "1", "0", "0", "0", "2", "0", "24-15"],
                            }
                        ],
                    },
                ],
            }
            for competitor in competition["competitors"]
        ],
    }
    return result


def baseball_play_summary(event=None):
    result = mlb_summary(event)
    result["plays"] = [
        {
            "id": f"baseball-play-{number}",
            "sequenceNumber": str(number),
            "type": {"text": "Pitch" if number != 3 else "Home Run"},
            "text": text,
            "awayScore": 2 if number >= 3 else 1,
            "homeScore": 1,
            "period": {"type": "Top", "number": 6, "displayValue": "6th Inning"},
            "scoringPlay": number == 3,
            "team": {"id": "25"},
            "wallclock": f"2026-09-20T20:1{number}:00Z",
            "atBatId": "at-bat-1",
            "resultCount": {"balls": number % 4, "strikes": min(number - 1, 2)},
            "outs": 1,
        }
        for number, text in enumerate(
            ["Called strike.", "Ball.", "Home run to left.", "In play, out."], start=1
        )
    ]
    result["plays"].append(
        {
            "id": "baseball-structural-marker",
            "sequenceNumber": "5",
            "type": {"text": "End Batter/Pitcher", "type": "end-batterpitcher"},
            "text": None,
            "period": {"type": "Top", "number": 6, "displayValue": "6th Inning"},
        }
    )
    return result


def football_play_summary(event=None):
    event = deepcopy(event or scoreboard_event(game_status=completed_status()))
    result = football_summary(event)
    result["drives"] = {
        "previous": [
            {
                "id": "drive-1",
                "plays": [
                    {
                        "id": f"football-play-{number}",
                        "sequenceNumber": str(number * 10),
                        "type": {"text": "Rush" if number != 3 else "Touchdown"},
                        "text": text,
                        "awayScore": 7 if number >= 3 else 0,
                        "homeScore": 0,
                        "period": {"number": 1 if number < 4 else 2},
                        "clock": {"displayValue": f"{16 - number}:00"},
                        "scoringPlay": number == 3,
                        "teamParticipants": [
                            {
                                "id": "29" if number != 4 else "1",
                                "type": "offense",
                            }
                        ],
                        "isPenalty": number == 2,
                        "isTurnover": number == 4,
                        "statYardage": number * 5,
                        "end": {
                            "down": min(number + 1, 4),
                            "distance": 10 - number,
                            "possessionText": "ATL 20",
                        },
                    }
                    for number, text in enumerate(
                        ["Run for five.", "Penalty on defense.", "Touchdown run.", "Fumble."],
                        start=1,
                    )
                ],
            }
        ]
    }
    return result


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


async def test_baseball_box_score_omits_unavailable_fields_and_normalizes_outs():
    clock = [datetime(2026, 9, 20, 20, 15, tzinfo=UTC)]
    summary_calls = 0

    def handler(request):
        nonlocal summary_calls
        if request.url.path.endswith("/summary"):
            summary_calls += 1
            payload = baseball_box_summary()
            if summary_calls >= 3:
                payload["boxscore"]["teams"][0]["statistics"][0]["stats"][0]["displayValue"] = "7"
            return httpx.Response(200, json=payload, headers={"Cache-Control": "max-age=60"})
        return httpx.Response(200, json={"events": [mlb_event()]})

    client, http = client_with(handler, now=lambda: clock[0])
    try:
        found = await discover(client, "Yankees", "mlb")
        first = await client.get_box_score(found.games[0].game_ref)
        clock[0] += timedelta(seconds=5)
        cached = await client.get_box_score(found.games[0].game_ref)
        clock[0] += timedelta(seconds=6)
        refreshed = await client.get_box_score(found.games[0].game_ref)
        clock[0] += timedelta(seconds=11)
        changed = await client.get_box_score(found.games[0].game_ref)
    finally:
        await http.aclose()

    payload = first.model_dump(mode="json")
    assert first.sport == "baseball" and first.lifecycle == "live"
    assert payload["line_score"]["innings"][-1]["home_runs"] is None
    assert "left_on_base" not in payload["line_score"]["away_totals"]
    batter = payload["batting"]["away"][0]
    assert batter["player_id"] == "b-25" and batter["hits"] == 1
    assert {"doubles", "triples", "stolen_bases"}.isdisjoint(batter)
    pitcher = payload["pitching"]["away"][0]
    assert pitcher["outs_recorded"] == 4
    assert pitcher["innings_pitched_display"] == "1.1"
    assert (pitcher["pitches"], pitcher["strikes"]) == (24, 15)
    assert first.completeness.batting == "partial"
    assert first.completeness.team_totals == "partial"
    assert cached.cache_hit and cached.cache_age_ms == 5000
    assert cached.observation_id == first.observation_id == refreshed.observation_id
    assert changed.observation_id != refreshed.observation_id
    assert summary_calls == 3


@pytest.mark.parametrize("league", ["nfl", "ncaa_football"])
async def test_completed_football_box_score_supports_nfl_and_ncaa(league):
    clock = [datetime(2026, 9, 20, 21, 0, tzinfo=UTC)]
    if league == "ncaa_football":
        event = scoreboard_event(
            game_status=completed_status(),
            teams=competitors(
                home="Ohio State Buckeyes",
                away="Michigan Wolverines",
                home_id="194",
                away_id="130",
                home_score="10",
                away_score="17",
            ),
        )
        event["competitions"][0]["situation"] = None
    else:
        event = scoreboard_event(game_status=completed_status())

    def handler(request):
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=football_box_summary(event))
        return httpx.Response(200, json={"events": [event]})

    client, http = client_with(handler, now=lambda: clock[0])
    try:
        found = await discover(client, "all", league)
        score = await client.get_box_score(found.games[0].game_ref)
        clock[0] += timedelta(seconds=11)
        cached = await client.get_box_score(found.games[0].game_ref)
    finally:
        await http.aclose()

    payload = score.model_dump(mode="json")
    assert score.league == league and score.sport == "football"
    assert score.lifecycle == "final" and score.is_partial is False
    assert cached.cache_hit and cached.cache_age_ms == 11000
    assert len(payload["line_score"]["periods"]) == 4
    assert payload["team_stats"]["away"][0]["name"] == "totalYards"
    assert payload["player_stats"]["home"][0]["category"] == "passing"
    assert payload["player_stats"]["home"][0]["players"][0]["player_id"].startswith("qb-")
    assert score.completeness.model_dump() == {
        "line_score": "complete",
        "team_stats": "complete",
        "player_stats": "complete",
    }


async def test_pregame_box_score_does_not_expose_provider_placeholders_or_player_stats():
    event = scoreboard_event(
        game_status=status("STATUS_SCHEDULED", "pre", "Scheduled", period=0, clock="0:00")
    )

    def handler(request):
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=football_box_summary(event))
        return httpx.Response(200, json={"events": [event]})

    client, http = client_with(handler)
    try:
        found = await discover(client)
        score = await client.get_box_score(found.games[0].game_ref)
    finally:
        await http.aclose()

    payload = score.model_dump(mode="json")
    assert "score" not in payload["away_team"] and "score" not in payload["home_team"]
    assert payload["line_score"]["periods"] == []
    assert payload["team_stats"] == {"away": [], "home": []}
    assert payload["player_stats"] == {"away": [], "home": []}
    assert score.completeness.model_dump() == {
        "line_score": "unavailable",
        "team_stats": "unavailable",
        "player_stats": "unavailable",
    }


async def test_baseball_player_directory_and_detail_reuse_one_box_score_observation():
    summary_calls = 0

    def handler(request):
        nonlocal summary_calls
        if request.url.path.endswith("/summary"):
            summary_calls += 1
            return httpx.Response(200, json=baseball_box_summary())
        return httpx.Response(200, json={"events": [mlb_event()]})

    client, http = client_with(handler)
    try:
        found = await discover(client, "Yankees", "mlb")
        game_ref = found.games[0].game_ref
        players = await client.list_players(game_ref)
        batter = await client.get_player_stats(game_ref, "b-25")
        pitcher = await client.get_player_stats(game_ref, "p-25")
    finally:
        await http.aclose()

    assert [(player.player_id, player.stat_groups) for player in players.players] == [
        ("b-25", ["batting"]),
        ("p-25", ["pitching"]),
        ("b-10", ["batting"]),
        ("p-10", ["pitching"]),
    ]
    assert batter.player_name == "Game Batter" and batter.team == "San Diego Padres"
    assert batter.batting is not None and batter.batting.hits == 1
    assert batter.pitching is None
    assert pitcher.pitching is not None and pitcher.pitching.outs_recorded == 4
    assert pitcher.batting is None
    assert players.observation_id == batter.observation_id == pitcher.observation_id
    assert batter.cache_hit and pitcher.cache_hit
    assert summary_calls == 1


@pytest.mark.parametrize("league", ["nfl", "ncaa_football"])
async def test_football_player_directory_and_detail_return_only_selected_player(league):
    event = scoreboard_event(game_status=completed_status())
    if league == "ncaa_football":
        event = scoreboard_event(
            game_status=completed_status(),
            teams=competitors(
                home="Ohio State Buckeyes",
                away="Michigan Wolverines",
                home_id="194",
                away_id="130",
                home_score="10",
                away_score="17",
            ),
        )
        event["competitions"][0]["situation"] = None

    def handler(request):
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=football_box_summary(event))
        return httpx.Response(200, json={"events": [event]})

    client, http = client_with(handler)
    try:
        found = await discover(client, "all", league)
        game_ref = found.games[0].game_ref
        players = await client.list_players(game_ref)
        selected = players.players[0]
        stats = await client.get_player_stats(game_ref, selected.player_id)
    finally:
        await http.aclose()

    assert len(players.players) == 2
    assert selected.stat_groups == ["passing"]
    assert stats.player_id == selected.player_id and stats.player_name == selected.name
    assert stats.team == selected.team and stats.stat_groups is not None
    assert stats.stat_groups[0].category == "passing"
    assert stats.stat_groups[0].statistics[1].name == "passingYards"
    assert stats.stat_groups[0].statistics[1].value == "250"
    assert "player_stats" not in stats.model_dump(mode="json")


async def test_player_lookup_rejects_invalid_or_missing_id_without_leaking_other_players():
    summary_calls = 0

    def handler(request):
        nonlocal summary_calls
        if request.url.path.endswith("/summary"):
            summary_calls += 1
            return httpx.Response(200, json=football_box_summary())
        return httpx.Response(200, json={"events": [scoreboard_event()]})

    client, http = client_with(handler)
    try:
        found = await discover(client)
        with pytest.raises(SportsStateError) as invalid:
            await client.get_player_stats(found.games[0].game_ref, "bad player id")
        assert invalid.value.code == "invalid_player_id"
        assert summary_calls == 0
        with pytest.raises(SportsStateError) as missing:
            await client.get_player_stats(found.games[0].game_ref, "missing-player")
        assert missing.value.code == "player_not_found"
    finally:
        await http.aclose()

    assert summary_calls == 1


async def test_baseball_play_windows_use_stable_ids_filters_and_cache():
    summary_calls = 0

    def handler(request):
        nonlocal summary_calls
        if request.url.path.endswith("/summary"):
            summary_calls += 1
            return httpx.Response(200, json=baseball_play_summary())
        return httpx.Response(200, json={"events": [mlb_event()]})

    client, http = client_with(handler)
    try:
        found = await discover(client, "Yankees", "mlb")
        game_ref = found.games[0].game_ref
        latest = await client.get_play_by_play(game_ref, limit=2)
        earlier = await client.get_play_by_play(
            game_ref, limit=2, before_play_id=latest.first_play_id
        )
        unseen = await client.get_play_by_play(
            game_ref, limit=10, after_play_id=latest.resume_after_play_id
        )
        scoring = await client.get_play_by_play(game_ref, limit=10, play_filter="scoring")
    finally:
        await http.aclose()

    assert [play.play_id for play in latest.plays] == ["baseball-play-3", "baseball-play-4"]
    assert latest.has_earlier and not latest.has_later
    assert latest.next_before_play_id == "baseball-play-3"
    assert latest.resume_after_play_id == "baseball-play-4"
    assert [play.play_id for play in earlier.plays] == ["baseball-play-1", "baseball-play-2"]
    assert earlier.anchor_mode == "before" and earlier.resume_after_play_id == "baseball-play-3"
    assert unseen.plays == [] and unseen.anchor_mode == "after"
    assert unseen.resume_after_play_id == "baseball-play-4"
    assert unseen.has_earlier and not unseen.has_later
    assert [play.play_id for play in scoring.plays] == ["baseball-play-3"]
    assert scoring.plays[0].context.sport == "baseball"
    assert latest.total_plays == 4
    assert not latest.warnings
    assert latest.observation_id == earlier.observation_id == unseen.observation_id
    assert earlier.cache_hit and unseen.cache_hit and scoring.cache_hit
    assert summary_calls == 1


async def test_football_play_windows_support_period_team_and_scoring_filters():
    def handler(request):
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=football_play_summary())
        return httpx.Response(
            200, json={"events": [scoreboard_event(game_status=completed_status())]}
        )

    client, http = client_with(handler)
    try:
        found = await discover(client)
        game_ref = found.games[0].game_ref
        latest = await client.get_play_by_play(game_ref, limit=2)
        scoring = await client.get_play_by_play(game_ref, limit=10, play_filter="scoring")
        away_first = await client.get_play_by_play(game_ref, limit=10, period=1, team="away")
    finally:
        await http.aclose()

    assert [play.play_id for play in latest.plays] == ["football-play-3", "football-play-4"]
    assert latest.granularity == "play" and latest.total_plays == 4
    assert latest.plays[-1].context.sport == "football"
    assert latest.plays[-1].context.turnover is True
    assert [play.play_id for play in scoring.plays] == ["football-play-3"]
    assert [play.play_id for play in away_first.plays] == [
        "football-play-1",
        "football-play-2",
        "football-play-3",
    ]
    assert away_first.period_filter == 1 and away_first.team_filter == "away"


async def test_play_window_rejects_invalid_arguments_before_provider_io():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"events": [scoreboard_event()]})

    client, http = client_with(handler)
    try:
        found = await discover(client)
        game_ref = found.games[0].game_ref
        calls.clear()
        with pytest.raises(SportsStateError) as conflict:
            await client.get_play_by_play(
                game_ref,
                before_play_id="play-1",
                after_play_id="play-2",
            )
        assert conflict.value.code == "conflicting_play_anchors"
        with pytest.raises(SportsStateError) as bad_limit:
            await client.get_play_by_play(game_ref, limit=51)
        assert bad_limit.value.code == "invalid_play_limit"
        with pytest.raises(SportsStateError) as bad_id:
            await client.get_play_by_play(game_ref, after_play_id="bad play id")
        assert bad_id.value.code == "invalid_play_id"
    finally:
        await http.aclose()

    assert calls == []


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
    assert "separate observations and may drift" in result.usage_note
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
    assert result.coverage.utc_boundary_check is True
    assert "overlap two ESPN UTC date pages" in result.coverage.utc_boundary_note


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
        assert unclear.discovery_mode == "clarification"
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
            compact=True,
        )
    finally:
        await http.aclose()

    assert result.discovery_mode == "schedule"
    assert result.compact is True
    assert len(result.games) == 1
    assert result.games[0].home_team == "Atlanta Falcons"
    assert set(result.games[0].model_dump()) == {
        "game_ref",
        "home_team",
        "away_team",
        "scheduled_start",
        "scheduled_start_local",
        "lifecycle",
    }
    assert len(calls) == 2
    assert result.coverage.utc_boundary_check is True


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


async def test_espn_player_ids_and_partial_base_state_are_enriched_from_boxscore():
    summary = mlb_summary()
    summary["situation"] = {
        "balls": 1,
        "strikes": 2,
        "outs": 1,
        "onSecond": {"playerId": 55},
        "batter": {"playerId": 77},
        "pitcher": {"playerId": 88},
    }
    summary["boxscore"] = {
        "players": [
            {
                "statistics": [
                    {
                        "athletes": [
                            {"athlete": {"id": "77", "displayName": "Current Batter"}},
                            {"athlete": {"id": "88", "displayName": "Current Pitcher"}},
                        ]
                    }
                ]
            }
        ]
    }

    def handler(request):
        payload = summary if request.url.path.endswith("/summary") else {"events": [mlb_event()]}
        return httpx.Response(200, json=payload)

    client, http = client_with(handler)
    try:
        result = await discover(client, "Yankees", "mlb")
        state = await client.get_game_state(result.games[0].game_ref)
    finally:
        await http.aclose()

    assert isinstance(state.situation, BaseballSituation)
    assert (state.situation.on_first, state.situation.on_second, state.situation.on_third) == (
        False,
        True,
        False,
    )
    assert state.situation.batter == "Current Batter"
    assert state.situation.pitcher == "Current Pitcher"


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
    def team_box(batter_id, pitcher_id, batter_name, pitcher_name):
        return {
            "batters": [batter_id],
            "pitchers": [pitcher_id],
            "players": {
                f"ID{batter_id}": {
                    "person": {"id": batter_id, "fullName": batter_name},
                    "battingOrder": "100",
                    "allPositions": [{"abbreviation": "SS"}],
                    "stats": {
                        "batting": {
                            "atBats": 2,
                            "runs": 1,
                            "hits": 1,
                            "doubles": 1,
                            "triples": 0,
                            "homeRuns": 0,
                            "rbi": 1,
                            "baseOnBalls": 0,
                            "strikeOuts": 1,
                            "stolenBases": 0,
                        }
                    },
                    "seasonStats": {"batting": {"homeRuns": 99}},
                },
                f"ID{pitcher_id}": {
                    "person": {"id": pitcher_id, "fullName": pitcher_name},
                    "stats": {
                        "pitching": {
                            "gamesStarted": 1,
                            "inningsPitched": "1.1",
                            "outs": 4,
                            "hits": 1,
                            "runs": 0,
                            "earnedRuns": 0,
                            "baseOnBalls": 0,
                            "strikeOuts": 2,
                            "homeRuns": 0,
                            "numberOfPitches": 24,
                            "strikes": 15,
                        }
                    },
                    "seasonStats": {"pitching": {"era": "3.03"}},
                },
            },
        }

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
                "innings": [
                    {"num": 1, "away": {"runs": 1}, "home": {"runs": 0}},
                    {"num": 2, "away": {"runs": 0}, "home": {"runs": 1}},
                ],
                "teams": {
                    "home": {"runs": 3, "hits": 6, "errors": 0, "leftOnBase": 4},
                    "away": {"runs": 2, "hits": 5, "errors": 1, "leftOnBase": 3},
                },
                "offense": {
                    "first": {"id": 1},
                    "batter": {"fullName": "Aaron Judge"},
                },
                "defense": {"pitcher": {"fullName": "Yu Darvish"}},
            },
            "boxscore": {
                "teams": {
                    "away": team_box(101, 201, "Away Batter", "Away Pitcher"),
                    "home": team_box(102, 202, "Home Batter", "Home Pitcher"),
                }
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


async def test_mlb_box_score_fallback_uses_only_game_statistics():
    def handler(request):
        if request.url.host == "site.api.espn.com" and request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json={"events": [mlb_event()]})
        if request.url.host == "site.api.espn.com":
            return httpx.Response(503)
        if request.url.path.endswith("/schedule"):
            return httpx.Response(200, json=fallback_schedule())
        return httpx.Response(200, json=fallback_feed())

    client, http = client_with(handler)
    try:
        result = await discover(client, "Yankees", "mlb")
        score = await client.get_box_score(result.games[0].game_ref)
    finally:
        await http.aclose()

    payload = score.model_dump(mode="json")
    assert score.source == "mlb_statsapi" and score.provider_game_id == "777"
    assert payload["batting"]["away"][0]["home_runs"] == 0
    assert payload["pitching"]["away"][0]["outs_recorded"] == 4
    assert "season_stats" not in json.dumps(payload).lower()
    assert "3.03" not in json.dumps(payload)
    assert score.completeness.model_dump() == {
        "line_score": "complete",
        "team_totals": "complete",
        "batting": "complete",
        "pitching": "complete",
    }


async def test_mlb_play_by_play_fallback_returns_bounded_at_bats():
    feed = fallback_feed()
    feed["liveData"]["plays"]["allPlays"] = [
        {
            "about": {
                "atBatIndex": 0,
                "inning": 1,
                "halfInning": "top",
                "startTime": "2026-09-20T20:11:00Z",
                "isScoringPlay": False,
            },
            "result": {
                "event": "Single",
                "description": "Away Batter singles to center.",
                "rbi": 0,
                "awayScore": 0,
                "homeScore": 0,
            },
            "count": {"balls": 0, "strikes": 0, "outs": 0},
        },
        {
            "about": {
                "atBatIndex": 1,
                "inning": 1,
                "halfInning": "top",
                "startTime": "2026-09-20T20:13:00Z",
                "isScoringPlay": True,
            },
            "result": {
                "event": "Home Run",
                "description": "Away Batter homers to left.",
                "rbi": 2,
                "awayScore": 2,
                "homeScore": 0,
            },
            "count": {"balls": 0, "strikes": 0, "outs": 0},
        },
    ]

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
        plays = await client.get_play_by_play(
            result.games[0].game_ref, limit=10, play_filter="scoring"
        )
    finally:
        await http.aclose()

    assert plays.source == "mlb_statsapi" and plays.granularity == "at_bat"
    assert [play.play_id for play in plays.plays] == ["777:1"]
    assert plays.plays[0].text == "Away Batter homers to left."
    assert plays.plays[0].team == "San Diego Padres"
    assert plays.warnings[0].code == "mlb_fallback_used"


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
