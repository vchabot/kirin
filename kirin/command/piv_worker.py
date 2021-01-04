#!/usr/bin/env python
# coding=utf-8

# Copyright (c) 2020, Canal TP and/or its affiliates. All rights reserved.
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

from kirin import manager, app, new_relic
from kirin.core.model import db
from kirin.core.types import ConnectorType
from kirin.core.build_wrapper import wrap_build
from kirin.piv import KirinModelBuilder
from kirin.piv.piv import get_piv_contributors, get_piv_contributor

from kombu.mixins import ConsumerMixin
from kombu import Connection, Exchange, Queue
from datetime import datetime, timedelta
from copy import deepcopy
import logging
import time

from kirin.utils import log_exception, get_lock, make_kirin_lock_name
from kirin import redis_client
from redis.exceptions import LockError


logger = logging.getLogger(__name__)

CONF_RELOAD_INTERVAL = timedelta(
    seconds=float(str(app.config.get(str("BROKER_CONSUMER_CONFIGURATION_RELOAD_INTERVAL"))))
)
# In case of postgresql disconnection, it seems that the timeout (so exception) takes 10 seconds to happen,
# the lock expiration shall not occur during this time otherwise it can lead to incorrect state (still working but
# unlocked)
PIV_WORKER_REDIS_TIMEOUT_LOCK = max(
    timedelta(seconds=float(str(app.config.get(str("REDIS_LOCK_TIMEOUT_POLLER"))))), timedelta(seconds=15)
)
PIV_LOCK_NAME = make_kirin_lock_name("piv_poller")  # only one contributor and one PIV worker at a time


class PivWorker(ConsumerMixin):
    @new_relic.agent.background_task(name="piv_worker-init", group="Task")
    def __init__(self, contributor):
        if contributor.connector_type != ConnectorType.piv.value:
            raise ValueError(
                "Contributor '{0}': PivWorker requires type {1}".format(contributor.id, ConnectorType.piv.value)
            )
        if not contributor.is_active:
            raise ValueError(
                "Contributor '{0}': PivWorker requires an activated contributor.".format(contributor.id)
            )
        if not contributor.broker_url:
            raise ValueError("Missing 'broker_url' configuration for contributor '{0}'".format(contributor.id))
        if not contributor.exchange_name:
            raise ValueError(
                "Missing 'exchange_name' configuration for contributor '{0}'".format(contributor.id)
            )
        if not contributor.queue_name:
            raise ValueError("Missing 'queue_name' configuration for contributor '{0}'".format(contributor.id))
        self.last_config_checked_time = datetime.now()
        self.builder = KirinModelBuilder(contributor)
        # store config to spot configuration changes
        self.broker_url = deepcopy(contributor.broker_url)
        self.navitia_coverage = deepcopy(contributor.navitia_coverage)
        self.navitia_token = deepcopy(contributor.navitia_token)
        self.last_lock_update = datetime.now()

    @new_relic.agent.background_task(name="piv_worker-enter", group="Task")
    def __enter__(self):
        self.connection = Connection(self.builder.contributor.broker_url)
        self.exchange = self._get_exchange(self.builder.contributor.exchange_name)
        self.queue = self._get_or_create_queue(self.builder.contributor.queue_name)
        return self

    def __exit__(self, type, value, traceback):
        self.connection.release()

    def _get_exchange(self, exchange_name):
        return Exchange(name=exchange_name, type=str("fanout"), durable=True, no_declare=True, auto_delete=False)

    def _get_or_create_queue(self, queue_name):
        queue = Queue(name=queue_name, exchange=self.exchange, durable=True, auto_delete=False)
        queue.declare(channel=self.connection.channel())
        return queue

    def get_consumers(self, Consumer, channel):
        return [
            Consumer(
                queues=[self.queue],
                accept=["plain/text"],  # avoid deserializing to json dict
                prefetch_count=1,
                callbacks=[self.process_message],
            )
        ]

    @new_relic.agent.background_task(name="piv_worker-process_message", group="Task")
    def process_message(self, body, message):
        try:
            wrap_build(self.builder, body)
        except Exception as e:
            log_exception(e, "piv_worker")
        finally:
            # We might want to not acknowledge the message in case of error in the processing.
            # Not simple though as:
            # * it re-queues message as first to handle
            # * we do not want to process this message after another one (produced later) on the same train
            message.ack()

    @new_relic.agent.background_task(name="piv_worker-on_iteration", group="Task")
    def on_iteration(self):
        if datetime.now() - self.last_lock_update > PIV_WORKER_REDIS_TIMEOUT_LOCK // 5:
            redis_client.expire(PIV_LOCK_NAME, PIV_WORKER_REDIS_TIMEOUT_LOCK)
            self.last_lock_update = datetime.now()
            logger.debug("lock {%s} updated", PIV_LOCK_NAME)

        if datetime.now() - self.last_config_checked_time < CONF_RELOAD_INTERVAL:
            return

        # SQLAlchemy is not querying the DB for read (uses cache instead),
        # unless we specifically tell that the data is expired.
        db.session.expire(self.builder.contributor)
        self.last_config_checked_time = datetime.now()

        contributor = get_piv_contributor(self.builder.contributor.id)
        if (
            not contributor
            or contributor.broker_url != self.broker_url
            or contributor.navitia_coverage != self.navitia_coverage
            or contributor.navitia_token != self.navitia_token
            or contributor.exchange_name != self.exchange.name
            or contributor.queue_name != self.queue.name
        ):
            logger.info(
                "configuration of contributor '{0}' changed, let the worker die".format(
                    self.builder.contributor.id
                )
            )
            self.should_stop = True
            return


@manager.command
def piv_worker():
    # We assume a maximum of one PIV contributor is going to exist in the DB
    # Therefore, we enforce a maximum of one `PivWorker` is running at a time
    while True:
        try:
            with get_lock(logger, PIV_LOCK_NAME, PIV_WORKER_REDIS_TIMEOUT_LOCK.total_seconds()) as locked:
                if locked:
                    should_wait = True
                    contributor = None
                    try:
                        contributors = get_piv_contributors()
                        if len(contributors) == 0:
                            logger.warning("no PIV contributor")
                            time.sleep(CONF_RELOAD_INTERVAL.total_seconds())
                            continue
                        contributor = contributors[0]
                        if len(contributors) > 1:
                            logger.warning(
                                "more than one PIV contributors: {0}; choosing '{1}'".format(
                                    map(lambda c: c.id, contributors), contributor.id
                                )
                            )

                        with PivWorker(contributor) as worker:
                            should_wait = False  # wait only after init crash
                            logger.info("launching the PIV worker for '{0}'".format(contributor.id))
                            worker.run()

                    except Exception as e:
                        logger.warning("PIV worker died: {0}".format(e))
                    finally:
                        try:
                            db.session.commit()
                        except Exception as db_e:
                            logger.warning("Exception while db-commit: {0}".format(db_e))
                            db.session.rollback()
                        if should_wait:
                            time.sleep(CONF_RELOAD_INTERVAL.total_seconds())
                        if contributor is not None:
                            db.session.expire(
                                contributor
                            )  # force db-reload otherwise staying locked on previous contributor's config
                else:
                    logger.debug("Lock {%s} un-acquired", PIV_LOCK_NAME)
                    time.sleep(CONF_RELOAD_INTERVAL.total_seconds())
        except LockError as lock_e:
            logger.warning("Exception with lock : {0}".format(lock_e))
