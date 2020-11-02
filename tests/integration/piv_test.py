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
from tests.check_utils import api_post, api_get, get_fixture_data
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


def _check_db_stomp_20201022_23186_delayed_5min():
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
        assert db_trip_delayed.message is None  # TODO xfail: to be updated when messages are read
        # PIV contain delayed stop_times only
        assert db_trip_delayed.company_id == "company:PIV:1187"
        assert len(db_trip_delayed.stop_time_updates) == 17

        first_st = db_trip_delayed.stop_time_updates[0]
        assert first_st.stop_id == "stop_point:PIV:85010231:Train"
        # no specific functional constraint on first arrival, except time consistency
        assert first_st.arrival <= first_st.departure
        assert first_st.departure == datetime(2020, 10, 22, 20, 39)
        assert first_st.departure_delay == timedelta(minutes=5)
        assert first_st.departure_status == ModificationType.update.name
        assert first_st.message is None  # TODO xfail: to be updated when messages are read

        second_st = db_trip_delayed.stop_time_updates[1]
        assert second_st.stop_id == "stop_point:PIV:85010157:Train"
        assert second_st.arrival == datetime(2020, 10, 22, 20, 40)
        assert second_st.arrival_status == ModificationType.update.name
        assert second_st.arrival_delay == timedelta(minutes=5)
        assert second_st.departure == datetime(2020, 10, 22, 20, 40, 30)
        assert second_st.departure_delay == timedelta(minutes=5)
        assert second_st.departure_status == ModificationType.update.name
        assert second_st.message is None  # TODO xfail: to be updated when messages are read

        for st in db_trip_delayed.stop_time_updates[2:-1]:
            assert st.stop_id
            assert datetime(2020, 10, 22, 20, 42) <= st.arrival <= datetime(2020, 10, 22, 21, 24)
            assert st.arrival_status == ModificationType.update.name
            assert st.arrival_delay == timedelta(minutes=5)
            assert datetime(2020, 10, 22, 20, 43) <= st.departure <= datetime(2020, 10, 22, 21, 25)
            assert st.departure_status == ModificationType.update.name
            assert st.departure_delay == timedelta(minutes=5)
            assert st.message is None  # TODO xfail: to be updated when messages are read

        last_st = db_trip_delayed.stop_time_updates[-1]
        assert last_st.stop_id == "stop_point:PIV:87745497:Train"
        assert last_st.arrival == datetime(2020, 10, 22, 21, 30)
        assert last_st.arrival_status == ModificationType.update.name
        assert last_st.arrival_delay == timedelta(minutes=5)
        # no specific functional constraint on last departure, except time consistency
        assert last_st.arrival <= last_st.departure
        assert second_st.message is None  # TODO xfail: to be updated when messages are read

        assert db_trip_delayed.contributor_id == PIV_CONTRIBUTOR_ID

        return db_trip_delayed  # for additional testing if needed


def test_piv_delayed(mock_rabbitmq):
    """
    delayed stops post
    """
    piv_feed = get_fixture_data("piv/stomp_20201022_23186_delayed_5min.json")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_feed)
    assert "PIV feed processed" in res.get("message")

    db_trip_delayed = _check_db_stomp_20201022_23186_delayed_5min()
    assert db_trip_delayed.effect == "SIGNIFICANT_DELAYS"
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 1