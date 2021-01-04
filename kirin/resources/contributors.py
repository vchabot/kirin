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
import flask
import jsonschema
import sqlalchemy
from flask_restful import Resource, marshal_with, fields, abort
from kirin.core import model
from kirin.core.types import ConnectorType
from kirin.utils import db_commit

contributor_fields = {
    "id": fields.String,
    "navitia_coverage": fields.String,
    "navitia_token": fields.String,
    "feed_url": fields.String,
    "connector_type": fields.String,
    "retrieval_interval": fields.Integer,
    "is_active": fields.Boolean,
    "broker_url": fields.String,
    "exchange_name": fields.String,
    "queue_name": fields.String,
    "nb_days_to_keep_trip_update": fields.Integer,
    "nb_days_to_keep_rt_update": fields.Integer,
}

contributors_list_fields = {"contributors": fields.List(fields.Nested(contributor_fields))}
contributor_nested_fields = {"contributor": fields.Nested(contributor_fields)}


class Contributors(Resource):

    schema_properties = {
        "id": {"type": "string"},
        "navitia_coverage": {"type": "string"},
        "navitia_token": {"type": ["string", "null"]},
        "feed_url": {"type": ["string", "null"], "format": "uri"},
        "connector_type": {"type": "string", "enum": ConnectorType.values()},
        "retrieval_interval": {"type": ["integer", "null"], "minimum": 1},
        "is_active": {"type": "boolean"},
        "broker_url": {"type": ["string", "null"], "format": "uri"},
        "exchange_name": {"type": ["string", "null"]},
        "queue_name": {"type": ["string", "null"]},
        "nb_days_to_keep_trip_update": {"type": ["integer", "null"]},
        "nb_days_to_keep_rt_update": {"type": ["integer", "null"]},
    }

    post_data_schema = {
        "type": "object",
        "properties": schema_properties,
        "required": [
            "navitia_coverage",
            "connector_type",
        ],
    }

    put_data_schema = {"type": "object", "properties": schema_properties}

    @marshal_with(contributors_list_fields)
    def get(self, id=None):
        if id is not None:
            contrib = model.Contributor.query.get_or_404(id)
            return {"contributors": [contrib]}

        return {"contributors": model.Contributor.query_existing().all()}

    @marshal_with(contributor_nested_fields)
    def post(self, id=None):
        data = flask.request.get_json()

        if data is None:
            abort(400, message="No posted Json data to create a contributor")

        try:
            jsonschema.validate(data, self.post_data_schema)
        except jsonschema.exceptions.ValidationError as e:
            abort(400, message="Failed to validate posted Json data. Error: {}".format(e))

        id = id or data.get("id")
        token = data.get("navitia_token", None)
        feed_url = data.get("feed_url", None)
        retrieval_interval = data.get("retrieval_interval", 10)
        is_active = data.get("is_active", True)
        broker_url = data.get("broker_url", None)
        exchange_name = data.get("exchange_name", None)
        queue_name = data.get("queue_name", None)
        nb_days_to_keep_trip_update = data.get("nb_days_to_keep_trip_update")
        nb_days_to_keep_rt_update = data.get("nb_days_to_keep_rt_update")
        if any([broker_url, exchange_name, queue_name]) and not all([broker_url, exchange_name, queue_name]):
            abort(
                400,
                message="'broker_url', 'exchange_name' and 'queue_name' must all be provided or none of them.",
            )

        try:
            new_contrib = model.Contributor(
                id,
                data["navitia_coverage"],
                data["connector_type"],
                token,
                feed_url,
                retrieval_interval,
                is_active,
                broker_url,
                exchange_name,
                queue_name,
                nb_days_to_keep_trip_update,
                nb_days_to_keep_rt_update,
            )
            db_commit(new_contrib)
            return {"contributor": new_contrib}, 201
        except KeyError as e:
            err_msg = "Missing attribute '{}' in input data to construct a contributor".format(e)
            abort(400, message=err_msg)
        except sqlalchemy.exc.SQLAlchemyError as e:
            abort(400, message="Error while creating contributor - {}".format(e))

    @marshal_with(contributor_nested_fields)
    def put(self, id=None):
        data = flask.request.get_json()

        if data is None:
            abort(400, message="No Json data found to update a contributor")

        try:
            jsonschema.validate(data, self.put_data_schema)
        except jsonschema.exceptions.ValidationError as e:
            abort(400, message="Failed to validate posted Json data. Error: {}".format(e))

        id = id or data.get("id")
        if id is None:
            abort(400, message="Contributor's id is missing")

        try:
            # As we should not update id, delete it from data if exists
            if "id" in data:
                del data["id"]

            model.Contributor.query.filter(model.Contributor.id == id).update(data)
            model.db.session.commit()
            contributor = model.Contributor.query.get_or_404(id)
            return {"contributor": contributor}, 200
        except sqlalchemy.exc.SQLAlchemyError as e:
            abort(400, message=e)

    def delete(self, id=None):
        if id is None:
            abort(400, message="Contributor's id is missing")

        contributor = model.Contributor.query_existing().filter_by(id=id).first_or_404()
        try:
            contributor.is_active = False
            model.db.session.commit()
        except sqlalchemy.exc.SQLAlchemyError as e:
            abort(400, message=e)

        return None, 204
