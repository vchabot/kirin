# Kirin internal format

## Overview

Kirin's internal model is derived from GTFS-realtime `Trip updates` (specifications)[http://gtfs.org/realtime/#message-tripupdate].
It is composed of several objects :

- VehicleJourney
- RealTimeUpdate
- TripUpdate
- StopTimeUpdate

## Model details

### Contributor

A contributor is a provider of a realtime service.
In db, it is linked to a referent coverage and contains configuration information.

Property | Type | Description
--- | --- | ---
id | String, Required | Unique code of the contributor. It must be known by Kraken that consumes the output (internal component of (navitia)[https://github.com/CanalTP/navitia]).
navitia_coverage | String, Required | Navitia coverage to be used while calling navitia to retrieve corresponding element
navitia_token | String, Optional | Navitia token to be used while calling navitia to retrieve corresponding element
feed_url | String, Optional | Url to retrieve the realtime information feed (for polled sources)
retrieval_interval | Integer, Optional | Minimal interval to retrieve the realtime information feed (for polled sources)
connector_type | Enum, Required | Type of connector (possible values are `cots`, `gtfs-rt`)
is_active | Boolean, Optional | Used to activate/deactivate the kirin service for the contributor (default value `true`)
broker_url| String, Optional | Url of the AMQP broker to listen for realtime information queue
exchange_name| String, Optional | Exchange of the AMQP broker to listen for realtime information
queue_name| String, Optional | Queue of the AMQP broker to listen for realtime information queue
nb_days_to_keep_trip_update| Integer, Optional | For trip_update table (archive of all the processed realtime-trips, used by Navitia in case of reload): number of days to keep before purge (considering trip's start date)
nb_days_to_keep_rt_update| Integer, Optional | For real_time_update table (text archive of all "raw" feeds received, used for diagnostic): number of days to keep before purge (considering date of reception of the feed)

Note: `broker_url`, `exchange_name` and `queue_name` are used for `piv` contributor only for now.
All 3 must be filled if used.

### RealTimeUpdate

Received raw data from a Real Time Update.

Property | TYpe | Description
--- | --- | ---
id | UUID |
connector | Enum, Required | Source of the data. See below for an available source format.
status | Enum, Required | Processing status of the received data (Possible values are `OK`, `KO` or `pending`)
error | String, Optional | Description of the error (if any)
raw_data | String, Optional | Content of the received raw data
contributor_id | String, Required | `contributor.id` of the RT data producer.
trip_updates | List | List of `TripUpdate` provided by this bloc of data

#### Connector field possible values

The `connector` field is restricted to the following values:

- `ire`: realtime informations of the SNCF long distance trains
- `gtfs-rt`: realtime informations from the `TripUpdate` format of GTFS-realtime

### TripUpdate

Update information about a VehicleJourney.

Property | TYpe | Description
--- | --- | ---
id | UUID |
vj_id | UUID | id of the VehicleJourney being updated
status | Enum, Required | Modification type for this trip (Possible values are `add`, `delete`, `update` or `none`)
message | String, Optional | Text to be displayed in Navitia for the `VehicleJourney`
contributor_id | String, Required | `contributor.id` of the RT data producer.
company_id | String, Optional | Identifier of the transport operator found in Navitia for this trip.
stop_time_updates | List | List of `StopTimeUpdate` provided by this bloc of data
effect | Enum, optional | Effect to be displayed in navitia (Possible values are `NO_SERVICE`, `REDUCED_SERVICE`, `SIGNIFICANT_DELAYS`, `DETOUR`, `ADDITIONAL_SERVICE`, `MODIFIED_SERVICE`, `OTHER_EFFECT`, `UNKNOWN_EFFECT`, `STOP_MOVED`)
physical_mode_id | String, Optional | Identifier of the physical mode found in Navitia for this trip

### VehicleJourney

Property | TYpe | Description
--- | --- | ---
id | UUID | Unique identifier
navitia_trip_id | String, Required | Identifier of Navitia's trip_id
start_timestamp | DateTime, Required | Start date and time of the VehicleJourney (UTC) in base_schedule (ie. without any realtime info)

### StopTimeUpdate

Property | TYpe | Description
--- | --- | ---
id | UUID |
trip_update_id | UUID | id of the `TripUpdate` containing this `StopTimeUpdate`
order | Integer, Required | `StopTime` order in the `VehicleJourney`
stop_id | String, Required | id of the stop_point in navitia
message | String, Optional | Text to be displayed in Navitia for the `StopTime`
departure | DateTime, Optional | Base scheduled departure datetime of this StopTime
departure_delay | Integer, Optional | Delay for the departure at this StopTime (in minutes)
departure_status | Enum, Required | Modification type for the departure of the trip at this StopTime (Possible values are `add`, `delete`, `update` or `none`)
arrival | DateTime, Optional | Base scheduled arrival datetime of this StopTime
arrival_delay | Integer, Optional | Delay for the arrival at this StopTime (in minutes)
arrival_status | Enum, Required | Modification type for the arrival of the trip at this StopTime (Possible values are `add`, `delete`, `update` or `none`)
