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

from collections import namedtuple
from datetime import timedelta, datetime
from dateutil import parser

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
from tests.check_utils import api_post, api_get, get_fixture_data_as_dict
from tests import mock_navitia
from tests.integration.conftest import PIV_CONTRIBUTOR_ID

ModificationTuple = namedtuple("ModificationTuple", ["statut", "motif"])
DisruptionTuple = namedtuple("DisruptionTuple", ["type", "texte"])


@pytest.fixture(scope="function", autouse=True)
def navitia(monkeypatch):
    """
    Mock all calls to navitia for this fixture
    """
    monkeypatch.setattr("navitia_wrapper._NavitiaWrapper.query", mock_navitia.mock_navitia_query)


def _set_piv_disruption(fixture, disruption):
    obj = fixture["objects"][0]["object"]
    if disruption:
        if not obj.get("evenement"):
            obj["evenement"] = []
        obj["evenement"].append({"type": disruption.type, "texte": disruption.texte})
    elif obj.get("evenement"):
        obj.pop("evenement", None)


def _set_event_on_stop(fixture, dep_or_arr_key, rang, disruption=None, modification=None, retard_dict=None):
    ads = fixture["objects"][0]["object"]["listeArretsDesserte"]["arret"]
    for desserte in ads:
        if desserte["rang"] == rang:
            if desserte.get(dep_or_arr_key):
                if disruption:
                    if disruption.type:
                        desserte[dep_or_arr_key]["evenement"] = {
                            "type": disruption.type,
                            "texte": disruption.texte,
                        }
                    else:
                        desserte[dep_or_arr_key].pop("evenement", None)
                if retard_dict:
                    if "evenement" in desserte[dep_or_arr_key]:
                        desserte[dep_or_arr_key]["evenement"]["retard"] = retard_dict
                    date_heure = parser.parse(
                        desserte[dep_or_arr_key]["dateHeure"], dayfirst=False, yearfirst=True, ignoretz=False
                    )
                    date_heure_reelle = date_heure + timedelta(minutes=retard_dict["duree"])
                    desserte[dep_or_arr_key]["dateHeureReelle"] = date_heure_reelle.isoformat()
                if modification:
                    if modification.statut:
                        desserte[dep_or_arr_key]["statutModification"] = modification.statut
                    else:
                        desserte[dep_or_arr_key].pop("statutModification", None)
                    if modification.motif:
                        desserte[dep_or_arr_key]["motifModification"] = modification.motif
                    else:
                        desserte[dep_or_arr_key].pop("motifModification", None)


def _set_event_on_stops(fixture, rang_min, rang_max, disruption=None, modification=None, retard_dict=None):
    for event_toggle in ["arrivee", "depart"]:
        for rang in range(rang_min, rang_max + 1):
            _set_event_on_stop(
                fixture=fixture,
                dep_or_arr_key=event_toggle,
                rang=rang,
                disruption=disruption,
                modification=modification,
                retard_dict=retard_dict,
            )


def _get_stomp_20201022_23187_partial_delayed_fixture():
    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="RETARD", texte="Absence inopinée d'un agent")
    _set_piv_disruption(piv_feed, disruption=disruption)
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement depart)")
    modification = ModificationTuple(motif="Absence inopinée d'un agent", statut=None)
    retard = {"duree": 5, "dureeInterne": 8}
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="depart",
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang=0,
    )
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement arrivee)")
    modification = ModificationTuple(
        motif="Absence inopinée d'un agent (motifModification arrivee)", statut=None
    )
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="arrivee",
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang=1,
    )
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement depart)")
    modification = ModificationTuple(motif="Absence inopinée d'un agent (motifModification depart)", statut=None)
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="depart",
        modification=modification,
        disruption=disruption,
        retard_dict=retard,
        rang=1,
    )
    retard = {"duree": 0, "dureeInterne": 2}
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement arrivee)")
    modification = ModificationTuple(
        motif="Absence inopinée d'un agent (motifModification arrivee)", statut=None
    )
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="arrivee",
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang=2,
    )
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement depart)")
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="depart",
        disruption=disruption,
        retard_dict=retard,
        rang=2,
    )
    disruption = DisruptionTuple(type="NORMAL", texte="Absence inopinée d'un agent")
    retard = {"duree": 0, "dureeInterne": 0}
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="arrivee",
        disruption=disruption,
        retard_dict=retard,
        rang=3,
    )
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="depart",
        disruption=disruption,
        retard_dict=retard,
        rang=3,
    )
    return piv_feed


def _get_stomp_20201022_23187_delayed_5min_fixture():
    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="RETARD", texte="Absence inopinée d'un agent")
    _set_piv_disruption(piv_feed, disruption=disruption)
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement depart)")
    modification = ModificationTuple(motif="Absence inopinée d'un agent", statut=None)
    retard = {"duree": 5, "dureeInterne": 8}
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="depart",
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang=0,
    )
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement arrivee)")
    modification = ModificationTuple(
        motif="Absence inopinée d'un agent (motifModification arrivee)", statut=None
    )
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="arrivee",
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang=1,
    )
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement depart)")
    modification = ModificationTuple(motif="Absence inopinée d'un agent (motifModification depart)", statut=None)
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="depart",
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang=1,
    )
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement arrivee)")
    modification = ModificationTuple(
        motif="Absence inopinée d'un agent (motifModification arrivee)", statut=None
    )
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="arrivee",
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang=2,
    )
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent (evenement depart)")
    _set_event_on_stop(
        fixture=piv_feed,
        dep_or_arr_key="depart",
        disruption=disruption,
        retard_dict=retard,
        rang=2,
    )
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent")
    modification = ModificationTuple(motif="", statut=None)
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang_min=3,
        rang_max=5,
    )

    return piv_feed


def _get_stomp_20201022_23187_stop_time_added_at_the_beginning_fixture():
    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="MODIFICATION_PROLONGATION", texte="")
    _set_piv_disruption(piv_feed, disruption=disruption)

    ads = piv_feed["objects"][0]["object"]["listeArretsDesserte"]["arret"]
    depart_85010116 = {
        "depart": {
            "numeroCirculation": "23187",
            "dateHeure": "2020-10-22T22:10:00+02:00",
            "dateHeureReelle": "2020-10-22T22:10:00+02:00",
            "sourceHoraire": "CO",
            "typeAffichage": "HORAIRE",
            "indicateurAdaptation": False,
            "planTransportSource": "PTA",
            "statutModification": "CREATION",
        },
        "emplacement": {"code": "85010116"},
        "rang": 0,
        "dureeStationnement": 1,
        "dureeStationnementReelle": 1,
    }
    sp_85010082 = {
        "arrivee": {
            "numeroCirculation": "23187",
            "dateHeure": "2020-10-22T22:22:00+02:00",
            "dateHeureReelle": "2020-10-22T22:22:00+02:00",
            "sourceHoraire": "CO",
            "typeAffichage": "HORAIRE",
            "indicateurAdaptation": False,
            "planTransportSource": "PTA",
            "statutModification": "CREATION",
        },
        "depart": {
            "numeroCirculation": "23187",
            "dateHeure": "2020-10-22T22:24:00+02:00",
            "dateHeureReelle": "2020-10-22T22:24:00+02:00",
            "sourceHoraire": "CO",
            "typeAffichage": "HORAIRE",
            "indicateurAdaptation": False,
            "planTransportSource": "PTA",
            "statutModification": "CREATION",
        },
        "emplacement": {"code": "85010082"},
        "rang": 1,
        "dureeStationnement": 1,
        "dureeStationnementReelle": 1,
    }
    arrivee_87745497 = {
        "numeroCirculation": "23187",
        "dateHeure": "2020-10-22T22:32:00+02:00",
        "dateHeureReelle": "2020-10-22T22:32:00+02:00",
        "sourceHoraire": "CO",
        "typeAffichage": "HORAIRE",
        "indicateurAdaptation": False,
        "planTransportSource": "PTA",
        "statutModification": "CREATION",
    }
    for desserte in ads:
        li_rang = desserte["rang"]
        if li_rang == 0:
            desserte["arrivee"] = arrivee_87745497
        desserte["rang"] = li_rang + 2
    ads.insert(0, sp_85010082)
    ads.insert(0, depart_85010116)
    piv_feed["objects"][0]["object"]["listeArretsDesserte"]["arret"] = ads
    return piv_feed


def _get_stomp_20201022_23187_stop_time_added_in_the_middle_fixture():
    sp_85010082 = {
        "arrivee": {
            "numeroCirculation": "23187",
            "dateHeure": "2020-10-22T23:05:00+02:00",
            "dateHeureReelle": "2020-10-22T23:05:00+02:00",
            "sourceHoraire": "CO",
            "typeAffichage": "HORAIRE",
            "indicateurAdaptation": False,
            "planTransportSource": "PTA",
            "statutModification": "CREATION",
        },
        "depart": {
            "numeroCirculation": "23187",
            "dateHeure": "2020-10-22T23:07:00+02:00",
            "dateHeureReelle": "2020-10-22T23:07:00+02:00",
            "sourceHoraire": "CO",
            "typeAffichage": "HORAIRE",
            "indicateurAdaptation": False,
            "planTransportSource": "PTA",
            "statutModification": "CREATION",
        },
        "emplacement": {"code": "85010082"},
        "rang": 3,
        "dureeStationnement": 1,
        "dureeStationnementReelle": 1,
    }
    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="MODIFICATION_DESSERTE_AJOUTEE", texte="")
    _set_piv_disruption(piv_feed, disruption=disruption)

    ads = piv_feed["objects"][0]["object"]["listeArretsDesserte"]["arret"]
    for desserte in ads:
        rang = desserte["rang"]
        if rang >= 3:
            desserte["rang"] = rang + 1
    ads.insert(3, sp_85010082)
    piv_feed["objects"][0]["object"]["listeArretsDesserte"]["arret"] = ads
    return piv_feed


def _get_stomp_20201022_23187_stop_time_added_at_the_end_fixture():
    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="MODIFICATION_PROLONGATION", texte="")
    _set_piv_disruption(piv_feed, disruption=disruption)

    ads = piv_feed["objects"][0]["object"]["listeArretsDesserte"]["arret"]
    depart_87745497 = {
        "numeroCirculation": "23187",
        "dateHeure": "2020-10-22T23:26:00+02:00",
        "dateHeureReelle": "2020-10-22T23:26:00+02:00",
        "sourceHoraire": "CO",
        "typeAffichage": "HORAIRE",
        "indicateurAdaptation": False,
        "planTransportSource": "PTA",
        "statutModification": "CREATION",
    }
    sp_85010082 = {
        "arrivee": {
            "numeroCirculation": "23187",
            "dateHeure": "2020-10-22T23:30:00+02:00",
            "dateHeureReelle": "2020-10-22T23:30:00+02:00",
            "sourceHoraire": "CO",
            "typeAffichage": "HORAIRE",
            "indicateurAdaptation": False,
            "planTransportSource": "PTA",
            "statutModification": "CREATION",
        },
        "depart": {
            "numeroCirculation": "23187",
            "dateHeure": "2020-10-22T23:31:00+02:00",
            "dateHeureReelle": "2020-10-22T23:31:00+02:00",
            "sourceHoraire": "CO",
            "typeAffichage": "HORAIRE",
            "indicateurAdaptation": False,
            "planTransportSource": "PTA",
            "statutModification": "CREATION",
        },
        "emplacement": {"code": "85010082"},
        "rang": 6,
        "dureeStationnement": 1,
        "dureeStationnementReelle": 1,
    }
    arrivee_85010116 = {
        "arrivee": {
            "numeroCirculation": "23187",
            "dateHeure": "2020-10-22T23:40:00+02:00",
            "dateHeureReelle": "2020-10-22T23:40:00+02:00",
            "sourceHoraire": "CO",
            "typeAffichage": "HORAIRE",
            "indicateurAdaptation": False,
            "planTransportSource": "PTA",
            "statutModification": "CREATION",
        },
        "emplacement": {"code": "85010116"},
        "rang": 7,
        "dureeStationnement": 1,
        "dureeStationnementReelle": 1,
    }
    for desserte in ads:
        if desserte["rang"] == 5:
            desserte["depart"] = depart_87745497
    ads.append(sp_85010082)
    ads.append(arrivee_85010116)
    return piv_feed


def _get_stomp_20201022_23188_add_trip_fixture(plan_transport_source="OPE"):
    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="CREATION", texte="")
    _set_piv_disruption(piv_feed, disruption=disruption)

    obj = piv_feed["objects"][0]["object"]
    obj["planTransportSource"] = plan_transport_source
    obj["numero"] = "23188"
    ads = obj["listeArretsDesserte"]["arret"]
    for desserte in ads:
        if desserte.get("depart"):
            desserte["depart"]["planTransportSource"] = plan_transport_source
        if desserte.get("arrivee"):
            desserte["arrivee"]["planTransportSource"] = plan_transport_source
    return piv_feed


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
    piv_str = ujson.dumps(_get_stomp_20201022_23187_delayed_5min_fixture())
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_str)
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
        assert rtu.raw_data == piv_str
    assert mock_rabbitmq.call_count == 1


def test_piv_purge(mock_rabbitmq):
    """
    Simple PIV post, then test the purge
    """
    piv_feed = _get_stomp_20201022_23187_delayed_5min_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
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


def _assert_partial_back_ok_simple():
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
        assert fourth_st.arrival == datetime(2020, 10, 22, 21, 16)
        assert fourth_st.arrival_status == ModificationType.none.name
        assert fourth_st.arrival_delay == timedelta(minutes=0)
        assert fourth_st.departure == datetime(2020, 10, 22, 21, 17)
        assert fourth_st.departure_delay == timedelta(minutes=0)
        assert fourth_st.departure_status == ModificationType.none.name
        assert fourth_st.message == "Régulation du trafic"

        fifth_st = db_trip_delayed.stop_time_updates[4]
        assert fifth_st.stop_id == "stop_point:PIV:85162750:Train"
        assert fifth_st.arrival == datetime(2020, 10, 22, 21, 19)
        assert fifth_st.arrival_status == ModificationType.none.name
        assert fifth_st.arrival_delay == timedelta(minutes=0)
        assert fifth_st.departure == datetime(2020, 10, 22, 21, 20)
        assert fifth_st.departure_delay == timedelta(minutes=0)
        assert fifth_st.departure_status == ModificationType.none.name
        assert fifth_st.message == "Régulation du trafic"

        last_st = db_trip_delayed.stop_time_updates[5]
        assert last_st.stop_id == "stop_point:PIV:87745497:Train"
        assert last_st.arrival == datetime(2020, 10, 22, 21, 25)
        assert last_st.arrival_status == ModificationType.none.name
        assert last_st.arrival_delay == timedelta(minutes=0)
        # no specific functional constraint on last departure, except time consistency
        assert last_st.arrival <= last_st.departure
        assert last_st.message == "Régulation du trafic"

        assert db_trip_delayed.contributor_id == PIV_CONTRIBUTOR_ID


def _assert_db_stomp_20201022_23187_normal():
    with app.app_context():
        assert RealTimeUpdate.query.count() >= 1
        assert TripUpdate.query.count() >= 1
        assert StopTimeUpdate.query.count() >= 6
        db_trip = TripUpdate.find_by_dated_vj("PIV:2020-10-22:23187:1187:Train", datetime(2020, 10, 22, 20, 34))
        assert db_trip

        assert db_trip.vj.navitia_trip_id == "PIV:2020-10-22:23187:1187:Train"
        assert db_trip.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
        assert db_trip.vj_id == db_trip.vj.id
        assert db_trip.status == ModificationType.update.name
        assert db_trip.effect == TripEffect.UNKNOWN_EFFECT.name
        assert db_trip.message == ""
        assert db_trip.company_id == "company:PIVPP:1187"
        assert len(db_trip.stop_time_updates) == 6

        for stop_time in db_trip.stop_time_updates:
            assert stop_time.arrival_status == ModificationType.none.name
            assert stop_time.arrival_delay == timedelta(minutes=0)
            assert stop_time.departure_delay == timedelta(minutes=0)
            assert stop_time.departure_status == ModificationType.none.name
            assert stop_time.arrival <= stop_time.departure
            assert stop_time.message is None

        first_st = db_trip.stop_time_updates[0]
        assert first_st.stop_id == "stop_point:PIV:85010231:Train"
        assert first_st.departure == datetime(2020, 10, 22, 20, 34)

        second_st = db_trip.stop_time_updates[1]
        assert second_st.stop_id == "stop_point:PIV:85010157:Train"
        assert second_st.arrival == datetime(2020, 10, 22, 20, 35)
        assert second_st.departure == datetime(2020, 10, 22, 20, 35, 30)

        third_st = db_trip.stop_time_updates[2]
        assert third_st.stop_id == "stop_point:PIV:85010140:Train"
        assert third_st.arrival == datetime(2020, 10, 22, 20, 47)
        assert third_st.departure == datetime(2020, 10, 22, 20, 48)

        fourth_st = db_trip.stop_time_updates[3]
        assert fourth_st.stop_id == "stop_point:PIV:85162735:Train"
        assert fourth_st.arrival == datetime(2020, 10, 22, 21, 16)
        assert fourth_st.departure == datetime(2020, 10, 22, 21, 17)

        fifth_st = db_trip.stop_time_updates[4]
        assert fifth_st.stop_id == "stop_point:PIV:85162750:Train"
        assert fifth_st.arrival == datetime(2020, 10, 22, 21, 19)
        assert fifth_st.departure == datetime(2020, 10, 22, 21, 20)

        last_st = db_trip.stop_time_updates[5]
        assert last_st.stop_id == "stop_point:PIV:87745497:Train"
        assert last_st.arrival == datetime(2020, 10, 22, 21, 25)

        assert db_trip.contributor_id == PIV_CONTRIBUTOR_ID

        return db_trip  # for additional testing if needed


def _assert_db_added_stop_time_in_the_middle():
    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.update.name
        assert db_trip.effect == TripEffect.MODIFIED_SERVICE.name
        assert db_trip.company_id == "company:PIVPP:1187"
        assert db_trip.physical_mode_id == "physical_mode:LongDistanceTrain"
        assert db_trip.headsign == "23187"
        assert len(db_trip.stop_time_updates) == 7

        for s in db_trip.stop_time_updates:
            if s.order != 3:
                assert s.arrival_status == ModificationType.none.name
                assert s.departure_status == ModificationType.none.name

        added_st = db_trip.stop_time_updates[3]
        assert added_st.stop_id == "stop_point:PIV:85010082:Train"
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 05)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 21, 07)


def test_piv_delayed(mock_rabbitmq):
    """
    delayed stops post
    """
    piv_feed = _get_stomp_20201022_23187_delayed_5min_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_delayed_5min()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 1


def test_piv_delayed_post_twice(mock_rabbitmq):
    """
    double delayed stops post
    """
    piv_str = ujson.dumps(_get_stomp_20201022_23187_delayed_5min_fixture())
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_str)
    assert "PIV feed processed" in res.get("message")
    status = api_get("/status")
    assert not status["last_update_error"]  # check no error
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=piv_str)
    assert "PIV feed processed" in res.get("message")
    status = api_get("/status")
    assert (
        status["last_update_error"][PIV_CONTRIBUTOR_ID] == "No new information destined to navitia for this piv"
    )  # check the same-feed detection is working

    with app.app_context():
        assert RealTimeUpdate.query.count() == 2
    _assert_db_stomp_20201022_23187_delayed_5min()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_piv_partial_delayed_then_delayed(mock_rabbitmq):
    """
    partial delayed stops post
    """
    piv_feed = _get_stomp_20201022_23187_partial_delayed_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_partial_delayed()
    assert mock_rabbitmq.call_count == 1

    piv_feed = _get_stomp_20201022_23187_delayed_5min_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_delayed_5min()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_piv_trip_removal_simple_post(mock_rabbitmq):
    """
    simple trip removal post
    """
    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="SUPPRESSION", texte="Indisponibilité d'un matériel")
    _set_piv_disruption(piv_feed, disruption=disruption)
    disruption = DisruptionTuple(type="SUPPRESSION_TOTALE", texte="")
    modification = ModificationTuple(motif="Indisponibilité d'un matériel", statut=None)
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        modification=modification,
        rang_min=0,
        rang_max=5,
    )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        # The 6 stops from base-schedule are marked as deleted.
        # We could also have none as trip-delete is enough.
        # It may help if trip is reactivated in tricky chaining of feeds:
        # 1. Add a stop
        # 2. Delete whole trip
        # 3. Reactivate base-schedule trip only : stop added previously should appear as
        #    deleted (so it has to be remembered).
        assert StopTimeUpdate.query.count() == 6

        db_trip_removal = TripUpdate.query.first()
        assert db_trip_removal
        assert db_trip_removal.vj.navitia_trip_id == "PIV:2020-10-22:23187:1187:Train"
        assert db_trip_removal.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
        assert db_trip_removal.status == ModificationType.delete.name
        assert db_trip_removal.effect == TripEffect.NO_SERVICE.name
        assert db_trip_removal.message == "Indisponibilité d'un matériel"
        assert len(db_trip_removal.stop_time_updates) == 6
        for st in db_trip_removal.stop_time_updates:
            assert st.arrival_status == ModificationType.delete.name
            assert st.departure_status == ModificationType.delete.name

    assert mock_rabbitmq.call_count == 1


def test_piv_event_priority(mock_rabbitmq):
    """
    simple trip removal post
    """
    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="SUPPRESSION", texte="Indisponibilité d'un matériel")
    _set_piv_disruption(piv_feed, disruption=disruption)
    disruption = DisruptionTuple(type="RETARD", texte="Absence inopinée d'un agent")
    _set_piv_disruption(piv_feed, disruption=disruption)
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="")
    modification = ModificationTuple(motif="Indisponibilité d'un matériel", statut=None)
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        modification=modification,
        rang_min=0,
        rang_max=5,
    )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        assert StopTimeUpdate.query.count() == 6  # Remembering all stops that were served once

        db_trip_removal = TripUpdate.query.first()
        assert db_trip_removal
        assert db_trip_removal.vj.navitia_trip_id == "PIV:2020-10-22:23187:1187:Train"
        assert db_trip_removal.vj.start_timestamp == datetime(2020, 10, 22, 20, 34)
        assert db_trip_removal.status == ModificationType.delete.name
        assert db_trip_removal.effect == TripEffect.NO_SERVICE.name
        assert db_trip_removal.message == "Indisponibilité d'un matériel"
        assert len(db_trip_removal.stop_time_updates) == 6
        for st in db_trip_removal.stop_time_updates:
            assert st.arrival_status == ModificationType.delete.name
            assert st.departure_status == ModificationType.delete.name

    assert mock_rabbitmq.call_count == 1


def test_piv_partial_removal(mock_rabbitmq):
    """
    the trip 23187 is partially deleted

    Normally there are 6 stops in this VJ, but 2 (Mies, Genève Eaux-Vives),
    respectively rang 2 and 3, are removed.
    """
    # Simple partial removal
    piv_23187_removal = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="MODIFICATION_DESSERTE_SUPPRIMEE", texte="")
    _set_piv_disruption(piv_23187_removal, disruption=disruption)
    disruption = DisruptionTuple(type="SUPPRESSION_PARTIELLE", texte="")
    modification = ModificationTuple(motif="Absence inopinée d'un agent", statut=None)
    _set_event_on_stops(
        fixture=piv_23187_removal,
        disruption=disruption,
        modification=modification,
        rang_min=2,
        rang_max=3,
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
            assert db_trip_partial_removed.status == ModificationType.update.name
            assert db_trip_partial_removed.effect == TripEffect.REDUCED_SERVICE.name

            # 6 stop times must have been created
            assert len(db_trip_partial_removed.stop_time_updates) == 6

            # the first two stop have not been changed
            for s in db_trip_partial_removed.stop_time_updates[0:1]:
                assert s.arrival_status == ModificationType.none.name
                assert s.departure_status == ModificationType.none.name
                assert s.message is None

            # # the stops Mies and Genève Eaux-Vives should have been marked as deleted
            mies_st = db_trip_partial_removed.stop_time_updates[2]
            assert mies_st.stop_id == "stop_point:PIV:85010140:Train"  # Mies
            assert mies_st.arrival_status == ModificationType.delete.name
            assert mies_st.departure_status == ModificationType.delete.name

            gev_st = db_trip_partial_removed.stop_time_updates[3]
            assert gev_st.stop_id == "stop_point:PIV:85162735:Train"  # Genève Eaux-Vives
            assert gev_st.arrival_status == ModificationType.delete.name
            assert gev_st.departure_status == ModificationType.delete.name

            for s in db_trip_partial_removed.stop_time_updates[4:5]:
                assert s.arrival_status == ModificationType.none.name
                assert s.departure_status == ModificationType.none.name
                assert s.message is None

            assert db_trip_partial_removed.contributor_id == PIV_CONTRIBUTOR_ID

    assert mock_rabbitmq.call_count == 1


def test_piv_modification_limitation(mock_rabbitmq):
    """
    the trip 23187 is deleted except the first two stop
    """
    # Simple modification limitation
    piv_23187_removal = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="MODIFICATION_LIMITATION", texte="")
    _set_piv_disruption(piv_23187_removal, disruption=disruption)
    disruption = DisruptionTuple(type="SUPPRESSION_PARTIELLE", texte="")
    modification = ModificationTuple(motif="Absence inopinée d'un agent", statut=None)
    _set_event_on_stop(
        fixture=piv_23187_removal,
        disruption=disruption,
        dep_or_arr_key="depart",
        modification=modification,
        rang=1,
    )
    _set_event_on_stops(
        fixture=piv_23187_removal,
        disruption=disruption,
        modification=modification,
        rang_min=2,
        rang_max=5,
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
            assert db_trip_partial_removed.status == ModificationType.update.name
            assert db_trip_partial_removed.effect == TripEffect.REDUCED_SERVICE.name

            # 6 stop times must have been created
            assert len(db_trip_partial_removed.stop_time_updates) == 6

            # the first stop have not been changed
            first_st = db_trip_partial_removed.stop_time_updates[0]
            assert first_st.stop_id == "stop_point:PIV:85010231:Train"
            assert first_st.arrival_status == ModificationType.none.name
            assert first_st.departure_status == ModificationType.none.name
            assert first_st.message is None

            # the second stop's departure have been marked as deleted
            second_st = db_trip_partial_removed.stop_time_updates[1]
            assert second_st.arrival_status == ModificationType.none.name
            assert second_st.departure_status == ModificationType.delete.name
            assert second_st.message == "Absence inopinée d'un agent"

            for s in db_trip_partial_removed.stop_time_updates[2:5]:
                assert s.arrival_status == ModificationType.delete.name
                assert s.departure_status == ModificationType.delete.name
                assert s.message == "Absence inopinée d'un agent"

            assert db_trip_partial_removed.contributor_id == PIV_CONTRIBUTOR_ID

    assert mock_rabbitmq.call_count == 1


def test_piv_partial_back_ok_simple_case(mock_rabbitmq):
    """
    in the first case, all stops are delayed
    and then made up the delay for stops 3 to 5
    """
    piv_feed = _get_stomp_20201022_23187_delayed_5min_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))

    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_delayed_5min()
    assert mock_rabbitmq.call_count == 1

    retard = {"duree": 0, "dureeInterne": 0}
    disruption = DisruptionTuple(type="NORMAL", texte="Régulation du trafic")
    modification = ModificationTuple(motif="", statut=None)
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang_min=3,
        rang_max=5,
    )
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_partial_back_ok_simple()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_piv_partial_back_ok_complex_case(mock_rabbitmq):
    """
    in the first case, all stops are delayed
    and then made up the delay for stops 3 to 5
    but stops 4 and 5 have a 'dureeInterne'
    """

    piv_feed = _get_stomp_20201022_23187_delayed_5min_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))

    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_delayed_5min()
    assert mock_rabbitmq.call_count == 1

    piv_feed = _get_stomp_20201022_23187_partial_delayed_fixture()
    disruption = DisruptionTuple(type="NORMAL", texte=None)
    retard = {"duree": 0, "dureeInterne": 3}
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        retard_dict=retard,
        rang_min=4,
        rang_max=5,
    )
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_partial_delayed()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_piv_full_back_ok(mock_rabbitmq):
    """
    in the first case, all stops are delayed
    and then all stops back to normal by 'evenement' on trip
    """

    piv_feed = _get_stomp_20201022_23187_delayed_5min_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))

    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_delayed_5min()
    assert mock_rabbitmq.call_count == 1

    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="NORMAL", texte="")
    _set_piv_disruption(piv_feed, disruption=disruption)

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_normal()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_piv_full_back_ok_on_stops(mock_rabbitmq):
    """
    in the first case, all stops are delayed
    and then all stops back to normal by 'evenement' on trip and stops
    """
    piv_feed = _get_stomp_20201022_23187_delayed_5min_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))

    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_delayed_5min()
    assert mock_rabbitmq.call_count == 1

    piv_feed = get_fixture_data_as_dict("piv/stomp_20201022_23187_blank_fixture.json")
    disruption = DisruptionTuple(type="NORMAL", texte="")
    _set_piv_disruption(piv_feed, disruption=disruption)
    disruption = DisruptionTuple(type="NORMAL", texte=None)
    retard = {"duree": 0, "dureeInterne": 3}
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        retard_dict=retard,
        rang_min=0,
        rang_max=5,
    )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_stomp_20201022_23187_normal()
    # the rabbit mq has to have been called twice
    assert mock_rabbitmq.call_count == 2


def test_piv_added_stop_time_at_the_beginning(mock_rabbitmq):
    """
    new stop 0 is added with only departure
    new stop 1 is added after the stop 0
    arrival is added on stop 2 (previously 0)
    """
    piv_23187_addition = _get_stomp_20201022_23187_stop_time_added_at_the_beginning_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_addition))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.update.name
        assert db_trip.effect == TripEffect.MODIFIED_SERVICE.name
        assert db_trip.company_id == "company:PIVPP:1187"
        assert db_trip.physical_mode_id == "physical_mode:LongDistanceTrain"
        assert db_trip.headsign == "23187"
        assert len(db_trip.stop_time_updates) == 8

        added_st = db_trip.stop_time_updates[0]
        assert added_st.stop_id == "stop_point:PIV:85010116:Train"
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 20, 10)

        added_st = db_trip.stop_time_updates[1]
        assert added_st.stop_id == "stop_point:PIV:85010082:Train"
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 20, 22)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 20, 24)

        added_st = db_trip.stop_time_updates[2]
        assert added_st.stop_id == "stop_point:PIV:85010231:Train"
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 20, 32)
        assert added_st.departure_status == ModificationType.none.name
        assert added_st.departure == datetime(2020, 10, 22, 20, 34)

        for s in db_trip.stop_time_updates[3:]:
            assert s.arrival_status == ModificationType.none.name
            assert s.departure_status == ModificationType.none.name


def test_piv_added_stop_time_in_the_middle(mock_rabbitmq):
    """ "
    stop is inserted on rang 3
    """
    piv_23187_addition = _get_stomp_20201022_23187_stop_time_added_in_the_middle_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_addition))
    assert "PIV feed processed" in res.get("message")

    _assert_db_added_stop_time_in_the_middle()


def test_piv_added_stop_time_in_the_middle_and_delay(mock_rabbitmq):
    """ "
    stop is inserted on rang 3 and a delay of 1 h is applied on all
    (big enough to have stop's realtime datetime after next stop's base-schedule datetime)
    """
    piv_23187_addition = _get_stomp_20201022_23187_stop_time_added_in_the_middle_fixture()

    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="")
    modification = ModificationTuple(motif="Indisponibilité d'un matériel", statut=None)
    retard = {"duree": 60, "dureeInterne": 61}
    _set_event_on_stops(
        fixture=piv_23187_addition,
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang_min=1,
        rang_max=2,
    )
    _set_event_on_stops(
        fixture=piv_23187_addition,
        retard_dict=retard,
        rang_min=3,
        rang_max=3,
    )
    _set_event_on_stops(
        fixture=piv_23187_addition,
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang_min=4,
        rang_max=6,
    )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_addition))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.update.name
        assert db_trip.effect == TripEffect.MODIFIED_SERVICE.name
        assert db_trip.company_id == "company:PIVPP:1187"
        assert db_trip.physical_mode_id == "physical_mode:LongDistanceTrain"
        assert db_trip.headsign == "23187"
        assert len(db_trip.stop_time_updates) == 7

        normal_st = db_trip.stop_time_updates[0]
        assert normal_st.stop_id == "stop_point:PIV:85010231:Train"
        assert normal_st.departure_status == ModificationType.none.name
        assert normal_st.departure == datetime(2020, 10, 22, 20, 34)

        delay_st = db_trip.stop_time_updates[1]
        assert delay_st.stop_id == "stop_point:PIV:85010157:Train"
        assert delay_st.arrival_status == ModificationType.update.name
        assert delay_st.arrival == datetime(2020, 10, 22, 21, 35)
        assert delay_st.departure_status == ModificationType.update.name
        assert delay_st.departure == datetime(2020, 10, 22, 21, 35, 30)

        delay_st = db_trip.stop_time_updates[2]
        assert delay_st.stop_id == "stop_point:PIV:85010140:Train"
        assert delay_st.arrival_status == ModificationType.update.name
        assert delay_st.arrival == datetime(2020, 10, 22, 21, 47)
        assert delay_st.departure_status == ModificationType.update.name
        assert delay_st.departure == datetime(2020, 10, 22, 21, 48)

        added_st = db_trip.stop_time_updates[3]
        assert added_st.stop_id == "stop_point:PIV:85010082:Train"
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 22, 05)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 22, 07)

        delay_st = db_trip.stop_time_updates[4]
        assert delay_st.stop_id == "stop_point:PIV:85162735:Train"
        assert delay_st.arrival_status == ModificationType.update.name
        assert delay_st.arrival == datetime(2020, 10, 22, 22, 16)
        assert delay_st.departure_status == ModificationType.update.name
        assert delay_st.departure == datetime(2020, 10, 22, 22, 17)

        delay_st = db_trip.stop_time_updates[5]
        assert delay_st.stop_id == "stop_point:PIV:85162750:Train"
        assert delay_st.arrival_status == ModificationType.update.name
        assert delay_st.arrival == datetime(2020, 10, 22, 22, 19)
        assert delay_st.departure_status == ModificationType.update.name
        assert delay_st.departure == datetime(2020, 10, 22, 22, 20)

        delay_st = db_trip.stop_time_updates[6]
        assert delay_st.stop_id == "stop_point:PIV:87745497:Train"
        assert delay_st.arrival_status == ModificationType.update.name
        assert delay_st.arrival == datetime(2020, 10, 22, 22, 25)


def test_piv_added_stop_time_at_the_end(mock_rabbitmq):
    """
    departure is added on stop 5
    new stop 6 is added after the stop 5
    new stop 7 is added with only arrival
    """
    piv_23187_addition = _get_stomp_20201022_23187_stop_time_added_at_the_end_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_addition))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.update.name
        assert db_trip.effect == TripEffect.MODIFIED_SERVICE.name
        assert db_trip.company_id == "company:PIVPP:1187"
        assert db_trip.physical_mode_id == "physical_mode:LongDistanceTrain"
        assert db_trip.headsign == "23187"
        assert len(db_trip.stop_time_updates) == 8

        for s in db_trip.stop_time_updates[:4]:
            assert s.arrival_status == ModificationType.none.name
            assert s.departure_status == ModificationType.none.name

        added_st = db_trip.stop_time_updates[5]
        assert added_st.stop_id == "stop_point:PIV:87745497:Train"
        assert added_st.arrival_status == ModificationType.none.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 25)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 21, 26)

        added_st = db_trip.stop_time_updates[6]
        assert added_st.stop_id == "stop_point:PIV:85010082:Train"
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 30)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 21, 31)

        added_st = db_trip.stop_time_updates[7]
        assert added_st.stop_id == "stop_point:PIV:85010116:Train"
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 40)


def test_piv_added_stop_time_by_event(mock_rabbitmq):
    """
    stop is inserted on rang 3
    by event notification instead of statusModification
    """
    piv_23187_addition = _get_stomp_20201022_23187_stop_time_added_in_the_middle_fixture()
    modification = ModificationTuple(statut=None, motif=None)
    disruption = DisruptionTuple(type="CREATION", texte=None)
    _set_event_on_stops(
        fixture=piv_23187_addition,
        rang_min=3,
        rang_max=3,
        disruption=disruption,
        modification=modification,
    )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_addition))
    assert "PIV feed processed" in res.get("message")

    _assert_db_added_stop_time_in_the_middle()


def test_piv_re_routed_at_the_beginning(mock_rabbitmq):
    """
    old trip
       "85010231" - "85010157" - "85010140" - "85162735" - "85162750" - "87745497"

    new trip
                    "85010157" - "85010140" - "85162735" - "85162750" - "87745497"
                      //
    "85010116" - "85010082"
    """
    piv_23187_addition = _get_stomp_20201022_23187_stop_time_added_at_the_beginning_fixture()
    piv_23187_addition["objects"][0]["object"]["evenement"] = [
        {"type": "MODIFICATION_DETOURNEMENT", "texte": None}
    ]
    disruption = DisruptionTuple(type=None, texte=None)
    modification = ModificationTuple(statut="CREATION_DETOURNEMENT", motif=None)
    _set_event_on_stop(
        fixture=piv_23187_addition,
        dep_or_arr_key="depart",
        rang=0,
        disruption=disruption,
        modification=modification,
    )
    _set_event_on_stops(
        fixture=piv_23187_addition,
        rang_min=1,
        rang_max=1,
        disruption=disruption,
        modification=modification,
    )
    modification = ModificationTuple(statut=None, motif=None)
    disruption = DisruptionTuple(type="SUPPRESSION_DETOURNEMENT", texte=None)
    _set_event_on_stops(
        fixture=piv_23187_addition,
        rang_min=2,
        rang_max=2,
        disruption=disruption,
        modification=modification,
    )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_addition))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.update.name
        assert db_trip.effect == TripEffect.DETOUR.name
        assert len(db_trip.stop_time_updates) == 8

        added_st = db_trip.stop_time_updates[0]
        assert added_st.stop_id == "stop_point:PIV:85010116:Train"
        assert added_st.departure_status == ModificationType.added_for_detour.name
        assert added_st.departure == datetime(2020, 10, 22, 20, 10)

        added_st = db_trip.stop_time_updates[1]
        assert added_st.stop_id == "stop_point:PIV:85010082:Train"
        assert added_st.arrival_status == ModificationType.added_for_detour.name
        assert added_st.arrival == datetime(2020, 10, 22, 20, 22)
        assert added_st.departure_status == ModificationType.added_for_detour.name
        assert added_st.departure == datetime(2020, 10, 22, 20, 24)

        deleted_st = db_trip.stop_time_updates[2]
        assert deleted_st.stop_id == "stop_point:PIV:85010231:Train"
        assert deleted_st.arrival_status == ModificationType.deleted_for_detour.name
        assert deleted_st.departure_status == ModificationType.deleted_for_detour.name

        for s in db_trip.stop_time_updates[3:]:
            assert s.arrival_status == ModificationType.none.name
            assert s.departure_status == ModificationType.none.name


def test_piv_re_routed_in_the_middle(mock_rabbitmq):
    """
    old trip "85010231" - "85010157" - "85010082" - "85010140" - "85162735" - "85162750" - "87745497"

    new trip "85010231"                             "85010140" - "85162735" - "85162750" - "87745497"
                           \\                    //
                                 "85010082"
    """
    piv_23187_addition = _get_stomp_20201022_23187_stop_time_added_in_the_middle_fixture()
    piv_23187_addition["objects"][0]["object"]["evenement"] = [
        {"type": "MODIFICATION_DETOURNEMENT", "texte": None}
    ]
    disruption = DisruptionTuple(type=None, texte=None)
    modification = ModificationTuple(statut="SUPPRESSION_DETOURNEMENT", motif=None)
    _set_event_on_stops(
        fixture=piv_23187_addition,
        rang_min=1,
        rang_max=1,
        disruption=disruption,
        modification=modification,
    )
    disruption = DisruptionTuple(type=None, texte=None)
    modification = ModificationTuple(statut="SUPPRESSION_DETOURNEMENT", motif=None)
    _set_event_on_stops(
        fixture=piv_23187_addition,
        rang_min=2,
        rang_max=2,
        disruption=disruption,
        modification=modification,
    )

    disruption = DisruptionTuple(type=None, texte=None)
    modification = ModificationTuple(statut="CREATION_DETOURNEMENT", motif=None)
    _set_event_on_stops(
        fixture=piv_23187_addition,
        rang_min=3,
        rang_max=3,
        disruption=disruption,
        modification=modification,
    )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_addition))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.update.name
        assert db_trip.effect == TripEffect.DETOUR.name
        assert len(db_trip.stop_time_updates) == 7

        unchanged_st = db_trip.stop_time_updates[0]
        assert unchanged_st.stop_id == "stop_point:PIV:85010231:Train"
        assert unchanged_st.departure_status == ModificationType.none.name
        assert unchanged_st.departure == datetime(2020, 10, 22, 20, 34)

        deleted_st = db_trip.stop_time_updates[1]
        assert deleted_st.stop_id == "stop_point:PIV:85010157:Train"
        assert deleted_st.arrival_status == ModificationType.deleted_for_detour.name
        assert deleted_st.departure_status == ModificationType.deleted_for_detour.name

        deleted_st = db_trip.stop_time_updates[2]
        assert deleted_st.stop_id == "stop_point:PIV:85010140:Train"
        assert deleted_st.arrival_status == ModificationType.deleted_for_detour.name
        assert deleted_st.departure_status == ModificationType.deleted_for_detour.name

        added_st = db_trip.stop_time_updates[3]
        assert added_st.stop_id == "stop_point:PIV:85010082:Train"
        assert added_st.arrival_status == ModificationType.added_for_detour.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 05)
        assert added_st.departure_status == ModificationType.added_for_detour.name
        assert added_st.departure == datetime(2020, 10, 22, 21, 07)

        for s in db_trip.stop_time_updates[4:]:
            assert s.arrival_status == ModificationType.none.name
            assert s.departure_status == ModificationType.none.name


def test_piv_re_routed_in_the_end(mock_rabbitmq):
    """
    old trip
    "85010231" - "85010157" - "85010082" - "85010140" - "85162735" - "85162750" - "87745497" -

    new trip
    "85010231" - "85010157" - "85010082" - "85010140" - "85162735" - "851X62750"
                                                                            \\
                                                                        "85010082" - "85010116"
    """
    piv_23187_addition = _get_stomp_20201022_23187_stop_time_added_at_the_end_fixture()
    piv_23187_addition["objects"][0]["object"]["evenement"] = [
        {"type": "MODIFICATION_DETOURNEMENT", "texte": None}
    ]
    disruption = DisruptionTuple(type=None, texte=None)
    modification = ModificationTuple(statut="SUPPRESSION_DETOURNEMENT", motif=None)
    _set_event_on_stops(
        fixture=piv_23187_addition,
        rang_min=5,
        rang_max=5,
        disruption=disruption,
        modification=modification,
    )

    disruption = DisruptionTuple(type=None, texte=None)
    modification = ModificationTuple(statut="CREATION_DETOURNEMENT", motif=None)
    _set_event_on_stops(
        fixture=piv_23187_addition,
        rang_min=6,
        rang_max=7,
        disruption=disruption,
        modification=modification,
    )

    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_23187_addition))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.update.name
        assert db_trip.effect == TripEffect.DETOUR.name
        assert len(db_trip.stop_time_updates) == 8

        for s in db_trip.stop_time_updates[:4]:
            assert s.arrival_status == ModificationType.none.name
            assert s.departure_status == ModificationType.none.name

        deleted_st = db_trip.stop_time_updates[5]
        assert deleted_st.stop_id == "stop_point:PIV:87745497:Train"
        assert deleted_st.arrival_status == ModificationType.deleted_for_detour.name
        assert deleted_st.departure_status == ModificationType.deleted_for_detour.name

        added_st = db_trip.stop_time_updates[6]
        assert added_st.stop_id == "stop_point:PIV:85010082:Train"
        assert added_st.arrival_status == ModificationType.added_for_detour.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 30)
        assert added_st.departure_status == ModificationType.added_for_detour.name
        assert added_st.departure == datetime(2020, 10, 22, 21, 31)

        added_st = db_trip.stop_time_updates[7]
        assert added_st.stop_id == "stop_point:PIV:85010116:Train"
        assert added_st.arrival_status == ModificationType.added_for_detour.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 40)


def _assert_db_piv_trip_creation(delay=0):
    with app.app_context():
        assert RealTimeUpdate.query.count() == 1
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.add.name
        assert db_trip.effect == TripEffect.ADDITIONAL_SERVICE.name
        assert len(db_trip.stop_time_updates) == 6

        added_st = db_trip.stop_time_updates[0]
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 20, 34) + timedelta(minutes=delay)
        assert added_st.arrival_delay == timedelta(minutes=0)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 20, 34) + timedelta(minutes=delay)
        assert added_st.departure_delay == timedelta(minutes=0)

        added_st = db_trip.stop_time_updates[1]
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 20, 35) + timedelta(minutes=delay)
        assert added_st.arrival_delay == timedelta(minutes=0)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 20, 35, 30) + timedelta(minutes=delay)
        assert added_st.departure_delay == timedelta(minutes=0)

        added_st = db_trip.stop_time_updates[2]
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 20, 47) + timedelta(minutes=delay)
        assert added_st.arrival_delay == timedelta(minutes=0)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 20, 48) + timedelta(minutes=delay)
        assert added_st.departure_delay == timedelta(minutes=0)

        added_st = db_trip.stop_time_updates[3]
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 16) + timedelta(minutes=delay)
        assert added_st.arrival_delay == timedelta(minutes=0)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 21, 17) + timedelta(minutes=delay)
        assert added_st.departure_delay == timedelta(minutes=0)

        added_st = db_trip.stop_time_updates[4]
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 19) + timedelta(minutes=delay)
        assert added_st.arrival_delay == timedelta(minutes=0)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 21, 20) + timedelta(minutes=delay)
        assert added_st.departure_delay == timedelta(minutes=0)

        added_st = db_trip.stop_time_updates[5]
        assert added_st.arrival_status == ModificationType.add.name
        assert added_st.arrival == datetime(2020, 10, 22, 21, 25) + timedelta(minutes=delay)
        assert added_st.arrival_delay == timedelta(minutes=0)
        assert added_st.departure_status == ModificationType.add.name
        assert added_st.departure == datetime(2020, 10, 22, 21, 25) + timedelta(minutes=delay)
        assert added_st.departure_delay == timedelta(minutes=0)


def test_piv_trip_creation(mock_rabbitmq):
    piv_feed = _get_stomp_20201022_23188_add_trip_fixture(plan_transport_source="PTP")
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_piv_trip_creation()


def test_piv_trip_creation_delayed_0min(mock_rabbitmq):
    piv_feed = _get_stomp_20201022_23188_add_trip_fixture()
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent")
    modification = ModificationTuple(motif=None, statut=None)
    retard = {"duree": 0, "dureeInterne": 3}
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang_min=0,
        rang_max=5,
    )
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_piv_trip_creation()


def test_piv_trip_creation_delayed_5min(mock_rabbitmq):
    piv_feed = _get_stomp_20201022_23188_add_trip_fixture()
    disruption = DisruptionTuple(type="RETARD_PROJETE", texte="Absence inopinée d'un agent")
    modification = ModificationTuple(motif=None, statut=None)
    retard = {"duree": 5, "dureeInterne": 8}
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        modification=modification,
        retard_dict=retard,
        rang_min=0,
        rang_max=5,
    )
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_piv_trip_creation(delay=5)


def test_piv_trip_creation_by_ope_or_ptp(mock_rabbitmq):
    piv_feed = _get_stomp_20201022_23188_add_trip_fixture()
    _set_piv_disruption(piv_feed, disruption=None)
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    _assert_db_piv_trip_creation()


def test_piv_trip_creation_partial_remove(mock_rabbitmq):
    piv_feed = _get_stomp_20201022_23188_add_trip_fixture()
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")
    _assert_db_piv_trip_creation()

    piv_feed = _get_stomp_20201022_23188_add_trip_fixture()
    disruption = DisruptionTuple(type="SUPPRESSION_PARTIELLE", texte="")
    modification = ModificationTuple(motif=None, statut=None)
    _set_event_on_stops(
        fixture=piv_feed,
        disruption=disruption,
        modification=modification,
        rang_min=2,
        rang_max=4,
    )
    res = api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    assert "PIV feed processed" in res.get("message")

    with app.app_context():
        assert RealTimeUpdate.query.count() == 2
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.add.name
        assert db_trip.effect == TripEffect.ADDITIONAL_SERVICE.name
        assert len(db_trip.stop_time_updates) == 6

        st = db_trip.stop_time_updates[0]
        assert st.arrival_status == ModificationType.add.name
        assert st.arrival == datetime(2020, 10, 22, 20, 34)
        assert st.departure_status == ModificationType.add.name
        assert st.departure == datetime(2020, 10, 22, 20, 34)

        st = db_trip.stop_time_updates[1]
        assert st.arrival_status == ModificationType.add.name
        assert st.arrival == datetime(2020, 10, 22, 20, 35)
        assert st.departure_status == ModificationType.add.name
        assert st.departure == datetime(2020, 10, 22, 20, 35, 30)

        st = db_trip.stop_time_updates[2]
        assert st.arrival_status == ModificationType.delete.name
        assert st.arrival == datetime(2020, 10, 22, 20, 47)
        assert st.departure_status == ModificationType.delete.name
        assert st.departure == datetime(2020, 10, 22, 20, 48)

        st = db_trip.stop_time_updates[3]
        assert st.arrival_status == ModificationType.delete.name
        assert st.arrival == datetime(2020, 10, 22, 21, 16)
        assert st.departure_status == ModificationType.delete.name
        assert st.departure == datetime(2020, 10, 22, 21, 17)

        st = db_trip.stop_time_updates[4]
        assert st.arrival_status == ModificationType.delete.name
        assert st.arrival == datetime(2020, 10, 22, 21, 19)
        assert st.departure_status == ModificationType.delete.name
        assert st.departure == datetime(2020, 10, 22, 21, 20)

        st = db_trip.stop_time_updates[5]
        assert st.arrival_status == ModificationType.add.name
        assert st.arrival == datetime(2020, 10, 22, 21, 25)
        assert st.departure_status == ModificationType.add.name
        assert st.departure == datetime(2020, 10, 22, 21, 25)


def test_piv_trip_creation_wrong_company_and_physical_mode(mock_rabbitmq):
    piv_feed = _get_stomp_20201022_23188_add_trip_fixture()
    piv_feed["objects"][0]["object"]["operateur"]["codeOperateur"] = "1180"
    piv_feed["objects"][0]["object"]["modeTransport"]["typeMode"] = "FEROUTIER"
    api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))

    with app.app_context():
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.add.name
        assert db_trip.effect == TripEffect.ADDITIONAL_SERVICE.name
        assert db_trip.company_id == "company:PIVPP:1187"
        assert db_trip.physical_mode_id == "physical_mode:LongDistanceTrain"


def test_piv_trip_creation_existing_company_and_empty_physical_mode(mock_rabbitmq):
    piv_feed = _get_stomp_20201022_23188_add_trip_fixture()
    piv_feed["objects"][0]["object"]["operateur"]["codeOperateur"] = "1190"
    piv_feed["objects"][0]["object"]["modeTransport"]["typeMode"] = ""
    api_post("/piv/{}".format(PIV_CONTRIBUTOR_ID), data=ujson.dumps(piv_feed))
    with app.app_context():
        assert TripUpdate.query.count() == 1
        db_trip = TripUpdate.query.first()

        assert db_trip.status == ModificationType.add.name
        assert db_trip.effect == TripEffect.ADDITIONAL_SERVICE.name
        assert db_trip.company_id == "company:PIVPP:1190"
        assert db_trip.physical_mode_id == "physical_mode:LongDistanceTrain"
