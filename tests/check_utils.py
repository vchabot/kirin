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
import os
from kirin import app
import json
from dateutil.parser import parse


def api_get(url, check=True, *args, **kwargs):
    """
    call api, check response status code, and return json as dict
    """
    tester = app.test_client()
    resp = tester.get(url, *args, **kwargs)

    if check:
        assert resp.status_code == 200
        return _to_json(resp.data)
    else:
        return _to_json(resp.data), resp.status_code


def api_post(url, check=True, *args, **kwargs):
    """
    call api, check response status code, and return json as dict
    """
    tester = app.test_client()
    resp = tester.post(url, *args, **kwargs)

    if check:
        assert resp.status_code == 200
        return _to_json(resp.data)
    else:
        return _to_json(resp.data), resp.status_code


def _to_json(data):
    assert data
    json_response = json.loads(data)

    return json_response


def get_fixture_data(name):
    """
    return a fixture input (IRE, COTS) as string
    the name must be the name of a file in kirin/tests/fixtures
    """
    f = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(f, "r") as fixture:
        return fixture.read().decode("utf_8")


def get_fixture_data_as_dict(name):
    return _to_json(get_fixture_data(name))


def _dt(dt_to_parse, year=2015, month=9, day=8):
    """
    small helper to ease the reading of the tests
    >>> _dt("8:15")
    datetime.datetime(2015, 9, 8, 8, 15)
    >>> _dt("9:15", day=2)
    datetime.datetime(2015, 9, 2, 9, 15)
    """
    d = parse(dt_to_parse)
    return d.replace(year=year, month=month, day=day)
