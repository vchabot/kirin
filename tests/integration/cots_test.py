# coding: utf8
#
# Copyright (c) 2001, Canal TP and/or its affiliates. All rights reserved.
#
# This file is part of Navitia,
#     the software to build cool stuff with public transport.
#
# Hope you'll enjoy and contribute to this project,
#     powered by Canal TP (www.canaltp.fr).
# Help us simplify mobility and open public transport:
#     a non ending quest to the responsive locomotion way of traveling!
#
# LICENCE: This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Stay tuned using
# twitter @navitia
# [matrix] channel #navitia:matrix.org (https://app.element.io/#/room/#navitia:matrix.org)
# https://groups.google.com/d/forum/navitia
# www.navitia.io
from __future__ import absolute_import, print_function, unicode_literals, division
from datetime import date, datetime, timedelta

import pytest

from kirin.core.types import ConnectorType
from tests.check_utils import api_post, api_get
from kirin import app
from tests import mock_navitia
from tests.check_utils import get_fixture_data
from kirin.core.model import (
    db,
    RealTimeUpdate,
    TripUpdate,
    StopTimeUpdate,
    VehicleJourney,
    DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
    DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
)
from kirin.tasks import purge_trip_update, purge_rt_update
from tests.integration.utils_cots_test import requests_mock_cause_message
from tests.integration.conftest import COTS_CONTRIBUTOR_ID, COTS_CONTRIBUTOR_DB_ID
from tests.integration.utils_sncf_test import (
    check_db_96231_delayed,
    check_db_john_trip_removal,
    check_db_96231_trip_removal,
    check_db_6113_trip_removal,
    check_db_6114_trip_removal,
    check_db_96231_normal,
    check_db_840427_partial_removal,
    check_db_96231_partial_removal,
    check_db_870154_partial_removal,
    check_db_870154_delay,
    check_db_870154_normal,
    check_db_96231_mixed_statuses_inside_stops,
    check_db_96231_mixed_statuses_delay_removal_delay,
    check_db_6111_trip_removal_pass_midnight,
)


@pytest.fixture(scope="function", autouse=True)
def navitia(monkeypatch):
    """
    Mock all calls to navitia for this fixture
    """
    monkeypatch.setattr("navitia_wrapper._NavitiaWrapper.query", mock_navitia.mock_navitia_query)


@pytest.fixture(scope="function", autouse=True)
def mock_cause_message(requests_mock):
    """
    Mock all calls to cause message sub-service for this fixture
    """
    return requests_mock_cause_message(requests_mock)


def test_wrong_cots_post():
    """
    simple json post on the api
    """
    res, status = api_post("/cots", check=False, data="{}")

    assert status == 400
    assert "No object" in res.get("error")


def test_cots_post_no_data():
    """
    when no data is given, we got a 400 error
    """
    tester = app.test_client()
    resp = tester.post("/cots")
    assert resp.status_code == 400

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 0
        assert len(TripUpdate.query.all()) == 0
        assert len(StopTimeUpdate.query.all()) == 0


def test_cots_simple_post(mock_rabbitmq):
    """
    simple COTS post should be stored in db as a RealTimeUpdate
    """
    cots_file = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_file)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        rtu_array = RealTimeUpdate.query.all()
        assert len(rtu_array) == 1
        rtu = rtu_array[0]
        assert "-" in rtu.id
        assert rtu.created_at
        assert rtu.status == "OK"
        assert rtu.error is None
        assert rtu.contributor_id == COTS_CONTRIBUTOR_ID
        assert rtu.connector == ConnectorType.cots.value
        assert "listePointDeParcours" in rtu.raw_data
    assert mock_rabbitmq.call_count == 1


def test_save_bad_raw_cots():
    """
    send a bad formatted COTS, the bad raw COTS should be saved in db
    """
    bad_cots = get_fixture_data("bad_cots.json")
    res = api_post("/cots", data=bad_cots, check=False)
    assert res[1] == 400
    assert res[0]["message"] == "invalid arguments"
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert RealTimeUpdate.query.first().status == "KO"
        assert (
            RealTimeUpdate.query.first().error
            == 'invalid json, impossible to find "numeroCourse" in json dict {"bad":"one","cots":"toto"}'
        )
        assert RealTimeUpdate.query.first().raw_data == bad_cots


def test_cots_delayed_simple_post(mock_rabbitmq):
    """
    simple delayed stops post
    """
    cots_96231 = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6
        db_trip_delayed = TripUpdate.find_by_dated_vj(
            "trip:OCETrainTER-87212027-85000109-3:11859", datetime(2015, 9, 21, 15, 21)
        )
        assert db_trip_delayed.stop_time_updates[4].message is None
    db_trip_delayed = check_db_96231_delayed(contributor_id=COTS_CONTRIBUTOR_ID)
    assert db_trip_delayed.effect == "SIGNIFICANT_DELAYS"
    assert len(db_trip_delayed.stop_time_updates) == 6
    assert mock_rabbitmq.call_count == 1


def test_cots_delayed_then_ok(mock_rabbitmq):
    """
    We delay a stop, then the vj is back on time
    """
    cots_96231 = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6
        assert RealTimeUpdate.query.first().status == "OK"
    db_trip_delayed = check_db_96231_delayed(contributor_id=COTS_CONTRIBUTOR_ID)
    assert db_trip_delayed.effect == "SIGNIFICANT_DELAYS"
    assert len(db_trip_delayed.stop_time_updates) == 6
    assert mock_rabbitmq.call_count == 1

    cots_96231 = get_fixture_data("cots_train_96231_normal.json")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6
    check_db_96231_normal(contributor_id=COTS_CONTRIBUTOR_ID)
    assert mock_rabbitmq.call_count == 2


def test_cots_bad_tz(mock_rabbitmq):
    """
    We use a "normal" feed (no delay), but:
    * on stop 3, no TZ is provided (check no bad crash: all feed rejected)
    """
    cots_96231 = get_fixture_data("cots_train_96231_normal_bad_tz.json")
    res, status = api_post("/cots", check=False, data=cots_96231)

    assert status == 400
    assert "Impossible to parse timezoned datetime" in res.get("error")
    assert mock_rabbitmq.call_count == 0


def test_cots_paris_tz(mock_rabbitmq):
    """
    We use a "normal" feed (no delay), but:
    * on stop 2, TZ is +0200 (and not the usual +0000)
    """
    cots_96231 = get_fixture_data("cots_train_96231_normal_paris_tz.json")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6
    check_db_96231_normal(contributor_id=COTS_CONTRIBUTOR_ID)
    assert mock_rabbitmq.call_count == 1


def test_cots_partial_removal_delayed_then_partial_removal_ok(mock_rabbitmq):
    """

    Removing first 5 stops and delay the rest by 10 min,
    then only the first 4 stops are removed, the rest are back and on time
    (no information on 10th stop so it should stay delayed)
    """
    cots_870154 = get_fixture_data("cots_train_870154_partial_removal_delay.json")
    res = api_post("/cots", data=cots_870154)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert RealTimeUpdate.query.first().status == "OK"
    check_db_870154_partial_removal(contributor_id=COTS_CONTRIBUTOR_ID)
    check_db_870154_delay()
    assert mock_rabbitmq.call_count == 1

    cots_870154 = get_fixture_data("cots_train_870154_partial_normal.json")
    res = api_post("/cots", data=cots_870154)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
    check_db_870154_partial_removal(contributor_id=COTS_CONTRIBUTOR_ID)
    check_db_870154_normal()
    assert mock_rabbitmq.call_count == 2


def test_cots_delayed_post_twice(mock_rabbitmq):
    """
    double delayed stops post
    """
    cots_96231 = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6
    db_trip_delayed = check_db_96231_delayed(contributor_id=COTS_CONTRIBUTOR_ID)
    assert db_trip_delayed.effect == "SIGNIFICANT_DELAYS"
    assert len(db_trip_delayed.stop_time_updates) == 6
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_cots_mixed_statuses_inside_stop_times(mock_rabbitmq):
    """
    stops have mixed statuses (between their departure and arrival especially)
    on 2nd stop departure is delayed by 30 s, arrival OK
    on 3rd stop arrival is delayed by 30 s, departure OK (catching up on lost time)
    on 4th stop arrival is delayed by 1 min, departure is removed
    """
    cots_96231 = get_fixture_data("cots_train_96231_mixed_statuses_inside_stops.json")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6
    check_db_96231_mixed_statuses_inside_stops(contributor_id=COTS_CONTRIBUTOR_ID)

    assert mock_rabbitmq.call_count == 1


def test_cots_mixed_statuses_delay_removal_delay(mock_rabbitmq):
    """
    stops have mixed statuses
    on 2nd stop is delayed by 5 min
    on 3rd is removed
    on 4th stop is delayed by 2 min
    """
    cots_96231 = get_fixture_data("cots_train_96231_mixed_statuses_delay_removal_delay.json")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6
    check_db_96231_mixed_statuses_delay_removal_delay(contributor_id=COTS_CONTRIBUTOR_ID)
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 1


def test_cots_trip_delayed_then_removal(mock_rabbitmq):
    """
    post delayed stops then trip removal on the same trip
    """
    cots_96231_delayed = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_96231_delayed)
    assert "cots feed processed" in res.get("message")
    cots_96231_trip_removal = get_fixture_data("cots_train_96231_trip_removal.json")
    res = api_post("/cots", data=cots_96231_trip_removal)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 0
    check_db_96231_trip_removal()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_cots_trip_delayed_then_partial_removal(mock_rabbitmq):
    """
    post delayed stops then trip removal on the same trip
    """
    cots_96231_delayed = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_96231_delayed)
    assert "cots feed processed" in res.get("message")
    cots_96231_partial_removal = get_fixture_data("cots_train_96231_partial_removal.json")
    res = api_post("/cots", data=cots_96231_partial_removal)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 6
        assert RealTimeUpdate.query.first().status == "OK"
    check_db_96231_partial_removal(contributor_id=COTS_CONTRIBUTOR_ID)
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_cots_trip_removal_simple_post(mock_rabbitmq):
    """
    simple trip removal post
    """
    cots_6113 = get_fixture_data("cots_train_6113_trip_removal.json")
    res = api_post("/cots", data=cots_6113)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 0
    check_db_6113_trip_removal()
    assert mock_rabbitmq.call_count == 1


def test_cots_trip_removal_pass_midnight(mock_rabbitmq):
    """
    simple trip removal post on a pass-midnight trip
    """
    cots_6111 = get_fixture_data("cots_train_6111_pass_midnight_trip_removal.json")
    res = api_post("/cots", data=cots_6111)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 0
    check_db_6111_trip_removal_pass_midnight()
    assert mock_rabbitmq.call_count == 1


def test_cots_delayed_and_trip_removal_post(mock_rabbitmq):
    """
    post delayed stops on one trip then trip removal on another
    """
    cots_96231 = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_96231)
    assert "cots feed processed" in res.get("message")

    cots_6113 = get_fixture_data("cots_train_6113_trip_removal.json")
    res = api_post("/cots", data=cots_6113)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 2
        assert len(StopTimeUpdate.query.all()) == 6
    db_trip_delayed = check_db_96231_delayed(contributor_id=COTS_CONTRIBUTOR_ID)
    assert db_trip_delayed.effect == "SIGNIFICANT_DELAYS"
    assert len(db_trip_delayed.stop_time_updates) == 6
    check_db_6113_trip_removal()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_cots_trip_removal_post_twice(mock_rabbitmq):
    """
    double trip removal post
    """
    cots_6113 = get_fixture_data("cots_train_6113_trip_removal.json")
    res = api_post("/cots", data=cots_6113)
    assert "cots feed processed" in res.get("message")
    res = api_post("/cots", data=cots_6113)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 0
    check_db_6113_trip_removal()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_cots_trip_with_parity(mock_rabbitmq):
    """
    a trip with a parity has been impacted, there should be 2 VJ impacted
    """
    cots_6113 = get_fixture_data("cots_train_6113_trip_removal.json")
    cots_6113_14 = cots_6113.replace('"numeroCourse": "006113",', '"numeroCourse": "006113/4",')
    res = api_post("/cots", data=cots_6113_14)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1

        # there should be 2 trip updated,
        # - trip:OCETGV-87686006-87751008-2:25768-2 for the headsign 6114
        # - trip:OCETGV-87686006-87751008-2:25768 for the headsign 6113

        assert len(TripUpdate.query.all()) == 2
        assert len(StopTimeUpdate.query.all()) == 0

    check_db_6113_trip_removal()
    check_db_6114_trip_removal()

    assert mock_rabbitmq.call_count == 1


def test_cots_trip_removal_reactivation_delay(mock_rabbitmq):
    """
    trip removal, then reactivation, then delay
    """
    cots_6113 = get_fixture_data("cots_train_6113_trip_removal.json")
    res = api_post("/cots", data=cots_6113)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 0
    check_db_6113_trip_removal()

    react_6113 = get_fixture_data("cots_train_6113_trip_reactivation.json")
    res = api_post("/cots", data=react_6113)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 4

        db_trip_react = TripUpdate.find_by_dated_vj(
            "trip:OCETGV-87686006-87751008-2:25768", datetime(2015, 10, 6, 11, 16)
        )
        assert db_trip_react

        assert db_trip_react.vj.navitia_trip_id == "trip:OCETGV-87686006-87751008-2:25768"
        assert db_trip_react.vj.start_timestamp == datetime(2015, 10, 6, 11, 16)
        assert db_trip_react.vj_id == db_trip_react.vj.id
        assert db_trip_react.status == "update"
        assert db_trip_react.effect == "UNKNOWN_EFFECT"
        assert db_trip_react.message == "Accident à un Passage à Niveau"

        assert len(db_trip_react.stop_time_updates) == 4
        for s in db_trip_react.stop_time_updates:
            assert s.arrival_status == "none"
            assert s.departure_status == "none"
            assert s.message is None

    react_delay_6113 = get_fixture_data("cots_train_6113_trip_reactivation_delay.json")
    res = api_post("/cots", data=react_delay_6113)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 3
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 4

        db_trip_react = TripUpdate.find_by_dated_vj(
            "trip:OCETGV-87686006-87751008-2:25768", datetime(2015, 10, 6, 11, 16)
        )
        assert db_trip_react

        assert db_trip_react.vj.navitia_trip_id == "trip:OCETGV-87686006-87751008-2:25768"
        assert db_trip_react.vj.start_timestamp == datetime(2015, 10, 6, 11, 16)
        assert db_trip_react.vj_id == db_trip_react.vj.id
        assert db_trip_react.status == "update"
        assert db_trip_react.effect == "SIGNIFICANT_DELAYS"
        assert db_trip_react.message == "Accident à un Passage à Niveau"

        assert len(db_trip_react.stop_time_updates) == 4
        for i, s in enumerate(db_trip_react.stop_time_updates):
            if i > 0:
                assert s.arrival_status == "update"
                assert s.arrival_delay == timedelta(minutes=10)
            if i < 3:
                assert s.departure_status == "update"
                assert s.departure_delay == timedelta(minutes=10)

    assert mock_rabbitmq.call_count == 3


def test_cots_trip_with_parity_one_unknown_vj(mock_rabbitmq):
    """
    a trip with a parity has been impacted, but the train 6112 is not known by navitia
    there should be only the train 6113 impacted
    """
    cots_6113 = get_fixture_data("cots_train_6113_trip_removal.json")
    cots_6112_13 = cots_6113.replace('"numeroCourse": "006113",', '"numeroCourse": "006112/3",')
    res = api_post("/cots", data=cots_6112_13)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 0

    check_db_6113_trip_removal()

    assert mock_rabbitmq.call_count == 1


def test_cots_trip_unknown_vj(mock_rabbitmq):
    """
    a trip with a parity has been impacted, but the train 6112 is not known by navitia
    there should be only the train 6113 impacted
    """
    cots_6113 = get_fixture_data("cots_train_6113_trip_removal.json")
    cots_6112 = cots_6113.replace('"numeroCourse": "006113",', '"numeroCourse": "006112",')

    res = api_post("/cots", data=cots_6112, check=False)
    assert res[1] == 404
    assert res[0]["error"] == "no train found for headsign(s) 006112"
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 0
        assert len(StopTimeUpdate.query.all()) == 0
        assert RealTimeUpdate.query.first().status == "KO"
        assert RealTimeUpdate.query.first().error == "no train found for headsign(s) 006112"
        assert RealTimeUpdate.query.first().raw_data == cots_6112

    status = api_get("/status")
    assert "-" in status["last_update"][COTS_CONTRIBUTOR_ID]  # only check it's a date
    assert status["last_valid_update"] == {}
    assert status["last_update_error"][COTS_CONTRIBUTOR_ID] == "no train found for headsign(s) 006112"

    assert mock_rabbitmq.call_count == 0


def test_cots_two_trip_removal_one_post(mock_rabbitmq):
    """
    post one COTS trip removal on two trips
    (navitia mock returns 2 vj for 'JOHN' headsign)
    """
    cots_john_trip_removal = get_fixture_data("cots_train_JOHN_trip_removal.json")
    res = api_post("/cots", data=cots_john_trip_removal)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 2
        assert len(StopTimeUpdate.query.all()) == 0
    check_db_john_trip_removal()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 1


def test_cots_two_trip_removal_post_twice(mock_rabbitmq):
    """
    post twice COTS trip removal on two trips
    """
    cots_john_trip_removal = get_fixture_data("cots_train_JOHN_trip_removal.json")
    res = api_post("/cots", data=cots_john_trip_removal)
    assert "cots feed processed" in res.get("message")
    res = api_post("/cots", data=cots_john_trip_removal)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 2
        assert len(StopTimeUpdate.query.all()) == 0
    check_db_john_trip_removal()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_cots_partial_removal_then_reactivation(mock_rabbitmq):
    """
    the trip 840427 is partially deleted

    Normally there are 7 stops in this VJ, but 3 (Bar-sur-Aube, Vendeuvre and Troyes) are
    removed (departure from Chaumont deleted)

    Then 2 stops are reactivated

    Then all 3 are reactivated

    Then the penultimate is delayed by 5 min

    """
    # Simple partial removal
    cots_080427_removal = get_fixture_data("cots_train_840427_partial_removal.json")
    res = api_post("/cots", data=cots_080427_removal)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 7
        assert RealTimeUpdate.query.first().status == "OK"
    check_db_840427_partial_removal(contributor_id=COTS_CONTRIBUTOR_ID)
    assert mock_rabbitmq.call_count == 1

    # Then 2 stops are reactivated
    cots_080427_partial_react = get_fixture_data("cots_train_840427_partial_reactivation.json")
    res = api_post("/cots", data=cots_080427_partial_react)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 7
        assert RealTimeUpdate.query.first().status == "OK"

        db_trip_partial_react = TripUpdate.find_by_dated_vj("OCE:SN840427F03001", datetime(2017, 3, 18, 13, 5))
        assert db_trip_partial_react

        assert db_trip_partial_react.vj.navitia_trip_id == "OCE:SN840427F03001"
        assert db_trip_partial_react.vj.start_timestamp == datetime(2017, 3, 18, 13, 5)
        assert db_trip_partial_react.vj_id == db_trip_partial_react.vj.id
        assert db_trip_partial_react.status == "update"
        assert db_trip_partial_react.effect == "REDUCED_SERVICE"

        assert len(db_trip_partial_react.stop_time_updates) == 7

        for s in db_trip_partial_react.stop_time_updates[0:3]:
            assert s.arrival_status == "none"
            assert s.departure_status == "none"
            assert s.message is None

        # the stops Chaumont, Bar-sur-Aube, Vendeuvre and Troyes should have message (same as deletion one)
        ch_st = db_trip_partial_react.stop_time_updates[3]
        assert ch_st.stop_id == "stop_point:OCE:SP:TrainTER-87142000"  # Chaumont is fully back
        assert ch_st.arrival_status == "none"
        assert ch_st.departure_status == "none"

        bar_st = db_trip_partial_react.stop_time_updates[4]
        assert bar_st.stop_id == "stop_point:OCE:SP:TrainTER-87118299"  # Bar-sur-Aube
        assert bar_st.arrival_status == "delete"  # only that stop is deleted
        assert bar_st.departure_status == "delete"

        ven_st = db_trip_partial_react.stop_time_updates[5]
        assert ven_st.stop_id == "stop_point:OCE:SP:TrainTER-87118257"  # Vendeuvre is back
        assert ven_st.arrival_status == "none"
        assert ven_st.departure_status == "none"

        tro_st = db_trip_partial_react.stop_time_updates[6]
        assert tro_st.stop_id == "stop_point:OCE:SP:TrainTER-87118000"  # Troyes is back
        assert tro_st.arrival_status == "none"
        assert tro_st.departure_status == "none"

        for s in db_trip_partial_react.stop_time_updates[3:6]:
            assert s.message == "Défaut d'alimentation électrique"

        assert db_trip_partial_react.contributor_id == COTS_CONTRIBUTOR_ID

    # Then all 3 stops are reactivated
    cots_080427_full_react = get_fixture_data("cots_train_840427_full_reactivation.json")
    res = api_post("/cots", data=cots_080427_full_react)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 3
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 7
        assert RealTimeUpdate.query.first().status == "OK"

        db_trip_full_react = TripUpdate.find_by_dated_vj("OCE:SN840427F03001", datetime(2017, 3, 18, 13, 5))
        assert db_trip_full_react

        assert db_trip_full_react.vj.navitia_trip_id == "OCE:SN840427F03001"
        assert db_trip_full_react.vj.start_timestamp == datetime(2017, 3, 18, 13, 5)
        assert db_trip_full_react.vj_id == db_trip_partial_react.vj.id
        assert db_trip_full_react.status == "update"
        assert db_trip_full_react.effect == "UNKNOWN_EFFECT"

        assert len(db_trip_full_react.stop_time_updates) == 7

        for s in db_trip_full_react.stop_time_updates:
            assert s.arrival_status == "none"
            assert s.departure_status == "none"

        for s in db_trip_full_react.stop_time_updates[:3]:
            assert s.message is None

        for s in db_trip_full_react.stop_time_updates[3:]:
            assert s.message == "Défaut d'alimentation électrique"

    # Then all 3 stops are reactivated and the penultimate has a 5 min delay
    cots_080427_full_react = get_fixture_data("cots_train_840427_full_reactivation_delay.json")
    res = api_post("/cots", data=cots_080427_full_react)
    assert "cots feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 4
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 7
        assert RealTimeUpdate.query.first().status == "OK"

        db_trip_full_react = TripUpdate.find_by_dated_vj("OCE:SN840427F03001", datetime(2017, 3, 18, 13, 5))
        assert db_trip_full_react

        assert db_trip_full_react.vj.navitia_trip_id == "OCE:SN840427F03001"
        assert db_trip_full_react.vj.start_timestamp == datetime(2017, 3, 18, 13, 5)
        assert db_trip_full_react.vj_id == db_trip_partial_react.vj.id
        assert db_trip_full_react.status == "update"
        assert db_trip_full_react.effect == "SIGNIFICANT_DELAYS"

        assert len(db_trip_full_react.stop_time_updates) == 7

        for s in db_trip_full_react.stop_time_updates[:5]:
            assert s.arrival_status == "none"
            assert s.departure_status == "none"

        assert db_trip_full_react.stop_time_updates[5].arrival_status == "update"
        assert db_trip_full_react.stop_time_updates[5].arrival_delay == timedelta(minutes=5)
        assert db_trip_full_react.stop_time_updates[5].departure_status == "update"
        assert db_trip_full_react.stop_time_updates[5].departure_delay == timedelta(minutes=5)

        assert db_trip_full_react.stop_time_updates[6].arrival_status == "none"
        assert db_trip_full_react.stop_time_updates[6].departure_status == "none"

        for s in db_trip_full_react.stop_time_updates[:3]:
            assert s.message is None

        for s in db_trip_full_react.stop_time_updates[3:]:
            assert s.message == "Défaut d'alimentation électrique"

    assert mock_rabbitmq.call_count == 4


def test_wrong_planned_stop_time_reference_post():
    """
    Rejected COTS feed, contains wrong planned-stoptime
    """
    cots_file = get_fixture_data("cots_train_96231_delaylist_at_stop_ko.json")
    res, status = api_post("/cots", check=False, data=cots_file)

    assert status == 400
    assert res.get("message") == "invalid arguments"
    assert "error" in res
    assert 'invalid json, impossible to find source "ESCALE" in any json dict of list:' in res.get("error")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 0
        assert len(StopTimeUpdate.query.all()) == 0


def test_cots_added_stop_time():
    """
    A new stop time is added in the VJ 96231
    """
    cots_add_file = get_fixture_data("cots_train_96231_add.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "MODIFIED_SERVICE"
        assert TripUpdate.query.all()[0].company_id == "company:OCE:SN"
        assert TripUpdate.query.all()[0].physical_mode_id is None
        assert TripUpdate.query.all()[0].headsign is None
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[3].arrival_status == "add"
        assert StopTimeUpdate.query.all()[3].arrival == datetime(2015, 9, 21, 16, 2)
        assert StopTimeUpdate.query.all()[3].departure_status == "add"
        assert StopTimeUpdate.query.all()[3].departure == datetime(2015, 9, 21, 16, 9)  # 16:04 + 5 min delay


def test_cots_added_and_deleted_stop_time():
    """
    Aim : Highlighting the delete mechanism.
          Delete is possible only if the stop_time was there before
    """

    # If add wasn't done before, the deletion will not work

    cots_deleted_file = get_fixture_data("cots_train_96231_deleted.json")
    res = api_post("/cots", data=cots_deleted_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 0

    cots_add_file = get_fixture_data("cots_train_96231_add.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "MODIFIED_SERVICE"
        assert TripUpdate.query.all()[0].company_id == "company:OCE:SN"
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[3].arrival_status == "add"
        assert StopTimeUpdate.query.all()[3].arrival == datetime(2015, 9, 21, 16, 2)
        assert StopTimeUpdate.query.all()[3].departure_status == "add"
        assert StopTimeUpdate.query.all()[3].departure == datetime(2015, 9, 21, 16, 9)  # 16:04 + 5 min delay
        created_at_for_add = StopTimeUpdate.query.all()[3].created_at

    # At this point the trip_update is valid. Adding a new Stop_time in data base
    cots_deleted_file = get_fixture_data("cots_train_96231_deleted.json")
    res = api_post("/cots", data=cots_deleted_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 3
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "REDUCED_SERVICE"
        assert TripUpdate.query.all()[0].company_id == "company:OCE:SN"
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[3].arrival_status == "delete"
        assert StopTimeUpdate.query.all()[3].departure_status == "delete"

        created_at_for_delete = StopTimeUpdate.query.all()[3].created_at

    # At this point the trip_update is valid. Stop_time recently added will be deleted.
    assert created_at_for_add != created_at_for_delete

    cots_deleted_file = get_fixture_data("cots_train_96231_deleted.json")
    res = api_post("/cots", data=cots_deleted_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 4
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "REDUCED_SERVICE"
        assert TripUpdate.query.all()[0].company_id == "company:OCE:SN"
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[3].arrival_status == "delete"
        assert StopTimeUpdate.query.all()[3].departure_status == "delete"
        # No change is stored in db (nothing sent to navitia) as the state is the same
        assert StopTimeUpdate.query.all()[3].created_at == created_at_for_delete

    cots_delayed_file = get_fixture_data("cots_train_96231_deleted_and_delayed.json")
    res = api_post("/cots", data=cots_delayed_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 5
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].company_id == "company:OCE:SN"
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[3].arrival_status == "delete"
        assert StopTimeUpdate.query.all()[3].departure_status == "delete"
        # It has already been deleted, but it's allowed to send deleted once again.
        assert StopTimeUpdate.query.all()[3].created_at > created_at_for_delete
        db_trip_delayed = check_db_96231_delayed(contributor_id=COTS_CONTRIBUTOR_ID)
        # when delete and delays exist, effect=REDUCED_SERVICE, because REDUCED_SERVICE > SIGNIFICANT_DELAYS
        assert db_trip_delayed.effect == "REDUCED_SERVICE"
        assert len(db_trip_delayed.stop_time_updates) == 7


def test_cots_added_stop_time_first_position_then_delete_it():
    """
    A new stop time is added in the VJ 96231 in first position
    """
    cots_add_file = get_fixture_data("cots_train_96231_add_first.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "MODIFIED_SERVICE"
        assert TripUpdate.query.all()[0].company_id == "company:OCE:TH"
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[0].arrival_status == "none"
        assert StopTimeUpdate.query.all()[0].departure_status == "add"
        assert StopTimeUpdate.query.all()[0].departure == datetime(2015, 9, 21, 14, 20)
        assert StopTimeUpdate.query.all()[1].arrival_status == "none"
        assert StopTimeUpdate.query.all()[1].departure_status == "none"
        assert StopTimeUpdate.query.all()[1].departure == datetime(2015, 9, 21, 15, 21)
        assert StopTimeUpdate.query.all()[1].arrival == datetime(2015, 9, 21, 15, 21)

    # we remove the added first stop time
    cots_del_file = get_fixture_data("cots_train_96231_delete_previously_added_first_stop.json")
    res = api_post("/cots", data=cots_del_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "REDUCED_SERVICE"
        assert TripUpdate.query.all()[0].company_id == "company:OCE:TH"
        assert TripUpdate.query.all()[0].physical_mode_id is None
        assert TripUpdate.query.all()[0].headsign is None
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[0].arrival_status == "none"
        assert StopTimeUpdate.query.all()[0].departure_status == "delete"
        assert StopTimeUpdate.query.all()[0].departure == datetime(2015, 9, 21, 14, 20)
        # Here is the real departure
        assert StopTimeUpdate.query.all()[1].arrival_status == "none"
        assert StopTimeUpdate.query.all()[1].departure_status == "none"
        assert StopTimeUpdate.query.all()[1].departure == datetime(2015, 9, 21, 15, 21)


def test_cots_added_stop_time_last_position():
    """
    A new stop time is added in the VJ 96231 in last position
    """
    cots_add_file = get_fixture_data("cots_train_96231_add_last.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "MODIFIED_SERVICE"
        assert TripUpdate.query.all()[0].company_id == "company:OCE:SN"
        assert TripUpdate.query.all()[0].physical_mode_id is None
        assert TripUpdate.query.all()[0].headsign is None
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[6].departure_status == "none"
        assert StopTimeUpdate.query.all()[6].arrival_status == "add"
        assert StopTimeUpdate.query.all()[6].departure == datetime(2015, 9, 21, 16, 50)


def test_cots_for_detour():
    """
    A new stop time is added for detour in the VJ 96231 at position 3
    """
    cots_add_file = get_fixture_data("cots_train_96231_added_for_detour.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "DETOUR"
        stop_time_updates = TripUpdate.query.all()[0].stop_time_updates
        assert len(stop_time_updates) == 7
        assert stop_time_updates[2].departure_status == "deleted_for_detour"
        assert stop_time_updates[2].arrival_status == "deleted_for_detour"

        assert stop_time_updates[3].departure_status == "added_for_detour"
        assert stop_time_updates[3].arrival_status == "added_for_detour"
        assert stop_time_updates[3].arrival == datetime(2015, 9, 21, 15, 58)
        assert stop_time_updates[3].departure == datetime(2015, 9, 21, 15, 58)


def test_cots_for_detour_in_advance():
    """
    A new stop time is added for detour in the VJ 96231 at position 3 with arrival and departure
    time earlier than deleted stop_time.
    """
    cots_add_file = get_fixture_data("cots_train_96231_added_for_detour_in_advance.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "DETOUR"
        stop_time_updates = TripUpdate.query.all()[0].stop_time_updates
        assert len(stop_time_updates) == 7
        assert stop_time_updates[2].departure_status == "deleted_for_detour"
        assert stop_time_updates[2].arrival_status == "deleted_for_detour"

        assert stop_time_updates[3].departure_status == "added_for_detour"
        assert stop_time_updates[3].arrival_status == "added_for_detour"
        assert stop_time_updates[3].arrival == datetime(2015, 9, 21, 15, 48)
        assert stop_time_updates[3].departure == datetime(2015, 9, 21, 15, 48)


def test_cots_add_stop_time_without_delay():
    """
    A new stop time is added in the VJ 96231 without delay
    """
    cots_add_file = get_fixture_data("cots_train_96231_add_without_delay.json")

    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert TripUpdate.query.all()[0].status == "update"
        assert TripUpdate.query.all()[0].effect == "MODIFIED_SERVICE"
        assert TripUpdate.query.all()[0].company_id == "company:OCE:TH"
        assert TripUpdate.query.all()[0].physical_mode_id is None
        assert TripUpdate.query.all()[0].headsign is None
        assert len(StopTimeUpdate.query.all()) == 7
        assert StopTimeUpdate.query.all()[3].arrival_status == "add"
        assert StopTimeUpdate.query.all()[3].arrival == datetime(2015, 9, 21, 16, 2)
        assert StopTimeUpdate.query.all()[3].departure_status == "add"
        assert StopTimeUpdate.query.all()[3].departure == datetime(2015, 9, 21, 16, 4)


def test_cots_added_stop_time_earlier_than_previous():
    """
    A new stop time is added in the VJ 96231 whose arrival/departure is earlier
    than the previous one.

    This cots should be rejected
    """
    cots_add_file = get_fixture_data("cots_train_96231_add_stop_time_earlier_than_previous.json")
    res, status = api_post("/cots", data=cots_add_file, check=False)
    assert status == 400
    assert res.get("message") == "invalid arguments"
    with app.app_context():
        assert (
            RealTimeUpdate.query.first().error
            == "invalid cots: stop_point's(0087-713065-BV) time is not consistent"
        )


def check_add_no_delays_96231():
    trips = TripUpdate.query.all()
    assert len(trips) == 1
    assert trips[0].status == "update"
    assert trips[0].effect == "MODIFIED_SERVICE"
    assert trips[0].company_id == "company:OCE:SN"
    assert trips[0].physical_mode_id is None
    assert trips[0].headsign is None
    stus = StopTimeUpdate.query.all()
    assert len(stus) == 7
    assert stus[0].arrival_status == "none"
    assert stus[0].arrival == datetime(2015, 9, 21, 15, 21)
    assert stus[0].departure_status == "none"
    assert stus[0].departure == datetime(2015, 9, 21, 15, 21)
    assert stus[1].arrival_status == "none"
    assert stus[1].arrival == datetime(2015, 9, 21, 15, 38)
    assert stus[1].departure_status == "none"
    assert stus[1].departure == datetime(2015, 9, 21, 15, 40)
    assert stus[2].arrival_status == "none"
    assert stus[2].arrival == datetime(2015, 9, 21, 15, 51)
    assert stus[2].departure_status == "none"
    assert stus[2].departure == datetime(2015, 9, 21, 15, 53)
    assert stus[3].arrival_status == "add"
    assert stus[3].arrival == datetime(2015, 9, 21, 16, 2)
    assert stus[3].departure_status == "add"
    assert stus[3].departure == datetime(2015, 9, 21, 16, 4)
    assert stus[4].arrival_status == "none"
    assert stus[4].arrival == datetime(2015, 9, 21, 16, 14)
    assert stus[4].departure_status == "none"
    assert stus[4].departure == datetime(2015, 9, 21, 16, 16)
    assert stus[5].arrival_status == "none"
    assert stus[5].arrival == datetime(2015, 9, 21, 16, 30)
    assert stus[5].departure_status == "none"
    assert stus[5].departure == datetime(2015, 9, 21, 16, 31)
    assert stus[6].arrival_status == "none"
    assert stus[6].arrival == datetime(2015, 9, 21, 16, 39)
    assert stus[6].departure_status == "none"
    assert stus[6].departure == datetime(2015, 9, 21, 16, 39)


def check_add_with_delays_96231():
    trips = TripUpdate.query.all()
    assert len(trips) == 1
    assert trips[0].status == "update"
    assert trips[0].effect == "MODIFIED_SERVICE"
    assert trips[0].company_id == "company:OCE:SN"
    assert trips[0].physical_mode_id is None
    assert trips[0].headsign is None
    stus = StopTimeUpdate.query.all()
    assert len(stus) == 7
    assert stus[0].arrival_status == "none"
    assert stus[0].arrival == datetime(2015, 9, 21, 15, 21)
    assert stus[0].departure_status == "none"
    assert stus[0].departure == datetime(2015, 9, 21, 15, 21)
    assert stus[1].arrival_status == "none"
    assert stus[1].arrival == datetime(2015, 9, 21, 15, 38)
    assert stus[1].departure_status == "update"
    assert stus[1].departure == datetime(2015, 9, 21, 15, 54)
    assert stus[2].arrival_status == "update"
    assert stus[2].arrival == datetime(2015, 9, 21, 16, 6)
    assert stus[2].departure_status == "update"
    assert stus[2].departure == datetime(2015, 9, 21, 16, 8)
    assert stus[3].arrival_status == "add"
    assert stus[3].arrival == datetime(2015, 9, 21, 16, 17)
    assert stus[3].departure_status == "add"
    assert stus[3].departure == datetime(2015, 9, 21, 16, 19)
    assert stus[4].arrival_status == "update"
    assert stus[4].arrival == datetime(2015, 9, 21, 16, 29)
    assert stus[4].departure_status == "update"
    assert stus[4].departure == datetime(2015, 9, 21, 16, 31)
    assert stus[5].arrival_status == "update"
    assert stus[5].arrival == datetime(2015, 9, 21, 16, 45)
    assert stus[5].departure_status == "update"
    assert stus[5].departure == datetime(2015, 9, 21, 16, 46)
    assert stus[6].arrival_status == "update"
    assert stus[6].arrival == datetime(2015, 9, 21, 16, 54)
    assert stus[6].departure_status == "none"
    assert stus[6].departure == datetime(2015, 9, 21, 16, 54)


def test_cots_add_no_delay():
    """
    A simple add, no delay inside or around
    """
    cots_add_file = get_fixture_data("cots_train_96231_no_delay_add_before_add_with_delay.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_no_delays_96231()


def test_cots_add_stop_time_with_delays_around():
    """
    A new stop time is added in the VJ 96231 with a delay as explained below

    Base:   15:21/15:21     15:38/15:40    15:51/15:53      16:14/16:16     16:30/16:31     16:39/16:39
            -------|------------|-|------------|-|--------------|-|------------|-|-------------|

    W/Delay 15:21/15:21     15:38/15:55         16:06/16:08      16:29/16:31     16:45/16:46     16:54/16:54
            -------|------------|----|--------------|-|---------------|-|-------------|-|--------------|

    Valid add: (16:06/16:08 < new_stop_time < 16:29/16:31)     ----|-----16:17/16:19
    Arrival and departure datetime of newly added stop_time compared to the existing stop_times with delay.
    To test you can modify arrival and departure of added stop_time = (713065) in Flux-cots
    """

    cots_add_file = get_fixture_data(
        "cots_train_96231_add_with_departure_after_next_base_stop_time_arrival.json"
    )
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_with_delays_96231()


def test_cots_add_and_delay_stop_time_with_delays_around():
    """
    Same as above, but the added stop is a base+delay datetime (16:02 + 15 min = 16:17)
    """

    cots_add_file = get_fixture_data(
        "cots_train_96231_add_with_delay_departure_after_next_base_stop_time_arrival.json"
    )
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_with_delays_96231()


def test_chain_add_no_delay_and_add_with_delay_around():
    """
    The death chain:
    * Simple add
    * then add(no delay) with delays around
    * then back to simple add
    * then add+delay with delay around
    * then back to simple add
    """
    cots_add_file = get_fixture_data("cots_train_96231_no_delay_add_before_add_with_delay.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_no_delays_96231()

    cots_add_file = get_fixture_data(
        "cots_train_96231_add_with_departure_after_next_base_stop_time_arrival.json"
    )
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        check_add_with_delays_96231()

    cots_add_file = get_fixture_data("cots_train_96231_no_delay_add_before_add_with_delay.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 3
        check_add_no_delays_96231()

    cots_add_file = get_fixture_data(
        "cots_train_96231_add_with_delay_departure_after_next_base_stop_time_arrival.json"
    )
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 4
        check_add_with_delays_96231()

    cots_add_file = get_fixture_data("cots_train_96231_no_delay_add_before_add_with_delay.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 5
        check_add_no_delays_96231()


def check_add_trip_151515():
    trips = TripUpdate.query.all()
    assert len(trips) == 1
    assert trips[0].status == "add"
    assert trips[0].effect == "ADDITIONAL_SERVICE"
    assert trips[0].company_id == "company:OCE:SN"
    assert trips[0].physical_mode_id == "physical_mode:LongDistanceTrain"
    assert trips[0].headsign == "151515"
    stop_times = StopTimeUpdate.query.all()
    assert len(stop_times) == 5
    assert stop_times[0].arrival_status == "none"
    assert stop_times[0].arrival == datetime(2012, 11, 20, 11, 00)
    assert stop_times[0].departure_status == "add"
    assert stop_times[0].departure == datetime(2012, 11, 20, 11, 00)
    assert stop_times[1].arrival_status == "add"
    assert stop_times[1].arrival == datetime(2012, 11, 20, 12, 00)
    assert stop_times[1].departure_status == "add"
    assert stop_times[1].departure == datetime(2012, 11, 20, 12, 10)
    assert stop_times[2].arrival_status == "add"
    assert stop_times[2].arrival == datetime(2012, 11, 20, 14, 00)
    assert stop_times[2].departure_status == "add"
    assert stop_times[2].departure == datetime(2012, 11, 20, 14, 10)
    assert stop_times[3].arrival_status == "add"
    assert stop_times[3].arrival == datetime(2012, 11, 20, 15, 00)
    assert stop_times[3].departure_status == "add"
    assert stop_times[3].departure == datetime(2012, 11, 20, 15, 10)
    assert stop_times[4].arrival_status == "add"
    assert stop_times[4].arrival == datetime(2012, 11, 20, 16, 00)
    assert stop_times[4].departure_status == "none"
    assert stop_times[4].departure == datetime(2012, 11, 20, 16, 00)


def check_add_trip_151515_with_delay():
    trips = TripUpdate.query.all()
    assert len(trips) == 1
    assert trips[0].status == "add"
    assert trips[0].effect == "ADDITIONAL_SERVICE"
    assert trips[0].company_id == "company:OCE:SN"
    assert trips[0].physical_mode_id == "physical_mode:LongDistanceTrain"
    assert trips[0].headsign == "151515"
    stop_times = StopTimeUpdate.query.all()
    assert len(stop_times) == 5
    assert stop_times[0].arrival_status == "none"
    assert stop_times[0].arrival == datetime(2012, 11, 20, 11, 15)
    assert stop_times[0].departure_status == "add"
    assert stop_times[0].departure == datetime(2012, 11, 20, 11, 15)
    assert stop_times[1].arrival_status == "add"
    assert stop_times[1].arrival == datetime(2012, 11, 20, 12, 15)
    assert stop_times[1].departure_status == "add"
    assert stop_times[1].departure == datetime(2012, 11, 20, 12, 25)
    assert stop_times[2].arrival_status == "add"
    assert stop_times[2].arrival == datetime(2012, 11, 20, 14, 15)
    assert stop_times[2].departure_status == "add"
    assert stop_times[2].departure == datetime(2012, 11, 20, 14, 25)
    assert stop_times[3].arrival_status == "add"
    assert stop_times[3].arrival == datetime(2012, 11, 20, 15, 15)
    assert stop_times[3].departure_status == "add"
    assert stop_times[3].departure == datetime(2012, 11, 20, 15, 25)
    assert stop_times[4].arrival_status == "add"
    assert stop_times[4].arrival == datetime(2012, 11, 20, 16, 15)
    assert stop_times[4].departure_status == "none"
    assert stop_times[4].departure == datetime(2012, 11, 20, 16, 15)


def check_add_trip_151515_with_delay_and_a_delete():
    trips = TripUpdate.query.all()
    assert len(trips) == 1
    assert trips[0].status == "add"
    assert trips[0].effect == "ADDITIONAL_SERVICE"
    assert trips[0].company_id == "company:OCE:SN"
    assert trips[0].physical_mode_id == "physical_mode:LongDistanceTrain"
    assert trips[0].headsign == "151515"
    stop_times = StopTimeUpdate.query.all()
    assert len(stop_times) == 5
    assert stop_times[0].arrival_status == "none"
    assert stop_times[0].arrival == datetime(2012, 11, 20, 11, 15)
    assert stop_times[0].departure_status == "add"
    assert stop_times[0].departure == datetime(2012, 11, 20, 11, 15)
    assert stop_times[1].arrival_status == "delete"
    assert stop_times[1].departure_status == "delete"
    assert stop_times[2].arrival_status == "add"
    assert stop_times[2].arrival == datetime(2012, 11, 20, 14, 15)
    assert stop_times[2].departure_status == "add"
    assert stop_times[2].departure == datetime(2012, 11, 20, 14, 25)
    assert stop_times[3].arrival_status == "add"
    assert stop_times[3].arrival == datetime(2012, 11, 20, 15, 15)
    assert stop_times[3].departure_status == "add"
    assert stop_times[3].departure == datetime(2012, 11, 20, 15, 25)
    assert stop_times[4].arrival_status == "add"
    assert stop_times[4].arrival == datetime(2012, 11, 20, 16, 15)
    assert stop_times[4].departure_status == "none"
    assert stop_times[4].departure == datetime(2012, 11, 20, 16, 15)


def check_add_trip_151515_with_delay_and_an_add():
    trips = TripUpdate.query.all()
    assert len(trips) == 1
    assert trips[0].status == "add"
    assert trips[0].effect == "ADDITIONAL_SERVICE"
    assert trips[0].company_id == "company:OCE:SN"
    assert trips[0].physical_mode_id == "physical_mode:LongDistanceTrain"
    assert trips[0].headsign == "151515"
    stop_time = StopTimeUpdate.query.all()
    assert len(stop_time) == 6
    assert stop_time[0].arrival_status == "none"
    assert stop_time[0].arrival == datetime(2012, 11, 20, 11, 15)
    assert stop_time[0].departure_status == "add"
    assert stop_time[0].departure == datetime(2012, 11, 20, 11, 15)
    assert stop_time[1].arrival_status == "add"
    assert stop_time[1].arrival == datetime(2012, 11, 20, 11, 45)
    assert stop_time[1].departure_status == "add"
    assert stop_time[1].departure == datetime(2012, 11, 20, 11, 55)
    assert stop_time[2].arrival_status == "add"
    assert stop_time[2].arrival == datetime(2012, 11, 20, 12, 15)
    assert stop_time[2].departure_status == "add"
    assert stop_time[2].departure == datetime(2012, 11, 20, 12, 25)
    assert stop_time[3].arrival_status == "add"
    assert stop_time[3].arrival == datetime(2012, 11, 20, 14, 15)
    assert stop_time[3].departure_status == "add"
    assert stop_time[3].departure == datetime(2012, 11, 20, 14, 25)
    assert stop_time[4].arrival_status == "add"
    assert stop_time[4].arrival == datetime(2012, 11, 20, 15, 15)
    assert stop_time[4].departure_status == "add"
    assert stop_time[4].departure == datetime(2012, 11, 20, 15, 25)
    assert stop_time[5].arrival_status == "add"
    assert stop_time[5].arrival == datetime(2012, 11, 20, 16, 15)
    assert stop_time[5].departure_status == "none"
    assert stop_time[5].departure == datetime(2012, 11, 20, 16, 15)


def test_cots_for_added_trip_chain_type_1():
    """
    Test for case 11 from the file "Enchainement Cas_API_20181010.xlsx"
    1. A simple trip add with 5 stop_times all existing in navitia
    2. Trip modified with 15 minutes delay in each stop_times
    3. Return to normal
    """
    cots_add_file = get_fixture_data("cots_train_151515_added_trip.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_trip_151515()

    cots_add_file = get_fixture_data("cots_train_151515_added_trip_with_delay.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        check_add_trip_151515_with_delay()

    cots_add_file = get_fixture_data("cots_train_151515_added_trip_to_normal.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 3
        check_add_trip_151515()


def test_cots_for_added_trip_chain_type_2():
    """
    Test for case 12 from the file "Enchainement Cas_API_20181010.xlsx"
    1. A simple trip add with 5 stop_times all existing in navitia
    2. Trip modified with 15 minutes delay in each stop_times
    3. Trip modified with a stop_time deleted in the above flux cots
    4. Return to normal
    """
    cots_add_file = get_fixture_data("cots_train_151515_added_trip.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_trip_151515()

    cots_add_file = get_fixture_data("cots_train_151515_added_trip_with_delay.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        check_add_trip_151515_with_delay()

    cots_add_file = get_fixture_data("cots_train_151515_added_trip_with_delay_and_stop_time_deleted.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 3
        check_add_trip_151515_with_delay_and_a_delete()

    cots_add_file = get_fixture_data("cots_train_151515_added_trip_to_normal.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 4
        check_add_trip_151515()


def test_cots_for_added_trip_chain_type_3():
    """
    Test for case 2 from the file "Enchainement Cas_API_20181010.xlsx"
    1. A simple trip add with 5 stop_times all existing in navitia
    2. Trip modified with 15 minutes delay in each stop_times
    3. Trip modified with a new stop_time added in the above flux cots
    4. Delete the trip with "statutCirculationOPE": "SUPPRESSION" in all stop_times
    """
    cots_add_file = get_fixture_data("cots_train_151515_added_trip.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_trip_151515()

    cots_add_file = get_fixture_data("cots_train_151515_added_trip_with_delay.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        check_add_trip_151515_with_delay()

    cots_add_file = get_fixture_data("cots_train_151515_added_trip_with_delay_and_stop_time_added.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 3
        check_add_trip_151515_with_delay_and_an_add()

    cots_add_file = get_fixture_data("cots_train_151515_deleted_trip_with_delay_and_stop_time_added.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 4
        trips = TripUpdate.query.all()
        assert len(trips) == 1
        assert trips[0].status == "delete"
        assert trips[0].effect == "NO_SERVICE"
        assert trips[0].company_id == "company:OCE:SN"
        stop_times = StopTimeUpdate.query.all()
        assert len(stop_times) == 0


def test_cots_add_same_trip_more_than_once():
    """
    1. A simple trip add with 5 stop_times all existing in navitia
    2. add the same trip as above
    """
    cots_add_file = get_fixture_data("cots_train_151515_added_trip.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_trip_151515()

    cots_add_file = get_fixture_data("cots_train_151515_added_trip.json")
    res, status = api_post("/cots", check=False, data=cots_add_file)
    assert status == 400
    assert "Invalid action, trip 151515 can not be added multiple times" in res.get("error")


def test_cots_delete_added_trip_more_than_once():
    """
    1. A simple trip add with 5 stop_times all existing in navitia
    2. delete the trip recently added
    3. re-delete the trip recently added and then deleted
    4. Adding back the same trip after the delete
    """
    cots_add_file = get_fixture_data("cots_train_151515_added_trip.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        check_add_trip_151515()

    cots_delete_file = get_fixture_data("cots_train_151515_deleted_trip_with_delay_and_stop_time_added.json")
    res = api_post("/cots", data=cots_delete_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 2
        trips = TripUpdate.query.all()
        assert len(trips) == 1
        assert trips[0].status == "delete"
        assert trips[0].effect == "NO_SERVICE"
        assert trips[0].company_id == "company:OCE:SN"
        stop_times = StopTimeUpdate.query.all()
        assert len(stop_times) == 0

    cots_delete_file = get_fixture_data("cots_train_151515_deleted_trip_with_delay_and_stop_time_added.json")
    res, status = api_post("/cots", check=False, data=cots_delete_file)
    assert status == 400
    assert "Invalid action, trip 151515 already deleted in database" in res.get("error")

    cots_add_file = get_fixture_data("cots_train_151515_added_trip.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 4
        check_add_trip_151515()


def test_cots_add_trip_in_coach():
    """
    1. A simple trip add with 5 stop_times all existing in navitia with "indicateurFer": "ROUTIER"
    """
    cots_add_file = get_fixture_data("cots_train_151515_added_trip_in_coach.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        trips = TripUpdate.query.all()
        assert len(trips) == 1
        assert trips[0].status == "add"
        assert trips[0].effect == "ADDITIONAL_SERVICE"
        assert trips[0].company_id == "company:OCE:SN"
        assert trips[0].physical_mode_id == "physical_mode:Coach"
        assert trips[0].headsign == "151515"
        stop_times = StopTimeUpdate.query.all()
        assert len(stop_times) == 5


def test_cots_add_trip_with_unknown_mode():
    """
    1. A simple trip add with 5 stop_times all existing in navitia with "indicateurFer": "TOTO"
    kirin uses physical_mode:LongDistanceTrain as default physical_mode if it exists in kraken
    """
    cots_add_file = get_fixture_data("cots_train_151515_added_trip_with_unknown_mode.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        trips = TripUpdate.query.all()
        assert len(trips) == 1
        assert trips[0].status == "add"
        assert trips[0].effect == "ADDITIONAL_SERVICE"
        assert trips[0].company_id == "company:OCE:SN"
        assert trips[0].physical_mode_id == "physical_mode:LongDistanceTrain"
        assert trips[0].headsign == "151515"
        stop_times = StopTimeUpdate.query.all()
        assert len(stop_times) == 5


def test_cots_add_trip_existing_in_navitia():
    """
    A simple trip add existing in navitia
    Kirin verifies the presence of the vehicle_journey for a trip to be added
    and rejects the trip if present.
    """
    cots_add_file = get_fixture_data("cots_train_6113_add_trip_present_in_navitia.json")
    res, status = api_post("/cots", check=False, data=cots_add_file)
    assert status == 400
    assert res.get("message") == "invalid arguments"
    assert "error" in res
    assert "Invalid action, trip 6113 already present in navitia" in res.get("error")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 0
        assert len(StopTimeUpdate.query.all()) == 0


def test_cots_on_add_trip_without_first_cots():
    """
    Test that trip update on a newly added trip without first flux cots is rejected with a message
    """
    cots_add_file = get_fixture_data("cots_train_151515_added_trip_with_delay.json")
    res = api_post("/cots", data=cots_add_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert RealTimeUpdate.query.first().status == "KO"
        assert RealTimeUpdate.query.first().error == "No new information destined to navitia for this cots"
        trips = TripUpdate.query.all()
        assert len(trips) == 0


def test_cots_update_trip_with_delay_pass_midnight_on_first_station():
    """
    Disruption on a base-schedule VJ that was past-midnight.
    The disruption changes the first station and delays it so that the realtime VJ is not past-midnight anymore.
    Testing that the realtime VJ is circulating on the right day (so one day after the base-schedule VJ).
    """
    cots_update_file = get_fixture_data("cots_train_8837_pass_midnight_at_first_station.json")
    res = api_post("/cots", data=cots_update_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        trips = TripUpdate.query.all()
        assert len(trips) == 1
        assert trips[0].status == "update"
        assert trips[0].effect == "DETOUR"
        stop_times = StopTimeUpdate.query.all()
        assert len(stop_times) == 4
        # First station is deleted for detour
        assert stop_times[0].arrival_status == "none"
        assert stop_times[0].arrival == datetime(2012, 11, 19, 23, 45)
        assert stop_times[0].departure_status == "deleted_for_detour"
        assert stop_times[0].departure == datetime(2012, 11, 19, 23, 45)
        # Second station is added for detour with a delay of 30 minutes pass midnight
        # towards next day
        assert stop_times[1].arrival_status == "added_for_detour"
        assert stop_times[1].arrival == datetime(2012, 11, 20, 0, 15)
        assert stop_times[1].departure_status == "added_for_detour"
        assert stop_times[1].departure == datetime(2012, 11, 20, 0, 15)
        # Not any change on the other two stations
        assert stop_times[2].arrival_status == "none"
        assert stop_times[2].arrival == datetime(2012, 11, 20, 0, 34)
        assert stop_times[2].departure_status == "none"
        assert stop_times[2].departure == datetime(2012, 11, 20, 0, 35)
        assert stop_times[3].arrival_status == "none"
        assert stop_times[3].arrival == datetime(2012, 11, 20, 1, 15)
        assert stop_times[3].departure_status == "none"
        assert stop_times[3].departure == datetime(2012, 11, 20, 1, 15)


def test_cots_add_first_stop_with_advance_pass_midnight():
    """
    Relates to test_delay_pass_midnight_towards_previous_day in jormungandr
    Vehicle_journey with first stop_time at 14:01 and last stop_time at 21:46 for 20121120
    Disruption on a base-schedule VJ without pass-midnight.
    The disruption adds a previous stop before existing stops at 21:30 the day before so that the realtime VJ is
    a past-midnight the day before (20121119).
    Test that the realtime VJ is circulating on the right day: 20121119 (so one day before the base-schedule VJ).
    TODO: A current issue, that should be fixed is a day is added on the result. In our case, it shift the VJ from
    20121119-20121120 to 20121120-20121121.
    """
    cots_update_file = get_fixture_data("cots_train_9580_pass_midnight_in_advance_on_first_station.json")
    res = api_post("/cots", data=cots_update_file)
    assert "cots feed processed" in res.get("message")
    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        trips = TripUpdate.query.all()
        assert len(trips) == 1
        assert trips[0].effect == "MODIFIED_SERVICE"
        stop_times = StopTimeUpdate.query.all()
        assert len(stop_times) == 14

        # The first station should be served at 20121119T2130 (pass midnight in advance 20121120)
        assert stop_times[0].arrival_status == "none"
        assert stop_times[0].departure_status == "add"
        assert stop_times[0].arrival == datetime(2012, 11, 20, 21, 30)
        assert stop_times[0].departure == datetime(2012, 11, 20, 21, 30)

        # From the second station, we should have a pass midnight towards the next day (20121121)
        assert stop_times[1].arrival_status == "none"
        assert stop_times[1].departure_status == "none"
        assert stop_times[1].arrival == datetime(2012, 11, 21, 13, 1)
        assert stop_times[1].departure == datetime(2012, 11, 21, 13, 1)
        assert stop_times[-1].arrival_status == "none"
        assert stop_times[-1].departure_status == "none"
        assert stop_times[-1].arrival == datetime(2012, 11, 21, 20, 46)
        assert stop_times[-1].departure == datetime(2012, 11, 21, 20, 46)


def test_cots_purge_trip_and_rt(mock_rabbitmq):
    """
    Simple COTS post, then test the purge
    """
    cots_file = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_file)
    assert res == "OK"

    with app.app_context():
        # Check there's really something before purge
        assert RealTimeUpdate.query.count() == 1

        config = {
            "contributor": COTS_CONTRIBUTOR_ID,
            "nb_days_to_keep": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
        }
        tu = VehicleJourney.query.first()
        tu.start_timestamp = date.today() - timedelta(days=DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE + 1)
        purge_trip_update(config)

        assert TripUpdate.query.count() == 0
        assert VehicleJourney.query.count() == 0
        assert StopTimeUpdate.query.count() == 0
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 0
        assert RealTimeUpdate.query.count() == 1

        config["nb_days_to_keep"] = DEFAULT_DAYS_TO_KEEP_RT_UPDATE
        # Put an old (realistic) date to RealTimeUpdate object so that RTU purge affects it
        rtu = RealTimeUpdate.query.first()
        rtu.created_at = date.today() - timedelta(days=DEFAULT_DAYS_TO_KEEP_RT_UPDATE + 1)
        purge_rt_update(config)

        assert TripUpdate.query.count() == 0
        assert VehicleJourney.query.count() == 0
        assert StopTimeUpdate.query.count() == 0
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 0
        assert RealTimeUpdate.query.count() == 0


def test_cots_purge_trip_only(mock_rabbitmq):
    cots_file = get_fixture_data("cots_train_96231_normal.json")
    res = api_post("/cots", data=cots_file)
    assert res == "OK"

    with app.app_context():
        # Check there's really something before purge
        assert RealTimeUpdate.query.count() == 1

        config = {
            "contributor": COTS_CONTRIBUTOR_ID,
            "nb_days_to_keep": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
        }

        tu = VehicleJourney.query.first()
        tu.start_timestamp = date.today() - timedelta(days=DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE + 1)
        purge_trip_update(config)

        assert TripUpdate.query.count() == 0
        assert VehicleJourney.query.count() == 0
        assert StopTimeUpdate.query.count() == 0
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 0
        assert RealTimeUpdate.query.count() == 1

        config["nb_days_to_keep"] = DEFAULT_DAYS_TO_KEEP_RT_UPDATE

        # Put a realistic date to RealTimeUpdate object, but just outside the limit where it should be purged
        rtu = RealTimeUpdate.query.first()
        rtu.created_at = date.today() - timedelta(days=DEFAULT_DAYS_TO_KEEP_RT_UPDATE)
        purge_rt_update(config)

        assert TripUpdate.query.count() == 0
        assert VehicleJourney.query.count() == 0
        assert StopTimeUpdate.query.count() == 0
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 0
        assert RealTimeUpdate.query.count() == 1


def test_no_purge_different_contributor(mock_rabbitmq):
    """
    Simple COTS post, then test the purge
    """
    cots_file = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_file)
    assert res == "OK"

    with app.app_context():
        # Check there's really something before purge
        assert RealTimeUpdate.query.count() == 1

        config = {
            "contributor": COTS_CONTRIBUTOR_DB_ID,
            "nb_days_to_keep": 0,
        }

        date_all_purge = date.today() - timedelta(days=DEFAULT_DAYS_TO_KEEP_RT_UPDATE + 1)
        tu = VehicleJourney.query.first()
        tu.start_timestamp = date_all_purge
        purge_trip_update(config)

        rtu = RealTimeUpdate.query.first()
        rtu.created_at = date_all_purge
        purge_rt_update(config)

        assert TripUpdate.query.count() == 1
        assert VehicleJourney.query.count() == 1
        assert StopTimeUpdate.query.count() == 6
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 1
        assert RealTimeUpdate.query.count() == 1


def test_cots_no_purge(mock_rabbitmq):
    """
    Simple COTS post, then test the purge
    """
    cots_file = get_fixture_data("cots_train_96231_delayed.json")
    res = api_post("/cots", data=cots_file)
    assert res == "OK"

    with app.app_context():
        # Check there's really something before purge
        assert RealTimeUpdate.query.count() == 1

        config = {
            "contributor": COTS_CONTRIBUTOR_ID,
            "nb_days_to_keep": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
        }

        tu = VehicleJourney.query.first()
        tu.start_timestamp = date.today() - timedelta(days=DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE)
        purge_trip_update(config)

        assert TripUpdate.query.count() == 1
        assert VehicleJourney.query.count() == 1
        assert StopTimeUpdate.query.count() == 6
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 1
        assert RealTimeUpdate.query.count() == 1

        config["²"] = DEFAULT_DAYS_TO_KEEP_RT_UPDATE

        rtu = RealTimeUpdate.query.first()
        rtu.created_at = date.today() - timedelta(days=DEFAULT_DAYS_TO_KEEP_RT_UPDATE)
        purge_rt_update(config)

        assert TripUpdate.query.count() == 1
        assert VehicleJourney.query.count() == 1
        assert StopTimeUpdate.query.count() == 6
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 1
        assert RealTimeUpdate.query.count() == 1
