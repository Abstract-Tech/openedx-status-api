# openedx-status-api

The backend of an Open edX status page. It checks your services every minute by default, keeps 90 days of
history and incidents in a small SQLite file, and serves the result as JSON. It does not use the LMS or
Django, so it keeps working when Open edX is down.

## Quick start

This runs the API, the web page ([openedx-status-web](https://github.com/Abstract-Tech/openedx-status-web))
and a Caddy proxy with Docker Compose:

    cd deploy
    cp .env.example .env              # set STATUS_DOMAIN, and point its DNS at this machine
    cp checks.example.yml checks.yml  # put your own addresses in
    docker compose up -d

Then open `https://<STATUS_DOMAIN>`. To try it locally without HTTPS, set `STATUS_DOMAIN=:80`,
`STATUS_BIND=127.0.0.1` and `STATUS_HTTP_PORT=8088` in `.env`.

Day to day:

- Change the checks: edit `checks.yml`, then `docker compose up -d --force-recreate status-api` (a plain restart can keep a stale copy of the file).
- Logs: `docker compose logs -f status-api`.
- Backup: the history is the SQLite file in the `status-data` volume.
- Already have a reverse proxy? Remove the `caddy` service and point your proxy at `status-web:8080`.

To run the API alone without Docker:

    pip install .
    STATUS_CHECKS_FILE=examples/checks.yml STATUS_DATA_DIR=./data python -m openedx_status_api

If you mount a host folder as `/data`, run `chown 10001` on it, because the container runs as user
10001. Start only one copy per data folder.

## The checks file

`checks.yml` is a list under `checks:`. Each check has:

- `id`, `name`, `group`
- `kind`: `http`, `tcp`, `mysql`, `mongo`, `redis` or `ssl`
- `target`: the URL, `host:port`, or host name
- `interval` (default 60 seconds, minimum 5), `timeout` (default 10)
- `critical` (default true). A failing non-critical check shows as "degraded" instead of "major outage".
- `params`: extra options for some kinds

To keep a secret out of the file, write `<name>_env: SOME_VARIABLE` instead of the value, for example
`password_env: DB_PASSWORD`. With the compose file in `deploy/`, put `DB_PASSWORD=...` in `deploy/.env`
(it is passed to the API container) and recreate the container. This works for the http `expected_status` and `verify_tls`, the mysql `user`,
`password` and `database`, the mongo `username` and `password`, the redis `password`, and the ssl `warn_days`.

There is a full example in `examples/checks.yml`. The file is read once at startup, so restart after
changing it. If it is missing or broken, the error is logged and the page shows "unknown".

## Settings

Set these as environment variables.

| Name | Default | What it does |
|---|---|---|
| STATUS_TITLE | Open edX status | Title of the page |
| STATUS_CHECKS_FILE | /config/checks.yml | Your list of checks |
| STATUS_DATA_DIR | /data | Where the history is stored |
| STATUS_FAIL_THRESHOLD | 3 | Failed checks in a row before an incident opens or a component shows as down |
| STATUS_RETENTION_DAYS | 90 | How long history is kept |
| STATUS_HOST / STATUS_PORT | 0.0.0.0 / 8000 | Where the API listens |

## What happens when something fails

| What fails | The status page |
|---|---|
| LMS, Studio, the micro-frontends, MySQL, MongoDB or Redis | Stays up, shows the outage and opens an incident |
| `tutor local stop`, `restart` or `upgrade` | Stays up, because it is a separate project |
| The status API restarts | Stays up and shows the last answer, with a "monitor is not reporting" note |
| The status web container restarts | Back in a few seconds, no history lost |
| The whole server goes down | Goes down too, if it runs on that server |

For the last case, run it on a second small server and use only public addresses in `checks.yml`.
Don't host the status page's DNS or certificates on the platform it reports on.

## API

- `GET /healthz` returns `{"ok": true}`
- `GET /api/status` returns the title, overall status, the groups with their components and 90 days of
  history (`uptime_90d` covers `STATUS_RETENTION_DAYS` days), and the incidents.

Targets and passwords are never included in the output.

## Tests

    pip install '.[test]'
    pytest

## Related projects

- [openedx-status-web](https://github.com/Abstract-Tech/openedx-status-web): the web page for this API.
- [tutor-contrib-status](https://github.com/Abstract-Tech/tutor-contrib-status): a Tutor plugin that runs
  both services inside Tutor. Easier to set up, but it stops with `tutor local stop`.

## License

MIT, see [LICENSE](LICENSE).
