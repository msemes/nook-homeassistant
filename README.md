# nook for Home Assistant

A Home Assistant integration that turns every paired nook TV into a `media_player` entity. With it you can:

- see whether nook is on on a TV, and what it plays (title, poster, position, season and episode);
- browse Continue Watching, Watchlist, Favourites, and the film and show catalogues in Home Assistant's media browser, and search the library from there;
- start a film or a show on a TV from a dashboard button, a script or an automation. It is the same push as **Play on...** in the phone app: the TV finds the stream itself and resumes where you left off.

Home Assistant talks only to the nook server, over HTTPS with an API key. It never talks to the TV, so it works wherever Home Assistant runs.

This repository is published from nook's main repository with each release; a change made here directly is overwritten by the next one.

## Requirements

- Home Assistant **2026.10** or newer (tested with 2026.10.4).
- A nook server whose web Settings offer API keys with the **Play and browse** scope.

## Install with HACS

1. In Home Assistant, open **HACS**, then the menu (three dots, top right) **> Custom repositories**.
2. Repository: `https://github.com/msemes/nook-homeassistant`, type: **Integration**. **Add.**
3. Find **nook** in HACS and **Download** it.
4. Restart Home Assistant.
5. **Settings > Devices & services > Add integration > nook.** Enter the server URL (for example `https://nook.example.com`) and an API key (below).

**Updates** show up in HACS and under **Settings > Updates** like any other: install, then restart Home Assistant.

### Without HACS

Copy the folder `custom_components/nook` to `/config/custom_components/nook` on the Home Assistant machine (the result is `/config/custom_components/nook/manifest.json`), restart, and add the integration as in step 5. To update, replace the folder and restart.

### The API key

In nook's admin console, open **System** (`https://<server>/admin/system`) and scroll to the **API access** card at the bottom. Create a key with the **Play and browse** scope. Name it "Home Assistant": the TV shows that name as the sender of what it plays.

A full-access key works too, but it gives Home Assistant, and everyone who can read its configuration files, admin rights over the nook server. Do not use one.

The integration reads only: your devices, what they play, search, Continue Watching, Watchlist, Favourites, the catalogues, and title details. It writes only one thing: "play this on that TV".

## The entities

One `media_player` per paired nook TV, named after the TV (for example `media_player.living_room`).

| nook | Home Assistant state |
|---|---|
| nook is not running on the TV | `off` |
| running, nothing playing | `idle` |
| a film or episode playing / paused | `playing` / `paused`, with title, poster, duration and position |
| the TV was removed from nook | `unavailable` |

TVs paired later appear on their own. The state is polled every 10 seconds (change it in the integration options, 5 to 120 seconds).

## Media ids

A film or show is `nook://<backend>/<type>/<id>`:

- `backend` is `jellyfin` (LOCAL) or `stremio` (ONLINE);
- `type` is `movie` or `series`. A show plays what its Play / Continue button names on the TV: the episode in progress, else the next unwatched one;
- `id` is the title's id in that backend, for example `tt1160419` or `tmdb:697620`.

Examples: `nook://stremio/movie/tt1160419` (Dune, ONLINE), `nook://stremio/series/tt5875444` (Slow Horses, ONLINE).

How to find an id:

- **Media browser or automation editor.** In an automation or script, add the action **Media player: Play media**, pick the TV, then pick the title from the browser. The editor stores its id.
- **Search action.** In **Developer tools > Actions**, run `media_player.search_media` on a nook TV with a `search_query`. The response lists every hit with its `media_content_id`.
- **nook web.** A title's page is `https://<server>/detail/stremio%3Att1160419`. The part before `%3A` (an encoded `:`) is the backend, the rest is the id: `nook://stremio/movie/tt1160419`. Add `movie` or `series` yourself.

## Examples

A dashboard button that starts Dune on the living-room TV:

```yaml
type: button
name: Dune
icon: mdi:movie-open
tap_action:
  action: perform-action
  perform_action: media_player.play_media
  target:
    entity_id: media_player.living_room
  data:
    media_content_type: movie
    media_content_id: nook://stremio/movie/tt1160419
```

A button that plays a show by name, through the `nook.play_title` action. It searches nook, takes the first hit that fits the optional filters (`type`: `movie` or `series`, `year`, `source`: `local` or `online`) and plays it:

```yaml
type: button
name: The Bear
icon: mdi:television-play
tap_action:
  action: perform-action
  perform_action: nook.play_title
  target:
    entity_id: media_player.living_room
  data:
    title: The Bear
    type: series
    source: online
```

`nook.play_title` can return what it picked (`title` and `media_content_id`) to a script:

```yaml
action: nook.play_title
target:
  entity_id: media_player.living_room
data:
  title: Dune
  year: 2021
response_variable: picked
```

An automation: on Friday at 20:00, if someone is home, start the film on the living-room TV:

```yaml
alias: Friday film night
triggers:
  - trigger: time
    at: "20:00:00"
conditions:
  - condition: time
    weekday: fri
  - condition: numeric_state
    entity_id: zone.home
    above: 0
actions:
  - action: media_player.play_media
    target:
      entity_id: media_player.living_room
    data:
      media_content_type: movie
      media_content_id: nook://stremio/movie/tt1160419
```

## Turning the TV on: link its Android TV Remote

nook can only play on a TV where nook is running. To let Home Assistant wake the TV first:

1. Set up Home Assistant's own **Android TV Remote** integration for the TV (Shield or any Google TV / Android TV). It creates a `remote.` entity.
2. Open **Settings > Devices & services > nook > Configure**. Each nook TV has a field: pick its `remote.` entity.

With a remote linked:

- the nook entity gets **Turn on**: Home Assistant powers the TV on and opens nook (it sends the app link `nook://open` through the remote);
- playing on that TV while it is `off` turns it on first, waits up to 20 seconds for nook to come online, then plays.

Without a linked remote, playing on an `off` TV fails with "<TV> is offline". nook's auto-start (on boot and on wake) keeps the wait short.

## Limits (phase 1)

- The TV must be online, or have a linked remote to wake it.
- The state covers films and episodes only. Live TV, radio and music on the TV show as `idle`.
- No pause, stop, seek or volume from Home Assistant yet: nook has no such commands. Volume and power stay with the Android TV Remote integration.
- A play is fire-and-forget: success means the server sent it to the TV. The entity shows it within one poll once the TV starts playing.
- Episodes play through their show (the episode its Continue button names); a specific episode cannot be picked yet.

## Development

Tests use `pytest-homeassistant-custom-component` (Python 3.14). From the folder that holds `custom_components/` and `tests/`:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

Home Assistant does not run on Windows; run the tests on Linux, macOS or WSL.
