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
from flask import url_for
from flask_restful import Resource

from kirin.core.abstract_ressource import AbstractRealtimeFeedResource
from kirin.core import model
from kirin.core.types import ConnectorType
from kirin.piv import KirinModelBuilder


def get_piv_contributors(include_deactivated=False):
    """
    :return: all PIV contributors from db (not configurable via file)
    """
    return model.Contributor.find_by_connector_type(
        ConnectorType.piv.value, include_deactivated=include_deactivated
    )


def get_piv_contributor(contributor_id):
    """
    :param contributor_id: Identifier of the contributor
    :return: The PIV contributor from DB corresponding to the input ID
    """
    # FIXME: Raise an exception if no contributor is found?
    contributors = [c for c in get_piv_contributors() if c.id == contributor_id]
    return contributors[0] if contributors else None


class PivIndex(Resource):
    def get(self):
        contributors = get_piv_contributors()

        if not contributors:
            return {"message": "No PIV contributor defined"}, 200

        response = {c.id: {"href": url_for("piv", id=c.id, _external=True)} for c in contributors}
        return response, 200


class Piv(AbstractRealtimeFeedResource):
    def post(self, id=None):
        self.connector_type = ConnectorType.piv
        self.id = id
        self.kirin_model_builder = KirinModelBuilder

        return AbstractRealtimeFeedResource.post(self)
