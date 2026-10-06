"""Deterministic sports game-state normalization, identity, budgets, and fallback tests."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from market_agent.providers.game_state import (
    BaseballSituation,
    GameState,
    IdentityMismatchError,
    SportsStateClient,
    SportsStateError,
    StateValidationError,
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
        "missing_required_fields": {},
        "missing_optional_fields": {},
    }


async def test_baseball_play_state_separates_pre_post_outs_and_substitutions():
    payload = baseball_play_summary()
    for play in payload["plays"][:3]:
        play["outs"] = 0
    payload["plays"][3]["outs"] = 1
    payload["plays"].insert(
        3,
        {
            "id": "baseball-substitution-1",
            "sequenceNumber": "35",
            "type": {"text": "Offensive Substitution", "type": "substitution"},
            "text": "Pinch-hitter entered for the shortstop.",
            "awayScore": 2,
            "homeScore": 1,
            "period": {"type": "Top", "number": 6, "displayValue": "6th Inning"},
            "scoringPlay": False,
            "team": {"id": "25"},
            "outs": 0,
            "participants": [
                {
                    "type": "incoming",
                    "athlete": {"id": "pinch-1", "displayName": "Pinch Hitter"},
                }
            ],
        },
    )

    def handler(request):
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"events": [mlb_event()]})

    client, http = client_with(handler)
    try:
        found = await discover(client, "Yankees", "mlb")
        plays = await client.get_play_by_play(found.games[0].game_ref, limit=10)
    finally:
        await http.aclose()

    home_run = next(play for play in plays.plays if play.play_id == "baseball-play-3")
    substitution = next(play for play in plays.plays if play.play_id == "baseball-substitution-1")
    final_out = next(play for play in plays.plays if play.play_id == "baseball-play-4")
    assert home_run.context.outs_before == 0 and home_run.context.outs_after == 0
    assert substitution.event_kind == "substitution"
    assert substitution.context.outs_before == substitution.context.outs_after == 0
    assert substitution.substitution is not None
    assert substitution.substitution.participants[0].name == "Pinch Hitter"
    assert final_out.context.outs_before == 0 and final_out.context.outs_after == 1


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
