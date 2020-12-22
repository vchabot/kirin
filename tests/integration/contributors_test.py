# coding=utf-8

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

from kirin import app, db, resources
from kirin.core import model
from flask import json
import pytest
import jsonschema
from kirin.core.model import (
    RealTimeUpdate,
    db,
    TripUpdate,
    VehicleJourney,
    StopTimeUpdate,
    Contributor,
    DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
    DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
)
from kirin.command.purge_rt import purge_contributor
from kirin.core.types import ConnectorType
from kirin.utils import db_commit
from tests.integration.gtfs_rt_test import basic_gtfs_rt_data, navitia


def test_get_contributor_end_point(test_client):
    assert test_client.get("/contributors").status_code == 200


@pytest.fixture
def with_custom_contributors():
    # Clean table contributor before adding any elements as we fill two contributors in the function clean_db
    db.session.execute("TRUNCATE table contributor CASCADE;")
    db.session.commit()

    db.session.add_all(
        [
            model.Contributor(
                "realtime.sherbrooke", "ca", ConnectorType.gtfs_rt.value, "my_token", "http://feed.url", 5
            ),
            model.Contributor(
                "realtime.paris", "idf", ConnectorType.gtfs_rt.value, "my_other_token", "http://otherfeed.url"
            ),
            model.Contributor("realtime.london", "gb", ConnectorType.cots.value),
        ]
    )
    db.session.commit()


def test_get_contributors(test_client, with_custom_contributors):
    resp = test_client.get("/contributors")
    assert resp.status_code == 200

    data = json.loads(resp.data)
    assert len(data["contributors"]) == 3

    ids = [c["id"] for c in data["contributors"]]
    ids.sort()

    assert ids == ["realtime.london", "realtime.paris", "realtime.sherbrooke"]


def test_get_contributors_with_specific_id(test_client, with_custom_contributors):
    resp = test_client.get("/contributors/realtime.paris")
    assert resp.status_code == 200

    data = json.loads(resp.data)
    contrib = data["contributors"]
    assert len(contrib) == 1
    assert contrib[0]["id"] == "realtime.paris"
    assert contrib[0]["navitia_coverage"] == "idf"
    assert contrib[0]["connector_type"] == ConnectorType.gtfs_rt.value
    assert contrib[0]["navitia_token"] == "my_other_token"
    assert contrib[0]["feed_url"] == "http://otherfeed.url"
    assert contrib[0]["retrieval_interval"] == 10


def test_get_contributors_with_specific_retrieval_interval(test_client, with_custom_contributors):
    resp = test_client.get("/contributors/realtime.sherbrooke")
    assert resp.status_code == 200

    data = json.loads(resp.data)
    contrib = data["contributors"]
    assert len(contrib) == 1
    assert contrib[0]["id"] == "realtime.sherbrooke"
    assert contrib[0]["navitia_coverage"] == "ca"
    assert contrib[0]["connector_type"] == ConnectorType.gtfs_rt.value
    assert contrib[0]["navitia_token"] == "my_token"
    assert contrib[0]["feed_url"] == "http://feed.url"
    assert contrib[0]["retrieval_interval"] == 5


def test_get_partial_contributor_with_empty_fields(test_client, with_custom_contributors):
    resp = test_client.get("/contributors/realtime.london")
    assert resp.status_code == 200

    data = json.loads(resp.data)
    contrib = data["contributors"]
    assert len(contrib) == 1
    assert contrib[0]["navitia_token"] is None
    assert contrib[0]["feed_url"] is None


def test_get_contributors_with_wrong_id(test_client, with_custom_contributors):
    resp = test_client.get("/contributors/this_id_doesnt_exist")
    assert resp.status_code == 404


def test_post_schema_distributor_is_valid():
    jsonschema.Draft4Validator.check_schema(resources.Contributors.post_data_schema)


def test_put_schema_distributor_is_valid():
    jsonschema.Draft4Validator.check_schema(resources.Contributors.put_data_schema)


def test_post_new_contributor(test_client):
    new_contrib = {
        "id": "realtime.tokyo",
        "navitia_coverage": "jp",
        "navitia_token": "blablablabla",
        "feed_url": "http://nihongo.jp",
        "connector_type": ConnectorType.gtfs_rt.value,
        "retrieval_interval": 30,
        "nb_days_to_keep_trip_update": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
        "nb_days_to_keep_rt_update": DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
    }
    resp = test_client.post("/contributors", json=new_contrib)
    assert resp.status_code == 201

    contrib = db.session.query(model.Contributor).filter(model.Contributor.id == "realtime.tokyo").first()
    assert contrib.id == "realtime.tokyo"
    assert contrib.navitia_coverage == "jp"
    assert contrib.connector_type == ConnectorType.gtfs_rt.value
    assert contrib.navitia_token == "blablablabla"
    assert contrib.feed_url == "http://nihongo.jp"
    assert contrib.retrieval_interval == 30
    assert contrib.nb_days_to_keep_trip_update == DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE
    assert contrib.nb_days_to_keep_rt_update == DEFAULT_DAYS_TO_KEEP_RT_UPDATE


def test_post_new_partial_contributor(test_client):
    new_contrib = {
        "id": "realtime.tokyo",
        "navitia_coverage": "jp",
        "connector_type": ConnectorType.gtfs_rt.value,
        "nb_days_to_keep_trip_update": 10,
        "nb_days_to_keep_rt_update": 200,
    }
    resp = test_client.post("/contributors", json=new_contrib)
    assert resp.status_code == 201

    contrib = db.session.query(model.Contributor).filter(model.Contributor.id == "realtime.tokyo").first()
    assert contrib.id == "realtime.tokyo"
    assert contrib.navitia_coverage == "jp"
    assert contrib.connector_type == ConnectorType.gtfs_rt.value
    assert contrib.navitia_token is None
    assert contrib.feed_url is None
    assert contrib.retrieval_interval == 10
    assert contrib.nb_days_to_keep_trip_update == 10
    assert contrib.nb_days_to_keep_rt_update == 200


def test_post_empty_contributor_should_fail(test_client):
    resp = test_client.post("/contributors")
    assert resp.status_code == 400


def test_post_with_id_in_the_resource_path(test_client):
    resp = test_client.post(
        "/contributors/realtime.test",
        json={
            "navitia_coverage": "jp",
            "connector_type": ConnectorType.gtfs_rt.value,
            "nb_days_to_keep_trip_update": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
            "nb_days_to_keep_rt_update": DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
        },
    )
    assert resp.status_code == 201

    contrib = db.session.query(model.Contributor).filter(model.Contributor.id == "realtime.test").first()
    assert contrib.id == "realtime.test"


def test_post_with_id_in_the_resource_path_and_data(test_client):
    resp = test_client.post(
        "/contributors/the_real_id",
        json={
            "id": "overridden_id",
            "navitia_coverage": "jp",
            "connector_type": ConnectorType.gtfs_rt.value,
        },
    )
    assert resp.status_code == 201

    assert 1 == db.session.query(model.Contributor).filter(model.Contributor.id == "the_real_id").count()
    assert 0 == db.session.query(model.Contributor).filter(model.Contributor.id == "overridden_id").count()


def test_post_contributor_with_wrong_connector_type(test_client):
    new_contrib = {"id": "realtime.tokyo", "coverage": "jp", "connector_type": "THIS-TYPE-DOES-NOT-EXIST"}
    resp = test_client.post("/contributors", json=new_contrib)
    assert resp.status_code == 400


def test_post_2_contributors_with_same_id_should_fail(test_client):
    resp = test_client.post(
        "/contributors",
        json={"id": "realtime.test", "navitia_coverage": "jp", "connector_type": ConnectorType.gtfs_rt.value},
    )
    resp = test_client.post(
        "/contributors",
        json={"id": "realtime.test", "navitia_coverage": "fr", "connector_type": ConnectorType.cots.value},
    )
    assert resp.status_code == 400


def test_post_contributor_with_wrong_connector_type_should_fail(test_client):
    resp = test_client.post(
        "/contributors",
        json={"id": "realtime.tokyo", "navitia_coverage": "jp", "connector_type": "THIS-TYPE-DOES-NOT-EXIST"},
    )
    assert resp.status_code == 400


def test_post_new_valid_contributor_with_unknown_parameter_should_work(test_client):
    resp = test_client.post(
        "/contributors",
        json={
            "UNKNOWN_PARAM": "gibberish",
            "id": "realtime.tokyo",
            "navitia_coverage": "jp",
            "connector_type": ConnectorType.gtfs_rt.value,
            "nb_days_to_keep_trip_update": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
            "nb_days_to_keep_rt_update": DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
        },
    )
    assert resp.status_code == 201


def test_put_contributor_with_id(test_client):
    db_commit(
        model.Contributor(
            id="SaintMeuMeu",
            navitia_coverage="ca",
            connector_type=ConnectorType.cots.value,
            navitia_token="this_is_a_token",
            feed_url="http://feed.url",
        )
    )

    resp = test_client.put(
        "/contributors/SaintMeuMeu",
        json={
            "navitia_coverage": "qb",
            "connector_type": ConnectorType.gtfs_rt.value,
            "navitia_token": "new_token",
            "feed_url": "http://new.feed",
            "retrieval_interval": 50,
        },
    )

    assert resp.status_code == 200

    contrib = db.session.query(model.Contributor).filter(model.Contributor.id == "SaintMeuMeu").first()
    assert contrib.navitia_coverage == "qb"
    assert contrib.connector_type == ConnectorType.gtfs_rt.value
    assert contrib.navitia_token == "new_token"
    assert contrib.feed_url == "http://new.feed"
    assert contrib.retrieval_interval == 50


def test_put_partial_contributor(test_client):
    db_commit(
        model.Contributor(
            id="SaintMeuMeu",
            navitia_coverage="ca",
            connector_type=ConnectorType.cots.value,
            navitia_token="this_is_a_token",
            feed_url="http://feed.url",
        )
    )

    put_resp = test_client.put("/contributors/SaintMeuMeu", json={"navitia_coverage": "qb"})
    assert put_resp.status_code == 200

    get_resp = test_client.get("/contributors/SaintMeuMeu")

    put_data = json.loads(put_resp.data)
    get_data = json.loads(get_resp.data)
    assert put_data["contributor"] == get_data["contributors"][0]


def test_put_contributor_with_no_data(test_client):
    db_commit(
        model.Contributor(
            id="SaintMeuMeu",
            navitia_coverage="ca",
            connector_type=ConnectorType.cots.value,
            navitia_token="this_is_a_token",
            feed_url="http://feed.url",
        )
    )

    resp = test_client.put("/contributors/SaintMeuMeu")
    assert resp.status_code == 400


def test_put_contributor_without_id(test_client, with_custom_contributors):
    resp = test_client.put("/contributors")
    assert resp.status_code == 400


def test_put_unknown_contributor(test_client, with_custom_contributors):
    resp = test_client.put("/contributors/SaintMeuMeu", json={"navitia_coverage": "qb"})
    assert resp.status_code == 404


def test_put_contributor_with_malformed_data(test_client, with_custom_contributors):
    resp = test_client.put("/contributors/realtime.paris", json={"feed_url": 42})
    assert resp.status_code == 400


def test_post_get_put_to_ensure_API_consistency(test_client):
    new_contrib = {
        "id": "realtime.tokyo",
        "navitia_coverage": "jp",
        "navitia_token": "blablablabla",
        "feed_url": "http://nihongo.jp",
        "connector_type": ConnectorType.gtfs_rt.value,
        "retrieval_interval": 180,
        "is_active": True,
        "nb_days_to_keep_trip_update": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
        "nb_days_to_keep_rt_update": DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
    }
    post_resp = test_client.post("/contributors", json=new_contrib)
    post_contrib = json.loads(post_resp.data)
    new_contrib["broker_url"] = None
    new_contrib["exchange_name"] = None
    new_contrib["queue_name"] = None
    new_contrib["nb_days_to_keep_trip_update"] = DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE
    new_contrib["nb_days_to_keep_rt_update"] = DEFAULT_DAYS_TO_KEEP_RT_UPDATE
    assert post_contrib["contributor"] == new_contrib

    get_resp = test_client.get("/contributors/realtime.tokyo")
    get_contrib = json.loads(get_resp.data)["contributors"][0]
    assert get_contrib == new_contrib

    put_resp = test_client.put("/contributors", json=get_contrib)
    put_data = json.loads(put_resp.data)
    assert put_data["contributor"] == new_contrib


def test_existing_contributors_after_put(test_client):
    # get existing contributor "rt.tchoutchou"
    get_contrib = test_client.get("/contributors/rt.tchoutchou")
    original_contrib_tchou = json.loads(get_contrib.data)["contributors"][0]

    # Get and modify another contributor "rt.vroumvroum" and verify that
    # modification of this contributor doesn't affect another.
    get_contrib = test_client.get("/contributors/rt.vroumvroum")
    contrib_vroumvroum = json.loads(get_contrib.data)["contributors"][0]
    contrib_vroumvroum["navitia_coverage"] = "tokyo"
    contrib_vroumvroum["navitia_token"] = "tokyo_token"
    put_resp = test_client.put("/contributors", json=contrib_vroumvroum)
    put_data = json.loads(put_resp.data)
    assert put_data["contributor"] == contrib_vroumvroum

    # Verify that  the contributor "rt.tchoutchou" is not modified
    get_contrib = test_client.get("/contributors/rt.tchoutchou")
    contrib_tchou = json.loads(get_contrib.data)["contributors"][0]
    assert contrib_tchou == original_contrib_tchou


def test_deactivate_contributor(test_client):
    # get existing contributor "rt.tchoutchou" which is active
    get_contrib = test_client.get("/contributors/rt.tchoutchou")
    contrib_tchou = json.loads(get_contrib.data)["contributors"][0]
    assert contrib_tchou["is_active"] is True

    # Modify attribute is_active to false and test after put
    contrib_tchou["is_active"] = False
    put_resp = test_client.put("/contributors", json=contrib_tchou)
    put_data = json.loads(put_resp.data)
    assert put_data["contributor"]["id"] == "rt.tchoutchou"
    assert put_data["contributor"]["is_active"] is False

    # Verify the same contributor with a /get
    get_contrib = test_client.get("/contributors/rt.tchoutchou")
    contrib_tchou = json.loads(get_contrib.data)["contributors"][0]
    assert contrib_tchou["is_active"] is False


def test_activate_contributor(test_client):
    new_contrib = {
        "id": "realtime.tokyo",
        "navitia_coverage": "jp",
        "navitia_token": "blablablabla",
        "feed_url": "http://nihongo.jp",
        "connector_type": ConnectorType.gtfs_rt.value,
        "is_active": False,
        "nb_days_to_keep_trip_update": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
        "nb_days_to_keep_rt_update": DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
    }
    test_client.post("/contributors", json=new_contrib)

    get_resp = test_client.get("/contributors/realtime.tokyo")
    get_contrib = json.loads(get_resp.data)["contributors"][0]
    assert get_contrib["is_active"] is False

    # Modify attribute is_active to false and test after put
    get_contrib["is_active"] = True
    put_resp = test_client.put("/contributors", json=get_contrib)
    put_data = json.loads(put_resp.data)
    assert put_data["contributor"]["id"] == "realtime.tokyo"
    assert put_data["contributor"]["is_active"] is True

    get_resp = test_client.get("/contributors/realtime.tokyo")
    get_contrib = json.loads(get_resp.data)["contributors"][0]
    assert get_contrib["is_active"] is True


def test_delete_contributor(test_client, with_custom_contributors):
    get_resp = test_client.get("/contributors")
    get_data = json.loads(get_resp.data)
    assert "contributors" in get_data
    initial_nb = len(get_data["contributors"])
    contributor_to_delete = get_data["contributors"][0]["id"]

    delete_resp = test_client.delete("/contributors/{}".format(contributor_to_delete))
    assert delete_resp._status_code == 204

    # Check that the deleted contributor isn't in the list of all contributors
    get_resp = test_client.get("/contributors")
    get_data = json.loads(get_resp.data)
    assert "contributors" in get_data
    assert len(get_data["contributors"]) == initial_nb - 1

    # Check that the info about the deleted contributor can be requested with its id
    get_id_resp = test_client.get("/contributors/{}".format(contributor_to_delete))
    get_id_data = json.loads(get_id_resp.data)
    assert "contributors" in get_id_data
    assert len(get_id_data["contributors"]) == 1
    assert get_id_data["contributors"][0]["id"] == contributor_to_delete
    assert get_id_data["contributors"][0]["is_active"] == False

    # Check that a deleted contributor can't be deleted again
    delete_resp = test_client.delete("/contributors/{}".format(contributor_to_delete))
    assert delete_resp._status_code == 404


def test_purge_contributor(test_client, basic_gtfs_rt_data, mock_rabbitmq):
    """
    Test the purge with different contributors and rt data
    """

    def test_rt_data():
        with app.app_context():
            assert len(RealTimeUpdate.query.all()) == 1
            assert len(TripUpdate.query.all()) == 1
            assert len(VehicleJourney.query.all()) == 1
            assert len(StopTimeUpdate.query.all()) == 4

    def test_contributor_count(contrib_count):
        with app.app_context():
            assert len(Contributor.query.all()) == contrib_count

    def deactivate_contributor(contributor_id):
        with app.app_context():
            contrib = Contributor.query.filter_by(id=contributor_id).first()
            contrib.is_active = False
            db.session.commit()

    def has_rt_data(contributor_id):
        with app.app_context():
            return len(RealTimeUpdate.query.filter_by(contributor_id=contributor_id).all()) > 0

    # We have 6 contributors and all are active:
    test_contributor_count(6)
    with app.app_context():
        contributors = Contributor.query.all()
        contributors_id = [c.id for c in contributors]
        assert "rt.tchoutchou" in contributors_id
        assert "rt.piv" in contributors_id
        assert "rt.vroumvroum" in contributors_id
        for c in contributors:
            assert c.is_active is True

    # Post a simple gtfs-rt for contributor "rt.vroumvroum" in configuration file.
    resp = test_client.post("/gtfs_rt/rt.vroumvroum", data=basic_gtfs_rt_data)
    assert resp.status_code == 200
    test_rt_data()

    # As all the contributors are active, purge_contributor won't do anything
    with app.app_context():
        purge_contributor("rt.vroumvroum")
    test_contributor_count(6)
    test_rt_data()

    # We deactivate a contributor "rt.tchoutchou" with rt data and use purge_contributor
    # purge_contributor won't do anything
    deactivate_contributor("rt.vroumvroum")
    assert has_rt_data("rt.vroumvroum") is True
    with app.app_context():
        purge_contributor("rt.vroumvroum")
    test_contributor_count(6)
    test_rt_data()

    # We deactivate another contributor "rt.vroumvroum_db" without any rt data. purge_contributor will simply
    # delete rt.vroumvroum_db from the table contributor not other tables.
    deactivate_contributor("rt.vroumvroum_db")
    assert has_rt_data("rt.vroumvroum_db") is False
    with app.app_context():
        purge_contributor("rt.vroumvroum_db")
    test_contributor_count(5)
    test_rt_data()


def test_piv_contributor(test_client):
    new_contrib = {
        "id": "realtime.sncf.piv",
        "navitia_coverage": "sncf",
        "navitia_token": "blablablabla",
        "feed_url": "no_url",
        "connector_type": ConnectorType.piv.value,
        "retrieval_interval": 30,
        "nb_days_to_keep_trip_update": DEFAULT_DAYS_TO_KEEP_TRIP_UPDATE,
        "nb_days_to_keep_rt_update": DEFAULT_DAYS_TO_KEEP_RT_UPDATE,
    }
    resp = test_client.post("/contributors", json=new_contrib)
    assert resp.status_code == 201

    contrib = db.session.query(model.Contributor).filter(model.Contributor.id == "realtime.sncf.piv").first()
    assert contrib.id == "realtime.sncf.piv"
    assert contrib.navitia_coverage == "sncf"
    assert contrib.connector_type == ConnectorType.piv.value
    assert contrib.navitia_token == "blablablabla"
    assert contrib.feed_url == "no_url"
    assert contrib.retrieval_interval == 30
