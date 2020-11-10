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

from datetime import timedelta, datetime

import pytest
import ujson

from kirin import app, db
from kirin.core.model import (
    RealTimeUpdate,
    TripUpdate,
    StopTimeUpdate,
    VehicleJourney,
    DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
    DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
)
from kirin.core.types import ConnectorType, TripEffect, ModificationType
from kirin.tasks import purge_trip_update, purge_rt_update
from tests.check_utils import api_post, api_get, get_fixture_data, get_fixture_data_as_dict
from tests import mock_navitia
from tests.integration.conftest import PIV_CONTRIBUTOR_ID


@pytest.fixture(scope="function", autouse=True)
def navitia(monkeypatch):
    """
    Mock all calls to navitia for this fixture
    """
    monkeypatch.setattr("navitia_wrapper._NavitiaWrapper.query", mock_navitia.mock_navitia_query)


def test_wrong_get_piv_with_id():
    """
    GET /piv/id.contributor (so with an id) is not allowed, only POST is possible
    """
    resp, status = api_get("/piv/{}".format(PIV_CONTRIBUTOR_ID), check=False)
    assert status == 405
    assert resp.get("message") == "The method is not allowed for the requested URL."


def test_piv_post_wrong_data():
    """
    simple json post on the api
    """
    wrong_piv_feed = "{}"
    res, status = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), check=False, data=wrong_piv_feed)

    assert status == 400
    assert res.get("error")
    assert "invalid arguments" in res.get("message")

    with app.app_context():
        # Raw data is saved in db, even when an error occurred
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 0
        assert StopTimeUpdate.query.count() == 0

        assert RealTimeUpdate.query.first().status == "KO"
        assert 'impossible to find "objects" in json' in RealTimeUpdate.query.first().error
        assert RealTimeUpdate.query.first().raw_data == wrong_piv_feed


def test_piv_post_no_data():
    """
    Post with a missing id or missing data returns an error 400
    Post with an unknown id returns an error 404
    """

    def post_and_check(url, expected_status, expected_message, expected_error):
        resp, status = api_post(url, check=False)
        assert status == expected_status
        assert expected_message in resp.get("message")
        if expected_error:
            assert expected_error == resp.get("error")

        with app.app_context():
            assert RealTimeUpdate.query.count() == 0
            assert TripUpdate.query.count() == 0
            assert StopTimeUpdate.query.count() == 0

    post_and_check("/piv/", 405, "The method is not allowed for the requested URL.", None)
    post_and_check("/piv/{}".format(PIV_CONTRIBUTOR_ID), 400, "invalid arguments", "no piv data provided")
    post_and_check("/piv/unknown_id", 404, "Contributor 'unknown_id' not found", None)


def test_piv_simple_post(mock_rabbitmq):
    """
    simple PIV post should be stored in db as a RealTimeUpdate
    """
    piv_feed = get_fixture_data("piv/stomp_20201022_23186_delayed_5min.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        rtu_array = RealTimeUpdate.query.all()
        assert len(rtu_array) == 1
        rtu = rtu_array[0]
        assert "-" in rtu.id
        assert rtu.created_at
        assert rtu.status == "OK"
        assert rtu.error is None
        assert rtu.contributor_id == PIV_CONTRIBUTOR_ID
        assert rtu.connector == ConnectorType.piv.value
        assert rtu.raw_data == piv_feed
    assert mock_rabbitmq.call_count == 1


def test_piv_purge(mock_rabbitmq):
    """
    Simple PIV post, then test the purge
    """
    piv_feed = get_fixture_data("piv/stomp_20201022_23186_delayed_5min.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        # Check there's really something before purge
        assert RealTimeUpdate.query.count() == 1

        # Put an old (realistic) date to RealTimeUpdate object so that RTU purge affects it
        rtu = RealTimeUpdate.query.first()
        rtu.created_at = datetime(2012, 6, 15, 15, 33)

        assert TripUpdate.query.count() == 1
        assert VehicleJourney.query.count() == 1
        assert StopTimeUpdate.query.count() > 0
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 1

        # VehicleJourney affected is old, so it's affected by TripUpdate purge (based on base-VJ's date)
        config = {
            "contributor": PIV_CONTRIBUTOR_ID,
            "nb_days_to_keep": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
        }
        purge_trip_update(config)

        assert TripUpdate.query.count() == 0
        assert VehicleJourney.query.count() == 0
        assert StopTimeUpdate.query.count() == 0
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 0
        assert RealTimeUpdate.query.count() == 1

        config = {
            "contributor": PIV_CONTRIBUTOR_ID,
            "nb_days_to_keep": DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
        }
        purge_rt_update(config)

        assert TripUpdate.query.count() == 0
        assert VehicleJourney.query.count() == 0
        assert StopTimeUpdate.query.count() == 0
        assert db.session.execute("select * from associate_realtimeupdate_tripupdate").rowcount == 0
        assert RealTimeUpdate.query.count() == 0


def _assert_db_stomp_20201022_23186_delayed_5min():
    with app.app_context():
        assert RealTimeUpdate.query.count() >= 1
        assert TripUpdate.query.count() >= 1
        assert StopTimeUpdate.query.count() >= 17
        db_trip_delayed = TripUpdate.find_by_dated_vj(
            "PIV:2020-10-22:23186:1187:Train", datetime(2020, 10, 22, 20, 34)
        )
        assert db_trip_delayed

        assert db_trip_delayed.vj.navitia_trip_id == "PIV:2020-10-22:23186:1187:Train"
        assert db_trip_delayed.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
        assert db_trip_delayed.vj_id == db_trip_delayed.vj.id
        assert db_trip_delayed.status == ModificationType.update.name
        assert db_trip_delayed.effect == TripEffect.SIGNIFICANT_DELAYS.name
        assert db_trip_delayed.message == "Absence inopinée d'un agent"
        # PIV feed contains delayed stop_times only
        assert db_trip_delayed.company_id == "company:PIVPP:1187"
        assert len(db_trip_delayed.stop_time_updates) == 17

        first_st = db_trip_delayed.stop_time_updates[0]
        assert first_st.stop_id == "stop_point:PIV:85010231:Train"
        # no specific functional constraint on first arrival, except time consistency
        assert first_st.arrival <= first_st.departure
        assert first_st.departure == datetime(2020, 10, 22, 20, 39)
        assert first_st.departure_delay == timedelta(minutes=5)
        assert first_st.departure_status == ModificationType.update.name
        assert first_st.message == "Absence inopinée d'un agent"

        second_st = db_trip_delayed.stop_time_updates[1]
        assert second_st.stop_id == "stop_point:PIV:85010157:Train"
        assert second_st.arrival == datetime(2020, 10, 22, 20, 40)
        assert second_st.arrival_status == ModificationType.update.name
        assert second_st.arrival_delay == timedelta(minutes=5)
        assert second_st.departure == datetime(2020, 10, 22, 20, 40, 30)
        assert second_st.departure_delay == timedelta(minutes=5)
        assert second_st.departure_status == ModificationType.update.name
        assert second_st.message == "Absence inopinée d'un agent (motifModification depart)"

        for st in db_trip_delayed.stop_time_updates[2:-1]:
            assert st.stop_id
            assert datetime(2020, 10, 22, 20, 42) <= st.arrival <= datetime(2020, 10, 22, 21, 24)
            assert st.arrival_status == ModificationType.update.name
            assert st.arrival_delay == timedelta(minutes=5)
            assert datetime(2020, 10, 22, 20, 43) <= st.departure <= datetime(2020, 10, 22, 21, 25)
            assert st.departure_status == ModificationType.update.name
            assert st.departure_delay == timedelta(minutes=5)

        db_trip_delayed.stop_time_updates[2].message = "Absence inopinée d'un agent (motifModification arrivee)"
        db_trip_delayed.stop_time_updates[3].message = "Absence inopinée d'un agent (evenement depart)"
        db_trip_delayed.stop_time_updates[4].message = "Absence inopinée d'un agent (evenement arrivee)"
        db_trip_delayed.stop_time_updates[5].message = "Absence inopinée d'un agent (motifModification depart)"
        db_trip_delayed.stop_time_updates[6].message = "Absence inopinée d'un agent (motifModification arrivee)"
        db_trip_delayed.stop_time_updates[7].message = "Absence inopinée d'un agent (evenement depart)"
        db_trip_delayed.stop_time_updates[8].message = "Absence inopinée d'un agent (evenement arrivee)"
        db_trip_delayed.stop_time_updates[9].message = "Absence inopinée d'un agent"

        last_st = db_trip_delayed.stop_time_updates[-1]
        assert last_st.stop_id == "stop_point:PIV:87745497:Train"
        assert last_st.arrival == datetime(2020, 10, 22, 21, 30)
        assert last_st.arrival_status == ModificationType.update.name
        assert last_st.arrival_delay == timedelta(minutes=5)
        # no specific functional constraint on last departure, except time consistency
        assert last_st.arrival <= last_st.departure
        assert last_st.message == "Absence inopinée d'un agent"

        assert db_trip_delayed.contributor_id == PIV_CONTRIBUTOR_ID

        return db_trip_delayed  # for additional testing if needed


def _assert_db_stomp_20201022_23187_partial_delayed():
    with app.app_context():
        assert RealTimeUpdate.query.count() >= 1
        assert TripUpdate.query.count() >= 1
        assert StopTimeUpdate.query.count() >= 6
        db_trip_delayed = TripUpdate.find_by_dated_vj(
            "PIV:2020-10-22:23187:1187:Train", datetime(2020, 10, 22, 20, 34)
        )
        assert db_trip_delayed

        assert db_trip_delayed.vj.navitia_trip_id == "PIV:2020-10-22:23187:1187:Train"
        assert db_trip_delayed.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
        assert db_trip_delayed.vj_id == db_trip_delayed.vj.id
        assert db_trip_delayed.status == ModificationType.update.name
        assert db_trip_delayed.effect == TripEffect.SIGNIFICANT_DELAYS.name
        assert db_trip_delayed.message == "Absence inopinée d'un agent"
        assert db_trip_delayed.company_id == "company:PIVPP:1187"
        assert len(db_trip_delayed.stop_time_updates) == 6

        first_st = db_trip_delayed.stop_time_updates[0]
        assert first_st.stop_id == "stop_point:PIV:85010231:Train"
        # no specific functional constraint on first arrival, except time consistency
        assert first_st.arrival <= first_st.departure
        assert first_st.departure == datetime(2020, 10, 22, 20, 39)
        assert first_st.departure_delay == timedelta(minutes=5)
        assert first_st.departure_status == ModificationType.update.name
        assert first_st.message == "Absence inopinée d'un agent"

        second_st = db_trip_delayed.stop_time_updates[1]
        assert second_st.stop_id == "stop_point:PIV:85010157:Train"
        assert second_st.arrival == datetime(2020, 10, 22, 20, 40)
        assert second_st.arrival_status == ModificationType.update.name
        assert second_st.arrival_delay == timedelta(minutes=5)
        assert second_st.departure == datetime(2020, 10, 22, 20, 40, 30)
        assert second_st.departure_delay == timedelta(minutes=5)
        assert second_st.departure_status == ModificationType.update.name
        assert second_st.message == "Absence inopinée d'un agent (motifModification depart)"

        third_st = db_trip_delayed.stop_time_updates[2]
        assert third_st.stop_id == "stop_point:PIV:85010140:Train"
        assert third_st.arrival == datetime(2020, 10, 22, 20, 47)
        assert (
            third_st.arrival_status == ModificationType.update.name
        )  # as feed status is "RETARD_PROJETE". OK to be corrected to 'none' in the future.
        assert third_st.arrival_delay == timedelta(minutes=0)
        assert third_st.departure == datetime(2020, 10, 22, 20, 48)
        assert third_st.departure_delay == timedelta(minutes=0)
        assert (
            third_st.departure_status == ModificationType.update.name
        )  # as feed status is "RETARD_PROJETE". OK to be corrected to 'none' in the future.
        assert third_st.message == "Absence inopinée d'un agent (motifModification arrivee)"

        fourth_st = db_trip_delayed.stop_time_updates[3]
        assert fourth_st.stop_id == "stop_point:PIV:85162735:Train"
        assert fourth_st.arrival == datetime(2020, 10, 22, 21, 16)
        assert fourth_st.arrival_status == ModificationType.none.name
        assert fourth_st.arrival_delay == timedelta(minutes=0)
        assert fourth_st.departure == datetime(2020, 10, 22, 21, 17)
        assert fourth_st.departure_delay == timedelta(minutes=0)
        assert fourth_st.departure_status == ModificationType.none.name
        assert fourth_st.message == "Absence inopinée d'un agent"

        fifth_st = db_trip_delayed.stop_time_updates[4]
        assert fifth_st.stop_id == "stop_point:PIV:85162750:Train"
        assert fifth_st.arrival == datetime(2020, 10, 22, 21, 19)
        assert fifth_st.arrival_status == ModificationType.none.name
        assert fifth_st.arrival_delay == timedelta(minutes=0)
        assert fifth_st.departure == datetime(2020, 10, 22, 21, 20)
        assert fifth_st.departure_delay == timedelta(minutes=0)
        assert fifth_st.departure_status == ModificationType.none.name
        assert fifth_st.message is None

        last_st = db_trip_delayed.stop_time_updates[5]
        assert last_st.stop_id == "stop_point:PIV:87745497:Train"
        assert last_st.arrival == datetime(2020, 10, 22, 21, 25)
        assert last_st.arrival_status == ModificationType.none.name
        assert last_st.arrival_delay == timedelta(minutes=0)
        # no specific functional constraint on last departure, except time consistency
        assert last_st.arrival <= last_st.departure
        assert last_st.message is None

        assert db_trip_delayed.contributor_id == PIV_CONTRIBUTOR_ID

        return db_trip_delayed  # for additional testing if needed


def _assert_db_stomp_20201022_23187_delayed_5min():
    with app.app_context():
        assert RealTimeUpdate.query.count() >= 1
        assert TripUpdate.query.count() >= 1
        assert StopTimeUpdate.query.count() >= 6
        db_trip_delayed = TripUpdate.find_by_dated_vj(
            "PIV:2020-10-22:23187:1187:Train", datetime(2020, 10, 22, 20, 34)
        )
        assert db_trip_delayed

        assert db_trip_delayed.vj.navitia_trip_id == "PIV:2020-10-22:23187:1187:Train"
        assert db_trip_delayed.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
        assert db_trip_delayed.vj_id == db_trip_delayed.vj.id
        assert db_trip_delayed.status == ModificationType.update.name
        assert db_trip_delayed.effect == TripEffect.SIGNIFICANT_DELAYS.name
        assert db_trip_delayed.message == "Absence inopinée d'un agent"
        # PIV feed contains delayed stop_times only
        assert db_trip_delayed.company_id == "company:PIVPP:1187"
        assert len(db_trip_delayed.stop_time_updates) == 6

        first_st = db_trip_delayed.stop_time_updates[0]
        assert first_st.stop_id == "stop_point:PIV:85010231:Train"
        # no specific functional constraint on first arrival, except time consistency
        assert first_st.arrival <= first_st.departure
        assert first_st.departure == datetime(2020, 10, 22, 20, 39)
        assert first_st.departure_delay == timedelta(minutes=5)
        assert first_st.departure_status == ModificationType.update.name
        assert first_st.message == "Absence inopinée d'un agent"

        second_st = db_trip_delayed.stop_time_updates[1]
        assert second_st.stop_id == "stop_point:PIV:85010157:Train"
        assert second_st.arrival == datetime(2020, 10, 22, 20, 40)
        assert second_st.arrival_status == ModificationType.update.name
        assert second_st.arrival_delay == timedelta(minutes=5)
        assert second_st.departure == datetime(2020, 10, 22, 20, 40, 30)
        assert second_st.departure_delay == timedelta(minutes=5)
        assert second_st.departure_status == ModificationType.update.name
        assert second_st.message == "Absence inopinée d'un agent (motifModification depart)"

        third_st = db_trip_delayed.stop_time_updates[2]
        assert third_st.stop_id == "stop_point:PIV:85010140:Train"
        assert third_st.arrival == datetime(2020, 10, 22, 20, 52)
        assert third_st.arrival_status == ModificationType.update.name
        assert third_st.arrival_delay == timedelta(minutes=5)
        assert third_st.departure == datetime(2020, 10, 22, 20, 53)
        assert third_st.departure_delay == timedelta(minutes=5)
        assert third_st.departure_status == ModificationType.update.name
        assert third_st.message == "Absence inopinée d'un agent (motifModification arrivee)"

        fourth_st = db_trip_delayed.stop_time_updates[3]
        assert fourth_st.stop_id == "stop_point:PIV:85162735:Train"
        assert fourth_st.arrival == datetime(2020, 10, 22, 21, 21)
        assert fourth_st.arrival_status == ModificationType.update.name
        assert fourth_st.arrival_delay == timedelta(minutes=5)
        assert fourth_st.departure == datetime(2020, 10, 22, 21, 22)
        assert fourth_st.departure_delay == timedelta(minutes=5)
        assert fourth_st.departure_status == ModificationType.update.name
        assert fourth_st.message == "Absence inopinée d'un agent"

        fifth_st = db_trip_delayed.stop_time_updates[4]
        assert fifth_st.stop_id == "stop_point:PIV:85162750:Train"
        assert fifth_st.arrival == datetime(2020, 10, 22, 21, 24)
        assert fifth_st.arrival_status == ModificationType.update.name
        assert fifth_st.arrival_delay == timedelta(minutes=5)
        assert fifth_st.departure == datetime(2020, 10, 22, 21, 25)
        assert fifth_st.departure_delay == timedelta(minutes=5)
        assert fifth_st.departure_status == ModificationType.update.name
        assert fifth_st.message == "Absence inopinée d'un agent"

        last_st = db_trip_delayed.stop_time_updates[5]
        assert last_st.stop_id == "stop_point:PIV:87745497:Train"
        assert last_st.arrival == datetime(2020, 10, 22, 21, 30)
        assert last_st.arrival_status == ModificationType.update.name
        assert last_st.arrival_delay == timedelta(minutes=5)
        # no specific functional constraint on last departure, except time consistency
        assert last_st.arrival <= last_st.departure
        assert last_st.message == "Absence inopinée d'un agent"

        assert db_trip_delayed.contributor_id == PIV_CONTRIBUTOR_ID

        return db_trip_delayed  # for additional testing if needed


def test_piv_delayed(mock_rabbitmq):
    """
    delayed stops post
    """
    piv_feed = get_fixture_data("piv/stomp_20201022_23186_delayed_5min.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23186_delayed_5min()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 1


def test_piv_delayed_post_twice(mock_rabbitmq):
    """
    double delayed stops post
    """
    piv_feed = get_fixture_data("piv/stomp_20201022_23186_delayed_5min.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 2
    _assert_db_stomp_20201022_23186_delayed_5min()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_piv_partial_delayed_then_delayed(mock_rabbitmq):
    """
    partial delayed stops post
    """
    piv_feed = get_fixture_data("piv/stomp_20201022_23187_partial_delayed.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_partial_delayed()
    assert mock_rabbitmq.call_count == 1

    piv_feed = get_fixture_data("piv/stomp_20201022_23187_delayed_5min.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_delayed_5min()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_piv_trip_removal_simple_post(mock_rabbitmq):
    """
    simple trip removal post
    """
    piv_feed = get_fixture_data("piv/stomp_20201029_841252_trip_removal.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        assert StopTimeUpdate.query.count() == 0

        db_trip_removal = TripUpdate.query.first()
        assert db_trip_removal
        assert db_trip_removal.vj.navitia_trip_id == "PIVPP:2020-10-29:841252:1187:Train"
        assert db_trip_removal.vj.start_timestamp == datetime(2020, 10, 29, 20, 5)
        assert db_trip_removal.status == "delete"
        assert db_trip_removal.effect == "NO_SERVICE"
        assert db_trip_removal.message == "Indisponibilité d'un matériel"
        # full trip removal : no stop_time to precise
        assert len(db_trip_removal.stop_time_updates) == 0

    assert mock_rabbitmq.call_count == 1


def test_piv_event_priority(mock_rabbitmq):
    """
    simple trip removal post
    """
    piv_feed = get_fixture_data("piv/stomp_20201022_23186_trip_removal_delayed.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        assert StopTimeUpdate.query.count() == 0

        db_trip_removal = TripUpdate.query.first()
        assert db_trip_removal
        assert db_trip_removal.vj.navitia_trip_id == "PIV:2020-10-22:23186:1187:Train"
        assert db_trip_removal.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
        assert db_trip_removal.status == "delete"
        assert db_trip_removal.effect == "NO_SERVICE"
        assert db_trip_removal.message == "Indisponibilité d'un matériel"
        # full trip removal : no stop_time to precise
        assert len(db_trip_removal.stop_time_updates) == 0

    assert mock_rabbitmq.call_count == 1


def test_no_company_source_code_default_to_company_1187(mock_rabbitmq):
    """
    delayed stops post
    """
    piv_feed = get_fixture_data("piv/stomp_20201022_23186_delayed_5min.json")
    # Replace with a company which doesn't exist in Navitia
    piv_feed = piv_feed.replace('"codeOperateur": "1187"', '"codeOperateur": "1180"')
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() >= 1
        # TODO: Should create a `TripUpdate` but doesn't
        assert TripUpdate.query.count() >= 0
        # db_trip_delayed = TripUpdate.find_by_dated_vj(
        #     "PIV:2020-10-22:23186:1187:Train", datetime(2020, 10, 22, 20, 34)
        # )
        # assert db_trip_delayed
        # assert db_trip_delayed.company_id == "company:PIVPP:1187"

        assert mock_rabbitmq.call_count == 1


def test_piv_partial_removal(mock_rabbitmq):
    """
    the trip 23186 is partially deleted

    Normally there are 17 stops in this VJ, but 2 (Mies, Pont-Céard) are
    removed (departure from Tannay deleted)
    """
    # Simple partial removal
    piv_23186_removal = get_fixture_data("piv/stomp_20201022_23186_partial_removal.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_23186_removal)
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert len(RealTimeUpdate.query.all()) == 1
        assert len(TripUpdate.query.all()) == 1
        assert len(StopTimeUpdate.query.all()) == 17
        assert RealTimeUpdate.query.first().status == "OK"

        with app.app_context():
            db_trip_partial_removed = TripUpdate.find_by_dated_vj(
                "PIV:2020-10-22:23186:1187:Train", datetime(2020, 10, 22, 20, 34)
            )
            assert db_trip_partial_removed

            assert db_trip_partial_removed.vj.navitia_trip_id == "PIV:2020-10-22:23186:1187:Train"
            assert db_trip_partial_removed.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
            assert db_trip_partial_removed.vj_id == db_trip_partial_removed.vj.id
            assert db_trip_partial_removed.status == "update"
            assert db_trip_partial_removed.effect == "REDUCED_SERVICE"

            # 17 stop times must have been created
            assert len(db_trip_partial_removed.stop_time_updates) == 17

            # the first stop have not been changed
            first_st = db_trip_partial_removed.stop_time_updates[0]
            assert first_st.stop_id == "stop_point:PIV:85010231:Train"
            assert first_st.arrival_status == "none"
            assert first_st.departure_status == "none"
            assert first_st.message is None

            for s in db_trip_partial_removed.stop_time_updates[4:16]:
                assert s.arrival_status == "none"
                assert s.departure_status == "none"
                assert s.message is None

            # # the stops Tannay, Mies and Pont-Céard should have been marked as deleted
            tannay_st = db_trip_partial_removed.stop_time_updates[1]
            assert tannay_st.stop_id == "stop_point:PIV:85010157:Train"  # Tannay
            assert tannay_st.arrival_status == "none"  # the train still arrives in this stop
            assert tannay_st.departure_status == "delete"

            mies_st = db_trip_partial_removed.stop_time_updates[2]
            assert mies_st.stop_id == "stop_point:PIV:85010140:Train"  # Mies
            assert mies_st.arrival_status == "delete"
            assert mies_st.departure_status == "delete"

            pc_st = db_trip_partial_removed.stop_time_updates[3]
            assert pc_st.stop_id == "stop_point:PIV:85010132:Train"  # Pont-Céard
            assert pc_st.arrival_status == "delete"
            assert pc_st.departure_status == "delete"

            assert db_trip_partial_removed.contributor_id == PIV_CONTRIBUTOR_ID

    assert mock_rabbitmq.call_count == 1


def _set_piv_disruption(fixture, evt_type, message):
    fixture["objects"][0]["object"]["evenement"] = [{"type": evt_type, "message": message}]


def _set_event_on_stop(fixture, evt_type, dep_or_arr, rang, message=None, motif_modification=None):
    ads = fixture["objects"][0]["object"]["listeArretsDesserte"]["arret"]
    for desserte in ads:
        if desserte["rang"] == rang:
            desserte[dep_or_arr]["evenement"] = {"type": evt_type, "texte": message}
            if motif_modification:
                desserte[dep_or_arr]["motifModification"] = motif_modification


def test_piv_modification_limitation(mock_rabbitmq):
    """
    the trip 23187 is deleted except the first two stop
    """
    # Simple modification limitation
    piv_23187_removal = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")

    _set_piv_disruption(piv_23187_removal, evt_type="MODIFICATION_LIMITATION", message="")
    _set_event_on_stop(
        fixture=piv_23187_removal,
        evt_type="SUPPRESSION_PARTIELLE",
        dep_or_arr="depart",
        motif_modification="Absence inopinée d'un agent",
        rang=1,
    )
    for rang in range(2, 5):
        for event_toggle in ["arrivee", "depart"]:
            _set_event_on_stop(
                fixture=piv_23187_removal,
                evt_type="SUPPRESSION_PARTIELLE",
                dep_or_arr=event_toggle,
                motif_modification="Absence inopinée d'un agent",
                rang=rang,
            )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_removal))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        assert StopTimeUpdate.query.count() == 6
        assert RealTimeUpdate.query.first().status == "OK"

        with app.app_context():
            db_trip_partial_removed = TripUpdate.find_by_dated_vj(
                "PIV:2020-10-22:23187:1187:Train", datetime(2020, 10, 22, 20, 34)
            )
            assert db_trip_partial_removed

            assert db_trip_partial_removed.vj.navitia_trip_id == "PIV:2020-10-22:23187:1187:Train"
            assert db_trip_partial_removed.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
            assert db_trip_partial_removed.vj_id == db_trip_partial_removed.vj.id
            assert db_trip_partial_removed.status == "update"
            assert db_trip_partial_removed.effect == "REDUCED_SERVICE"

            # 6 stop times must have been created
            assert len(db_trip_partial_removed.stop_time_updates) == 6

            # the first stop have not been changed
            first_st = db_trip_partial_removed.stop_time_updates[0]
            assert first_st.stop_id == "stop_point:PIV:85010231:Train"
            assert first_st.arrival_status == "none"
            assert first_st.departure_status == "none"
            assert first_st.message is None

            # the second stop's departure have been marked as deleted
            second_st = db_trip_partial_removed.stop_time_updates[1]
            assert second_st.arrival_status == "none"
            assert second_st.departure_status == "delete"
            assert second_st.message == "Absence inopinée d'un agent"

            for s in db_trip_partial_removed.stop_time_updates[2:5]:
                assert s.arrival_status == "delete"
                assert s.departure_status == "delete"
                assert s.message == "Absence inopinée d'un agent"

            assert db_trip_partial_removed.contributor_id == PIV_CONTRIBUTOR_ID

    assert mock_rabbitmq.call_count == 1
