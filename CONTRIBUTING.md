# Contributing

## Code guidelines

### Naive UTC datetimes

All datetimes in Kirin **must** be :

* **UTC** : datetimes are converted to UTC as soon as possible
* **naive** : no information about timezone is stored inside (making the UTC timezone implicit).\
  To have a timezone-naive datetime, its `tzinfo` attribute must be `None`.

_Why?_\
Simplest way to always manipulate the same kind of datetimes,
being able to compare them, no matter the source.\
Especially as the database does not store/provide timezone-aware datetimes.

_Where?_\
Everywhere.\
So a naive-UTC-datetime is produced right after it's read,
and timezone info is added as late as possible when needed (ex: when requesting external service).\
When absolutely needed, datetimes that are not UTC **must** be timezone-aware to avoid
being able to mix them with naive-UTC-datetimes (it's also the easiest and safest way to handle timezones).\
Communication (request and read) with external services is done in UTC as much as possible (with Navitia especially).

### Git hooks

The project provides a few git hooks that you should use to prevent any issues.
Warning: they require **python 3.6+** to run (as opposed to Kirin itself, still using python 2).

These hooks are managed by [pre-commit](https://pre-commit.com/)
that you need to [install](https://pre-commit.com/#install).

The simplest way is to create a _virtualenv using python 3.6+_.\
Then run:

```sh
pip install -U pre-commit
```

After that, you can install the hooks from the root of the repository with:

```sh
pre-commit install
```

Then each time you commit, those hooks will be triggered.

You can also run them on the whole codebase.\
From the _pre-commit's virtualenv_ run:

```sh
pre-commit run --all
```

### Design recommandations

* Consider a trip as a sorted list of stop-events (pickup or drop-off).\
  This should be easier to manipulate than a list of stops with 0, 1 or 2 events.\
  Why: stop-events can individually be deleted, added, useless at extremities unless stay-in exist...

## Internal data format

The format used internally to store and process realtime information is
described [here](documentation/internal_format.md)

## Database migrations

Look at the [documentation in `migrations/`](migrations/README.md).

## Roles and architecture

Kirin is split in 4 separate "components", as seen in honcho's Procfile.

### Kirin-webservice

> Alias 'kirin' or 'web'

Its roles are:

* display the `/status`.
* provide a POST endpoint for each type of accepted realtime provider (`cots` so far).\
  On given endpoints, the webservice receives and directly processes the feed.
  The result is then saved in db and sent to corresponding Navitia's Kraken.
  It is mainly used for COTS.
* provide a CRUD `/contributors` endpoint to configure connectors to use.

There can be several of these (if they are behind a load-balancer).

### Kirin-background

> Alias 'load_realtime'

Its role is to provide all information available in db for a given provider in the rabbitmq queue, so
that Kraken can restart fully aware of realtime.

It is listening to a rabbitmq queue to be triggered on a given realtime provider.\
When a Kraken restarts it pops a rabbitmq queue and asks Kirin to provide all info in it.

There can be several of these if the load is important.

### Kirin-beat

> Alias 'scheduler'

Its role is to regularly publish polling jobs destined to Kirin-workers.

There is only one of these on each platform.

### Kirin-worker

> Alias 'worker'

Its role is to poll an external location and check if new information was published.
In that case, the worker processes it, stores the result in db and sends the corresponding info to Kraken.

For each `gtfs-rt` contributor, polling is done every `retrieval_interval` seconds.

There must be at least one worker if any feed is polled.
There can be several of these if the load is important.
At least one per polled provider is recommended.

### Kirin-PIV-worker

> Alias 'PIV'

Its role is to handle PIV contributors and listen to configured RabbitMQ.
In that case, the PIV-worker connects to it, processes feeds received, stores the result in db and sends the
corresponding info to Kraken.

For given `piv` contributor, the piv-worker will require a `broker_url`, `exchange_name` and `queue_name`.\
The RabbitMQ broker at `broker_url` must be up and the exchange `exchange_name` must exist before triggering
contributor.\
Then piv-worker will create (or use if existing) a queue named `queue_name`, bound to `exchange_name`.\
No cleaning is done after contributor shutdown (queue is persistent).

There must be at least one PIV-worker if any PIV contributor is configured.
There can be several of these to ensure uptime.
Limitation: currently, only the first PIV contributor encountered is polled.

## Error management

The main separations are:
* `Info`, `debug` log levels and `"OK"` status in newrelic are for indicators only.
  For example processing times, size of feeds.\
  Nothing there can be expected to trigger alerts to anybody.
* `Warning` log level and `"warning"` status in newrelic are for normal Kirin behavior but
  abnormal functional behavior. This is the case of a feed that can not be processed because
  base-schedule data is missing.\
  Those are expected to alert contributors (or delegates).
* `Error` log level and `"failure"` status in newrelic, and Errors in newrelic's summary
  are for abnormal Kirin behavior.
  This should never happen, but can be present for unknown errors, or when some external
  components are disconnected.\
  Those are expected to alert developers and OPS.

### Filter errors in New Relic

Errors in NewRelic summary are filtered by adding a type of exception in `newrelic.ini`.
This allows the filtering to be effective for all cases (flask, celery, piv-worker).

The line to modify/add will look like:

```ini
[newrelic]

...

error_collector.ignore_errors = kirin.exceptions:ObjectNotFound werkzeug.exceptions:MethodNotAllowed
```

#### Troubleshooting New Relic

For python agent, one can add environment variable to log NewRelic's calls to a given file.

```bash
NEW_RELIC_AUDIT_LOG=newrelic.log honcho start
```

## Tests

Most tests are implemented in `/tests` directory.\
Please read [tests readme](tests/readme.md) for more information.

## Troubleshooting

### Retrieve processed feed

#### pgAdmin

To use pgAdmin, simply `File/add server` then enter any `name` then
`Host`, `user` and `password` used by Kirin on given platform
(check conf's repository, default values are in common.py).\
If you use pgAdmin, you can increase massively the number of characters per column
(as the feed is big):
`File/preferences` then `Request editor/Request editor/Maximum number of characters per column`

#### Search and replay a RealTime update feed

Each time kirin poller consumes a realtime feed, it is saved in the
database (`real_time_update.raw_data`).

Find the concerned RealTime Updates in the table `real_time_update`, save it as a
CSV file and use the file to analyze any presence of errors.\
When logged in to the concerned kirin database:

```sql
COPY (SELECT raw_data FROM real_time_update WHERE id = 'f24eedf4-9b05-4503-a701-35439ed6571a')
To '/var/tmp/real_time_update.csv' With CSV;
```

It is also possible to re-inject (POST) the RealTime Updates of the file to Kirin's API after
some modifications.

If the feed is COTS, post the file directly to `/cots`.

For a GTFS-RT, follow these steps to post the real_time_update.csv file into the
service `/gtfs_rt`:

* Modify some lines in the file /kirin/gtfs_rt/gtfs_rt.py:\
    add `import google.protobuf.text_format` after the
    line `proto = gtfs_realtime_pb2.FeedMessage()`\
    replace `proto.ParseFromString(raw_proto)` by
    `google.protobuf.text_format.Parse(raw_proto, proto)`
* Make some adjustments in the file previously saved:\
    replace all `""` by `"` and delete the first and last `"` in the file
* Launch kirin after some adjustments in default_settings: `honcho start`
* Post the file to kirin:\
  `http POST http://server kirin/gtfs_rt @/dest_path/real_time_update.csv`

## Release

To generate a new release:

1. merge the version you want to release into release branch (adapt script):

   ```sh
   git checkout release
   git pull
   git merge <canaltp>/master
   ```

2. tag and annotate the version:

   ```sh
   git tag -a <version> # then annotate with 'Version <version>'
   # check that the version is OK
   git describe # should output the desired version
   ```

3. if needed merge back release into master branch:

   ```sh
   git checkout master
   git pull
   git merge release
   ```

4. push master, release and tags to central repo

   ```sh
   git push <canaltp> release master --tags
   ```

5. the pipe https://ci.navitia.io/view/kirin_release_deployment/ should be
   triggered (and release the new docker among other things)
