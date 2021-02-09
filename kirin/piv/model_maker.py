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

import logging
import datetime
from sys import maxint

import jmespath
import ujson
from operator import itemgetter

from kirin.core import model
from kirin.core.abstract_builder import AbstractKirinModelBuilder
from kirin.core.merge_utils import convert_nav_stop_list_to_stu_list
from kirin.core.model import StopTimeUpdate
from kirin.core.types import (
    ModificationType,
    TripEffect,
    get_higher_status,
    get_effect_by_stop_time_status,
    SIMPLE_MODIF_STATUSES,
    StopTimeEvent,
    DELETED_STATUSES,
)
from kirin.exceptions import InvalidArguments, UnsupportedValue, ObjectNotFound
from kirin.utils import make_rt_update, get_value, as_utc_naive_dt, record_internal_failure, as_duration

# The default company for PIV is SNCF (code '1187')
DEFAULT_COMPANY_CODE = "1187"
DEFAULT_PHYSICAL_MODE_ID = "physical_mode:LongDistanceTrain"
TRIP_PIV_ID_FORMAT = "PIV:REALTIME:{}"

STATUS_MAP = {"arrivee": "arrival_status", "depart": "departure_status"}
DELAY_MAP = {"arrivee": "arrival_delay", "depart": "departure_delay"}
STOP_EVENT_DATETIME_MAP = {"arrivee": "arrival", "depart": "departure"}
STOP_EVENT_MDI_MAP = {"arrivee": "indicateurDescenteInterdite", "depart": "indicateurMonteeInterdite"}


trip_piv_status_to_effect = {
    "SUPPRESSION": TripEffect.NO_SERVICE,
    "CREATION": TripEffect.ADDITIONAL_SERVICE,
    "MODIFICATION_DETOURNEMENT": TripEffect.DETOUR,
    "MODIFICATION_DESSERTE_SUPPRIMEE": TripEffect.REDUCED_SERVICE,
    "MODIFICATION_LIMITATION": TripEffect.REDUCED_SERVICE,
    "MODIFICATION_DESSERTE_AJOUTEE": TripEffect.MODIFIED_SERVICE,
    "MODIFICATION_PROLONGATION": TripEffect.MODIFIED_SERVICE,
    "RETARD": TripEffect.SIGNIFICANT_DELAYS,
    "NORMAL": TripEffect.UNKNOWN_EFFECT,
    "UNDEFINED": TripEffect.UNDEFINED,
}

trip_effect_order = {
    TripEffect.ADDITIONAL_SERVICE: 0,
    TripEffect.NO_SERVICE: 1,
    TripEffect.DETOUR: 2,
    TripEffect.REDUCED_SERVICE: 3,
    TripEffect.MODIFIED_SERVICE: 4,
    TripEffect.SIGNIFICANT_DELAYS: 5,
    TripEffect.UNKNOWN_EFFECT: 6,
    TripEffect.UNDEFINED: maxint,
}

MANAGED_EVENTS = [
    "RETARD",
    "SUPPRESSION",
    "MODIFICATION_DESSERTE_SUPPRIMEE",
    "MODIFICATION_LIMITATION",
    "MODIFICATION_DESSERTE_AJOUTEE",
    "MODIFICATION_PROLONGATION",
    "MODIFICATION_DETOURNEMENT",
    "CREATION",
    "NORMAL",
]
MANAGED_STOP_EVENTS = [
    "RETARD_OBSERVE",
    "RETARD_PROJETE",
    "SUPPRESSION_PARTIELLE",
    "SUPPRESSION_DETOURNEMENT",
    "CREATION",
    "CREATION_DETOURNEMENT",
    "NORMAL",
]


def _get_trip_effect_order_from_piv_status(status):
    return trip_effect_order.get(trip_piv_status_to_effect.get(status, TripEffect.UNDEFINED), maxint)


def _has_departure(stop):
    return get_value(stop, "depart", nullable=True) and not get_value(
        stop, "indicateurMonteeInterdite", nullable=True
    )


def _has_arrival(stop):
    return get_value(stop, "arrivee", nullable=True) and not get_value(
        stop, "indicateurDescenteInterdite", nullable=True
    )


def _retrieve_interesting_stops(list_ad):
    """
    Filter and sort "Arrets Desserte" (corresponding to stop_times in Navitia) to get only the relevant
    ones from a Navitia's perspective (stations, where travelers can hop on or hop off)
    :param list_ad: an array of "Arrets Desserte" (typically the one from the feed)
    :return: Filtered array
    Notes:  - written in a yield-fashion to switch implementation if possible, but we need random access for now
    """
    res = []
    picked_one = False
    sorted_list_stops = sorted(list_ad, key=itemgetter("rang"))
    for idx, stop in enumerate(sorted_list_stops):
        has_departure = _has_departure(stop)
        has_arrival = _has_arrival(stop)
        # At start, do not consume until there's a departure time (depart)
        if not picked_one and not has_departure:
            continue
        # exclude stop_times that have no departure nor arrival time (empty stop_times)
        if not has_departure and not has_arrival:
            continue
        # stop consuming once all following stop_times are missing arrival time
        # * if a stop_time only has departure time, travelers can only hop in, but if they are able to
        #   hop off later because some stop_time has arrival time then the current stop_time is useful,
        #   so we keep current stop_time.
        # * if no stop_time has arrival time anymore, then stop_times are useless as traveler cannot
        #   hop off, so no point hopping in anymore, so we remove all the stop_times until the end
        #   (should not happen in practice).
        if not has_arrival:
            has_following_arrival = any(_has_arrival(follow_stop) for follow_stop in sorted_list_stops[idx:])
            if not has_following_arrival:
                break

        picked_one = True
        res.append(stop)
    return res


def _is_fully_added_stop(ad):
    """
    Check if an arretDesserte object has any event that was present in base-schedule
    (and return the opposite boolean)
    """
    for event_toggle in ["arrivee", "depart"]:
        event = get_value(ad, event_toggle, nullable=True)
        if event:
            if get_value(event, "planTransportSource", nullable=True) not in ["OPE", "PTP"]:
                return False
    return True


def _get_first_stop_base_datetime(list_ads, hour_obj_name, skip_fully_added_stops=True):
    if skip_fully_added_stops:
        s = next((s for s in list_ads if not _is_fully_added_stop(s)), None)
    else:
        s = next((s for s in list_ads), None)

    str_time = get_value(get_value(s, hour_obj_name), "dateHeure") if s else None
    return as_utc_naive_dt(str_time) if str_time else None


def _make_navitia_empty_vj(piv_key):
    trip_id = TRIP_PIV_ID_FORMAT.format(piv_key)
    return {"id": "vehicle_journey:{}".format(trip_id), "trip": {"id": trip_id}}


def _extract_navitia_stop_time(uic8, nav_vj):
    # In base_schedule trip, searching for a stop_time at stop_point belonging to the right stop_area
    # stop_areas bear UIC8 code, while stop_point match one stop_area and one mode currently.
    nav_stop_times = jmespath.search(
        "stop_times[? stop_point.stop_area.codes[? value=='{uic8}' && type=='source']]".format(uic8=uic8),
        nav_vj,
    )

    log_dict = {}
    if not nav_stop_times:
        log_dict = {"log": "missing stop point", "stop_point_code": uic8}
        return None, log_dict

    if len(nav_stop_times) > 1:
        log_dict = {"log": "duplicate stops", "stop_point_code": uic8}

    return nav_stop_times[0], log_dict


def _is_stop_event_served(event_datetime, event_status, mdi):
    return event_datetime and not ((event_status and event_status in DELETED_STATUSES) or mdi)


def _get_message(arret):
    arrival_stop = get_value(arret, "arrivee", nullable=True)
    departure_stop = get_value(arret, "depart", nullable=True)
    motif = None
    if departure_stop:
        motif = get_value(departure_stop, "motifModification", nullable=True)
    if not motif and arrival_stop:
        motif = get_value(arrival_stop, "motifModification", nullable=True)
    if not motif and departure_stop:
        motif = departure_stop.get("evenement", {}).get("texte", None)
    if not motif and arrival_stop:
        motif = arrival_stop.get("evenement", {}).get("texte", None)
    return motif


class KirinModelBuilder(AbstractKirinModelBuilder):
    def __init__(self, contributor):
        super(KirinModelBuilder, self).__init__(contributor)

    def build_rt_update(self, input_raw):
        rt_update = make_rt_update(
            input_raw, connector_type=self.contributor.connector_type, contributor_id=self.contributor.id
        )
        log_dict = {}
        return rt_update, log_dict

    def build_trip_updates(self, rt_update):
        """
        parse the PIV raw json stored in the rt_update object (in Kirin db)
        and return a list of trip updates

        The TripUpdates are not associated with the RealTimeUpdate at this point
        """
        try:
            json = ujson.loads(rt_update.raw_data)
        except ValueError as e:
            raise InvalidArguments("invalid json: {}".format(e.message))

        dict_objects = get_value(json, "objects")
        json_train = get_value(dict_objects[0], "object")  # TODO: can we get more than 1 relevant in objects[]?
        piv_disruptions = get_value(json_train, "evenement", nullable=True)
        plan_transport_source = get_value(json_train, "planTransportSource", nullable=True)

        if not piv_disruptions and not plan_transport_source:
            raise InvalidArguments('No object "evenement" or "planTransportSource" available in feed provided')

        higher_trip_disruption = ujson.loads('{"type": "UNDEFINED", "texte": ""}')
        if piv_disruptions:
            for piv_disruption in piv_disruptions:
                piv_disruption_type = get_value(piv_disruption, "type", nullable=True)
                if (
                    piv_disruption_type
                    and piv_disruption_type in trip_piv_status_to_effect
                    and piv_disruption_type in MANAGED_EVENTS
                ):
                    higher_trip_disruption = (
                        piv_disruption
                        if _get_trip_effect_order_from_piv_status(piv_disruption_type)
                        < _get_trip_effect_order_from_piv_status(get_value(higher_trip_disruption, "type"))
                        else higher_trip_disruption
                    )
            if trip_piv_status_to_effect[higher_trip_disruption.get("type")] == TripEffect.UNDEFINED:
                raise UnsupportedValue("None of the disruption-types {} are supported".format(piv_disruptions))
        elif plan_transport_source:
            if plan_transport_source in ["PTP", "OPE"]:
                higher_trip_disruption = ujson.loads('{"type": "CREATION", "texte": ""}')
            else:
                raise UnsupportedValue("planTransportSource {} is not supported".format(plan_transport_source))

        json_train["evenement"] = higher_trip_disruption
        train_date = get_value(json_train, "dateCirculation")
        train_numbers = get_value(json_train, "numero")
        train_company = get_value(get_value(json_train, "operateur"), "codeOperateur")
        mode_dict = get_value(json_train, "modeTransport")
        train_mode = get_value(mode_dict, "codeMode")
        train_submode = get_value(mode_dict, "codeSousMode")
        train_typemode = get_value(mode_dict, "typeMode")
        piv_key = "{d}:{n}:{c}:{m}:{s}:{t}".format(
            d=train_date, n=train_numbers, c=train_company, m=train_mode, s=train_submode, t=train_typemode
        )

        list_ads = get_value(json_train, "listeArretsDesserte")
        ads = _retrieve_interesting_stops(get_value(list_ads, "arret"))
        if len(ads) < 2:
            raise InvalidArguments(
                'invalid json, "listeArretsDesserte/arret" has less than 2 valid stop_times in '
                "json elt {elt}".format(elt=ujson.dumps(json_train))
            )
        # replace by cleaned and sorted version of stoptimes list.
        json_train["listeArretsDesserte"]["arret"] = ads

        is_trip_addition = higher_trip_disruption.get("type") == "CREATION"
        vj = self._get_navitia_vj(piv_key, ads, is_trip_addition)

        trip_updates = [self._make_trip_update(json_train, vj)]

        log_dict = {}
        return trip_updates, log_dict

    def _get_navitia_vj(self, piv_key, ads, is_trip_addition):
        log = logging.LoggerAdapter(logging.getLogger(__name__), extra={str("contributor"): self.contributor.id})

        log.debug("searching for vj {} in navitia".format(piv_key))
        navitia_vjs = self.navitia.vehicle_journeys(
            q={
                "filter": 'vehicle_journey.has_code("rt_piv", "{}")'.format(piv_key),
                "depth": "2",  # we need this depth to get the stoptime's stop_area
                "show_codes": "true",  # we need the stop_areas codes
            }
        )

        if not navitia_vjs:
            # Last PIV information is always right, so if the VJ doesn't exist, it's an ADD (no matter feed content)
            navitia_vjs = [_make_navitia_empty_vj(piv_key)]

        vj = None
        if len(navitia_vjs) != 1:
            log.info("Can not match a unique train for key {}".format(piv_key))
            record_internal_failure("no unique train", contributor=self.contributor.id)
        else:
            navitia_vj = navitia_vjs[0]
            try:
                base_vs_rt_error_margin = datetime.timedelta(hours=1)
                vj_base_start = _get_first_stop_base_datetime(
                    ads, "depart", skip_fully_added_stops=not is_trip_addition
                )
                vj = model.VehicleJourney(
                    navitia_vj,
                    vj_base_start - base_vs_rt_error_margin,
                    vj_base_start + base_vs_rt_error_margin,
                    vj_start_dt=vj_base_start,
                )
            except Exception as e:
                log.exception("Error while creating kirin VJ of {}: {}".format(navitia_vjs[0].get("id"), e))
                record_internal_failure("Error while creating kirin VJ", contributor=self.contributor.id)

        if not vj:
            raise ObjectNotFound("no train found for key {}".format(piv_key))
        return vj

    def _make_trip_update(self, json_train, vj):
        trip_update = model.TripUpdate(vj=vj, contributor_id=self.contributor.id)
        trip_update.headsign = json_train.get("numero")
        company_id = json_train.get("operateur").get("codeOperateur")
        trip_update.company_id = self._get_navitia_company_id(company_id)
        physical_mode = json_train.get("modeTransport").get("typeMode")
        trip_update.physical_mode_id = self._get_navitia_physical_mode_id(physical_mode)
        trip_status_type = trip_piv_status_to_effect[json_train.get("evenement").get("type")]
        trip_update.message = json_train.get("evenement").get("texte")
        trip_update.effect = trip_status_type.name
        if trip_status_type == TripEffect.NO_SERVICE:
            # the whole trip is deleted
            trip_update.status = ModificationType.delete.name
            trip_update.stop_time_updates = []
            return trip_update
        elif trip_status_type == TripEffect.ADDITIONAL_SERVICE:
            trip_update.status = ModificationType.add.name
        else:
            trip_update.status = ModificationType.update.name

        highest_st_status = ModificationType.none.name
        ads = get_value(get_value(json_train, "listeArretsDesserte"), "arret")

        # this variable is used to memoize the last stop_time's departure in order to check the stop_time consistency
        # ex. stop_time[i].arrival/departure must be greater than stop_time[i-1].departure
        previous_rt_stop_event_time = datetime.datetime.fromtimestamp(0)
        for arret in ads:
            # retrieve navitia's stop_point corresponding to the current PIV ad
            nav_stop, log_dict = self._get_navitia_stop_point(arret, vj.navitia_vj)

            if log_dict:
                record_internal_failure(log_dict["log"], contributor=self.contributor.id)
                log_dict.update({"contributor": self.contributor.id})
                logging.getLogger(__name__).info("metrology", extra=log_dict)

            if nav_stop is None:
                continue  # simply skip stop_times at unknown stop areas

            st_update = model.StopTimeUpdate(nav_stop)
            trip_update.stop_time_updates.append(st_update)

            st_update.message = _get_message(arret)
            for event_toggle in ["arrivee", "depart"]:
                event = get_value(arret, event_toggle, nullable=True)
                if event is None:
                    continue

                piv_disruption = event.get("evenement", {})
                piv_event_status = event.get("statutModification", piv_disruption.get("type", None))

                if not piv_event_status or piv_event_status in MANAGED_STOP_EVENTS:
                    piv_event_datetime = get_value(event, "dateHeureReelle", nullable=True)
                    event_datetime = as_utc_naive_dt(piv_event_datetime) if piv_event_datetime else None
                    if event_datetime:
                        setattr(st_update, STOP_EVENT_DATETIME_MAP[event_toggle], event_datetime)

                    if piv_event_status in ["SUPPRESSION_PARTIELLE"]:
                        setattr(st_update, STATUS_MAP[event_toggle], ModificationType.delete.name)
                    elif piv_event_status in ["SUPPRESSION_DETOURNEMENT"]:
                        setattr(st_update, STATUS_MAP[event_toggle], ModificationType.deleted_for_detour.name)
                    elif piv_event_status in ["CREATION"]:
                        setattr(st_update, STATUS_MAP[event_toggle], ModificationType.add.name)
                    elif piv_event_status in ["CREATION_DETOURNEMENT"]:
                        setattr(st_update, STATUS_MAP[event_toggle], ModificationType.added_for_detour.name)
                    elif trip_status_type == TripEffect.ADDITIONAL_SERVICE:
                        # In this case we want to create schedules taking into account the delay
                        # without adding the delay a second time
                        setattr(st_update, STATUS_MAP[event_toggle], ModificationType.add.name)
                    elif piv_event_status in ["RETARD_OBSERVE", "RETARD_PROJETE"]:
                        setattr(st_update, STATUS_MAP[event_toggle], ModificationType.update.name)
                        if piv_disruption:
                            piv_event_delay = piv_disruption.get("retard", {}).get("duree", 0)
                            setattr(
                                st_update, DELAY_MAP[event_toggle], as_duration(piv_event_delay * 60)
                            )  # minutes
                    else:
                        setattr(st_update, STATUS_MAP[event_toggle], ModificationType.none.name)
                    # otherwise let those be none

                else:
                    raise InvalidArguments(
                        "invalid value {s} for field {t}/statutModification or {t}/evenement/type".format(
                            s=piv_event_status, t=event_toggle
                        )
                    )

                event_status = getattr(st_update, STATUS_MAP[event_toggle], ModificationType.none.name)
                highest_st_status = get_higher_status(highest_st_status, event_status)

                mdi = (
                    arret[STOP_EVENT_MDI_MAP[event_toggle]]
                    if arret.get(STOP_EVENT_MDI_MAP[event_toggle])
                    else False
                )
                if _is_stop_event_served(event_datetime, event_status, mdi):
                    if previous_rt_stop_event_time > event_datetime:
                        raise InvalidArguments(
                            "invalid feed: stop_point's({}) time is not consistent".format(
                                get_value(get_value(arret, "emplacement"), "code")
                            )
                        )
                    previous_rt_stop_event_time = event_datetime

        # Calculates effect from stop_time status list (this work is also done in kraken and has to be deleted there)
        if trip_update.effect == TripEffect.MODIFIED_SERVICE.name:
            trip_update.effect = get_effect_by_stop_time_status(highest_st_status)
        return trip_update

    def _get_navitia_company_id(self, code):
        """
        Get a navitia company for the given code
        """
        company = self._request_navitia_company(code)
        if not company:
            company = self._request_navitia_company(DEFAULT_COMPANY_CODE)
            if not company:
                raise ObjectNotFound(
                    "no company found for key {}, nor for the default company {}".format(
                        code, DEFAULT_COMPANY_CODE
                    )
                )
        return company.get("id", None) if company else None

    def _request_navitia_company(self, code):
        companies = self.navitia.companies(
            q={"filter": 'company.has_code("source", "{}")'.format(code), "count": "1"}
        )
        return companies[0] if companies else None

    def _get_navitia_physical_mode_id(self, indicator=None):
        """
        Get a navitia physical_mode for the codes present in PIV (FERRE / ROUTIER)
        """
        uri = {
            "FERRE": "physical_mode:LongDistanceTrain",
            "ROUTIER": "physical_mode:Coach",
        }.get(indicator, DEFAULT_PHYSICAL_MODE_ID)
        physical_mode = self._request_navitia_physical_mode(uri)
        return physical_mode.get("id", None) if physical_mode else None

    def _request_navitia_physical_mode(self, uri):
        physical_modes = self.navitia.physical_modes(uri=uri)
        return physical_modes[0] if physical_modes else None

    def _get_navitia_stop_point(self, arret, nav_vj):
        """
        Get a navitia stop point from the stop_time in a 'Point de Parcours' dict.
        The dict MUST contain cr, ci, ch tags.
        It searches in the vj's stops for a stop_area with the external code cr-ci-ch

        If the stop_time isn't found in the vj, in case of an additional stop_time,
        a request is made to Navitia.

        Error messages are also returned as 'missing stop point', 'duplicate stops'
        """
        uic8 = get_value(get_value(arret, "emplacement"), "code")
        nav_st, log_dict = _extract_navitia_stop_time(uic8, nav_vj)
        if not nav_st:
            nav_stop, req_log_dict = self._request_navitia_stop_point(uic8)
            log_dict.update(req_log_dict)
        else:
            nav_stop = nav_st.get("stop_point", None)
        return nav_stop, log_dict

    def _request_navitia_stop_point(self, uic8):
        stop_points = self.navitia.stop_points(
            q={"filter": 'stop_area.has_code("source", "{}")'.format(uic8), "count": "1"}
        )
        if stop_points:
            return stop_points[0], {}

        return None, {"log": "No stop point found", "stop_point_code": uic8}

    def merge_trip_updates(self, navitia_vj, db_trip_update, new_trip_update):
        """
        Steps:
        1. Build TripUpdate info resulting of merge
        2. Adjust TripUpdate info to have it be self-consistent (call adjust_trip_update_consistency())
        3. If resulting TripUpdate info are new compared to the one from previous RT info: send it

        NB:
        * Working with ORM objects directly: no persistence of new object before knowing it's final version and
          it's not a duplicate (performance and db unicity constraints)
        """
        res_stus = []  # final list of StopTimeUpdates (to be attached to res in the end)

        circulation_date = new_trip_update.vj.get_circulation_date()
        # last info known about stops in trip (before processing new feed):
        # either from previous RT feed or base-schedule
        old_stus = (
            db_trip_update.stop_time_updates
            if db_trip_update
            else convert_nav_stop_list_to_stu_list(navitia_vj.get("stop_times", []), circulation_date)
        )

        # build resulting STU list
        old_stus_unprocessed_start = 0  # keeps track of what was processed in previous STU info

        for new_order, new_stu in enumerate(new_trip_update.stop_time_updates):
            index_new_stu = new_order if new_order + 1 != len(new_trip_update.stop_time_updates) else -1

            # find corresponding stop in last known information
            match_old_stu_order, match_old_stu = find_enumerate_stu_in_stus(
                new_stu, old_stus, start=old_stus_unprocessed_start
            )
            index_old_stu = match_old_stu_order if match_old_stu_order + 1 != len(old_stus) else -1

            # If new stop-time is not added, or if it is and was already added in last known information
            # Progress on stop-times from last-known information and process them
            if not new_stu.is_fully_added(index=index_new_stu) or (
                match_old_stu is not None and match_old_stu.is_fully_added(index=index_old_stu)
            ):
                # Populate with old stops not found in new feed (the ones skipped to find current new_stu)
                populate_res_stus_with_unfound_old_stus(
                    res_stus=res_stus,
                    old_stus=old_stus,
                    unfound_start=old_stus_unprocessed_start,
                    unfound_end=match_old_stu_order,
                )
                # Remember progress (to avoid matching the same old stop-time for 2 different new stop-times)
                old_stus_unprocessed_start = match_old_stu_order + 1

            # mark new stop-time as added if it was not known previously
            # Ex: a "delay" on a stop that doesn't exist in base-schedule is actually an "add"
            if match_old_stu is None:
                if new_stu.arrival_status in SIMPLE_MODIF_STATUSES:
                    new_stu.arrival_status = ModificationType.add.name
                if new_stu.departure_status in SIMPLE_MODIF_STATUSES:
                    new_stu.departure_status = ModificationType.add.name

            if new_stu.departure_delay is None:
                new_stu.departure_delay = datetime.timedelta(0)
            if new_stu.arrival_delay is None:
                new_stu.arrival_delay = datetime.timedelta(0)
            # add stop currently processed
            # GOTCHA: need a StopTimeUpdate detached of new_trip_update to avoid persisting new_trip_update
            # (this would lead to 2 TripUpdates for the same trip on the same day, forbidden by unicity constraint)
            res_stus.append(
                StopTimeUpdate(
                    navitia_stop=new_stu.navitia_stop,
                    departure=new_stu.departure,
                    arrival=new_stu.arrival,
                    departure_delay=new_stu.departure_delay,
                    arrival_delay=new_stu.arrival_delay,
                    dep_status=new_stu.departure_status,
                    arr_status=new_stu.arrival_status,
                    message=new_stu.message,
                    order=len(res_stus),
                )
            )
        # Finish populating with old stops not found in new feed
        # (the ones at the end after searching stops from new feed, if any)
        populate_res_stus_with_unfound_old_stus(
            res_stus=res_stus,
            old_stus=old_stus,
            unfound_start=old_stus_unprocessed_start,
            unfound_end=len(old_stus),
        )

        # if navitia_vj is empty, it's a creation
        if not navitia_vj.get("stop_times", []):
            new_trip_update.effect = TripEffect.ADDITIONAL_SERVICE.name
            new_trip_update.status = ModificationType.add.name

        # adjust consistency for resulting trip_update
        adjust_trip_update_consistency(new_trip_update, res_stus)

        # return result only if there are changes
        if not trip_updates_are_equal(
            left_tu=db_trip_update,
            left_stus=db_trip_update.stop_time_updates if db_trip_update else None,
            right_tu=new_trip_update,
            right_stus=res_stus,
        ):
            # update existing TripUpdate to avoid duplicates, keep created_at and
            # preserve performance (ORM objects are heavy)
            res = db_trip_update if db_trip_update else new_trip_update
            res.message = new_trip_update.message
            res.status = new_trip_update.status
            res.effect = new_trip_update.effect
            res.physical_mode_id = new_trip_update.physical_mode_id
            res.headsign = new_trip_update.headsign
            res.stop_time_updates = res_stus
            return res
        else:
            return None


def lists_stu_are_equal(left, right):
    """
    Compares 2 lists of StopTimeUpdates
    :return: True if lists are equals, False otherwise
    """
    if len(right) != len(left):
        return False
    for i in range(0, len(left)):
        if not left[i].is_equal(right[i]):
            return False
    return True


def trip_updates_are_equal(left_tu, left_stus, right_tu, right_stus):
    """
    Compares 2 TripUpdates (StopTimeUpdate list are provided separately)
    :param left_tu: TripUpdate containing attributes for left
    :param left_stus: list of StopTimeUpdates for left
    :param right_tu: TripUpdate containing attributes for right
    :param right_stus: list of StopTimeUpdates for right
    :return: True if TripUpdates are equal, False otherwise
    """
    # If only one of the two is None or empty, they're not equal
    if bool(left_tu) != bool(right_tu):
        return False
    return (
        left_tu.message == right_tu.message
        and left_tu.status == right_tu.status
        and left_tu.effect == right_tu.effect
        and left_tu.physical_mode_id == right_tu.physical_mode_id
        and left_tu.headsign == right_tu.headsign
        and lists_stu_are_equal(left_stus, right_stus)
    )


def find_enumerate_stu_in_stus(ref_stu, stus, start=0):
    """
    Find a stop_time in the navitia vehicle journey
    :param ref_stu: the referent stop_time
    :param stus: list of StopTimeUpdate available in a TripUpdate
    :param start: order (comprised) to start the search from
    :return: (order, stu) if found else (len(stus), None)
    """
    if start >= len(stus):
        return len(stus), None

    def same_stop(stu, ref):
        return stu.stop_id == ref.stop_id

    return next(((order, stu) for (order, stu) in enumerate(stus) if same_stop(stu, ref_stu)), (len(stus), None))


def fill_missing_stop_event_dt(stu, stop_event, previous_stop_event_dt):
    stop_event_dt = getattr(stu, stop_event.name)
    if stop_event_dt is None:
        # if info is missing, first consider the opposite event of same stop-time
        stop_event_dt = getattr(stu, stop_event.opposite().name, datetime.datetime.min)
        if stop_event_dt is None and previous_stop_event_dt != datetime.datetime.min:
            # if info is still missing, consider last known stop-time event if info exists
            stop_event_dt = previous_stop_event_dt
        setattr(stu, stop_event.name, stop_event_dt)


def adjust_stop_event_in_time(stu, stop_event, previous_stop_event_dt):
    """
    Compare current stop_event datetime with previous stop_event, and push it to be at the same time if it is earlier
    :param stu: StopTimeUpdate to containing the event to consider
    :param stop_event: StopEvent to consider
    :param previous_stop_event_dt: datetime of the previous StopEvent
    :return:
    """
    stop_event_dt = getattr(stu, stop_event.name)
    # If not time-sorted
    if previous_stop_event_dt > stop_event_dt:
        # Adjust delay and status: do not affect deleted and added events
        if stu.get_stop_event_status(stop_event.name) in SIMPLE_MODIF_STATUSES:
            stop_event_delay = getattr(stu, "{}_delay".format(stop_event.name), datetime.timedelta(0))
            setattr(
                stu,
                "{}_delay".format(stop_event.name),
                stop_event_delay + (previous_stop_event_dt - stop_event_dt),
            )
            setattr(stu, "{}_status".format(stop_event.name), ModificationType.update.name)
        # Adjust datetime
        setattr(stu, stop_event.name, previous_stop_event_dt)


def adjust_stop_event_consistency(stu, stop_event, previous_stop_event_dt):
    """
    Adjust info for given stop-event to have it consistent with surrounding stop-events
    :param stu: StopTimeUpdate to containing the event to consider
    :param stop_event: StopEvent to consider
    :param previous_stop_event_dt: datetime of the previous StopEvent
    :return: datetime of stop-event considered once adjusted
    """
    # First fill missing datetime info
    fill_missing_stop_event_dt(stu, stop_event, previous_stop_event_dt)

    # Check time consistency: chaining of served stop_events must be time-sorted.
    # Push to the same time than previous event to respect it if needed.
    if not stu.is_stop_event_deleted(stop_event.name):
        adjust_stop_event_in_time(stu, stop_event, previous_stop_event_dt)

        # update previous event's info for next event's management
        final_stop_event_dt = getattr(stu, stop_event.name)
        if final_stop_event_dt is not None:
            return final_stop_event_dt
    return previous_stop_event_dt


def adjust_trip_update_consistency(trip_update, stus):
    """
    Adjust consistency of TripUpdate and StopTimeUpdates (side-effect).
    NB : using list of StopTimeUpdate provided in stus, as the list is often not
    attached to TripUpdate (to avoid ORM persiting it)
    :param trip_update: TripUpdate to adjust (all but stop_time_updates)
    :param stus: List of StopTimeUpdates to adjust
    :return: None, just update given parameters
    """
    previous_stop_event_dt = datetime.datetime.min
    for res_stu in stus:
        # if trip is added: stops are either added or deleted (both can be for detour) + no delay
        if trip_update.effect == TripEffect.ADDITIONAL_SERVICE.name:
            if res_stu.arrival_status in SIMPLE_MODIF_STATUSES:
                res_stu.arrival_status = ModificationType.add.name
                res_stu.arrival_delay = datetime.timedelta(0)
            if res_stu.departure_status in SIMPLE_MODIF_STATUSES:
                res_stu.departure_status = ModificationType.add.name
                res_stu.departure_delay = datetime.timedelta(0)

        # adjust stop-events considering stop-events surrounding them
        previous_stop_event_dt = adjust_stop_event_consistency(
            res_stu, StopTimeEvent.arrival, previous_stop_event_dt
        )
        previous_stop_event_dt = adjust_stop_event_consistency(
            res_stu, StopTimeEvent.departure, previous_stop_event_dt
        )


def populate_res_stus_with_unfound_old_stus(res_stus, old_stus, unfound_start, unfound_end):
    # Get old stops not found, mark them as deleted and populate res_stus
    # Ex: a stop-time in base-schedule that is not known in PIV feed is actually deleted
    for old_order in range(unfound_start, unfound_end):
        del_stu = old_stus[old_order]
        del_stu.arrival_status = ModificationType.delete.name
        del_stu.departure_status = ModificationType.delete.name
        del_stu.order = len(res_stus)
        res_stus.append(del_stu)
