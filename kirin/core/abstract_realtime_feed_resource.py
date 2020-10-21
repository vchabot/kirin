import flask
from flask_restful import Resource, abort
from kirin.core import model
from kirin.core.abstract_builder import wrap_build
from kirin.exceptions import InvalidArguments


def _get_request_data(connector_type, req):
    if not req.data:
        raise InvalidArguments("no {} data provided".format(connector_type.value))
    return req.data


class AbstractRealtimeFeedResource(Resource):
    def __init__(self, connector_type, kirin_model_builder):
        self.id = None
        self.connector_type = connector_type
        self.kirin_model_builder = kirin_model_builder

    def post(self):
        if self.id is None:
            abort(400, message="Contributor's id is missing")
        contributor = (
            model.Contributor.query_existing()
            .filter_by(id=self.id, connector_type=self.connector_type.value)
            .first()
        )
        if not contributor:
            abort(404, message="Contributor '{}' not found".format(self.id))
        wrap_build(
            self.kirin_model_builder(contributor), _get_request_data(self.connector_type, flask.globals.request)
        )
        return {"message": "{} feed processed".format(self.connector_type.value)}, 200
