# Brigid

Watches [Ogma](https://github.com/brk3/ogma)'s `TV/` and `Movies/` output folders
and fetches missing subtitles as new files show up. No Sonarr/Radarr/Bazarr:
Brigid only needs a folder to watch and [subliminal](https://github.com/Diaoul/subliminal)
to do the actual searching.

## How it works

A filesystem watcher on `TV_ROOT`/`MOVIES_ROOT` reacts the moment Ogma hardlinks
a new video in, waits `SETTLE_SECONDS` for the write to settle, then runs
`subliminal download` against that one file.

Brigid doesn't watch Ogma or call it directly, and Ogma doesn't know Brigid
exists. The two are linked only by Ogma now preserving each file's original
release name instead of renaming it to a clean Plex title -- that's what gives
subliminal's [guessit](https://github.com/guessit-io/guessit)-based scoring
(resolution, source, release group) something to match against, the same
signal a name like `Show Name - S01E02.mkv` would have thrown away.

subliminal already skips any video that has an embedded or external subtitle
in the requested language, so re-running over an already-subtitled library is
a no-op. It searches [OpenSubtitles](https://www.opensubtitles.com) and a
handful of other providers by default; anonymous use works but is rate
limited, and the accompanying homelab manifests wire in a
[dagda](https://github.com/brk3/homelab) infrastructure Secret with an
OpenSubtitles.com account so hash-based matching -- the most reliable kind,
since it works from the file's contents rather than its name -- actually
kicks in.

## Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `TV_ROOT` | `/data/tv` | Watched for new episodes |
| `MOVIES_ROOT` | `/data/movies` | Watched for new movies |
| `LANGUAGES` | `en` | Comma-separated subtitle languages to fetch |
| `SETTLE_SECONDS` | `5` | Wait after a file appears before fetching |
| `HEALTH_PORT` | `8080` | Port for `/healthz` and `/readyz` |
| `LOG_FILE` | _(unset)_ | Also log to this file |

Provider credentials (e.g. an OpenSubtitles.com account) are passed straight
through to subliminal via its own `SUBLIMINAL_PROVIDER_*` env vars -- see
subliminal's [provider config reference](https://github.com/Diaoul/subliminal)
for the full list. Brigid doesn't wrap or translate these.

## Health

- `/healthz` -- 503 if the watcher thread has died or the main loop has stalled.
- `/readyz` -- 503 until the startup scan finishes.

## Development

```sh
make venv
make test
```

## Releasing

Pushing a `vX.Y.Z` tag triggers `.github/workflows/release.yml`, which runs
the tests and builds/pushes `ghcr.io/brk3/brigid:vX.Y.Z`.

```sh
git tag v0.1.0
git push origin v0.1.0
```
