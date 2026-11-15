# nook for Home Assistant

A Home Assistant integration that turns every paired nook TV into a `media_player` entity. With it you can:

- see whether nook is on on a TV, and what it plays: a film or episode (title, poster, position, season and episode), a Live TV channel and its programme, a radio station, or a song;
- browse Continue Watching, Watchlist, Favourites, the film and show catalogues, Live TV, your favourite radio stations and your music in Home Assistant's media browser, and search all of it from there;
- start a film, a show, a channel, a station or music on a TV from a dashboard button, a script or an automation. A film or show is the same push as **Play on...** in the phone app: the TV finds the stream itself and resumes where you left off;
- stop, pause and resume what the TV plays.

Home Assistant talks only to the nook server, over HTTPS with an API key. It never talks to the TV, so it works wherever Home Assistant runs.

This repository is published from nook's main repository with each release; a change made here directly is overwritten by the next one.

## Requirements

- Home Assistant **2026.10** or newer (tested with 2026.10.4).
- A nook server whose web Settings offer API keys with the **Play and browse** scope.
- For Live TV, radio, music and Stop / Pause: an up-to-date nook server, and the TV app release after 1.10.14. With an older server those parts are left out of the media browser and the state; films and shows work as before. With an older TV app the TV ignores them.

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

The integration reads only: your devices, what they play, search, Continue Watching, Watchlist, Favourites, the catalogues, title details, your Live TV channels (names, numbers, logos and the programme on now, never the stream addresses), your radio favourites and radio search, and your music library. It writes only to your TVs: "play this", "open this link" (a channel, a station, music) and "stop / pause / resume".

## The entities

One `media_player` per paired nook TV, named after the TV (for example `media_player.living_room`).

| nook | Home Assistant state |
|---|---|
| nook is not running on the TV | `off` |
| running, nothing playing | `idle` |
| a film or episode playing / paused | `playing` / `paused`, with title, poster, duration and position |
| Live TV | `playing`, `media_content_type: channel`; title = the programme on now, `media_channel` = the channel, position and duration within the programme when the guide has it |
| a radio station playing / paused | `playing` / `paused`, `media_content_type: music`; title = the station's track line, `media_channel` = the station, its logo |
| music playing / paused | `playing` / `paused`, `media_content_type: music`; title, `media_artist`, `media_album_name`, art, duration and position |
| the TV was removed from nook | `unavailable` |

A film or episode wins when the TV reports one besides other media. `media_content_id` holds the id that plays the same thing again (below) where there is one: a film, an ONLINE show, a channel, a station; a song has none.

TVs paired later appear on their own. The state is polled every 10 seconds (change it in the integration options, 5 to 120 seconds).

## Media ids

| What | Media id | Sent to the TV as |
|---|---|---|
| A film or show | `nook://<backend>/<movie\|series>/<id>` | a Play on... push |
| Any TV link | `nook://open?...` | the link itself |
| A Live TV channel | `nook://live/<channel id>` | `nook://open?channelId=<id>&channel=<name>` |
| A radio station | `nook://radio/<station id>/<name>` (the name may be left out) | `nook://open?station=<id>&stationName=<name>` |
| An album / playlist / artist | `nook://music/album/<id>`, `nook://music/playlist/<id>`, `nook://music/artist/<id>` | `nook://open?album=<id>` (or `playlist=`, `artist=`) |
| Liked songs | `nook://music/liked` | `nook://open?music=liked` |
| A song in one of those | `nook://music/album/<id>/song/<track id>` (also under `playlist/<id>`, `artist/<id>` and `liked`) | the list, with `&song=<track id>`: it plays from that song on |
| Shuffle the whole library | `nook://music/shuffle` | `nook://open?music=shuffle` |

A media browser folder is `nook://browse/<node>`; it cannot be played. A song found by search or in Recently played plays from its album; a song with no album is listed but cannot be played.

A film or show is `nook://<backend>/<type>/<id>`:

- `backend` is `jellyfin` (LOCAL) or `stremio` (ONLINE);
- `type` is `movie` or `series`. A show plays what its Play / Continue button names on the TV: the episode in progress, else the next unwatched one;
- `id` is the title's id in that backend, for example `tt1160419` or `tmdb:697620`.

Examples: `nook://stremio/movie/tt1160419` (Dune, ONLINE), `nook://stremio/series/tt5875444` (Slow Horses, ONLINE).

How to find an id:

- **Media browser or automation editor.** In an automation or script, add the action **Media player: Play media**, pick the TV, then pick the title from the browser. The editor stores its id.
- **Search action.** In **Developer tools > Actions**, run `media_player.search_media` on a nook TV with a `search_query`. The response lists every hit with its `media_content_id`.
- **nook web.** A title's page is `https://<server>/detail/stremio%3Att1160419`. The part before `%3A` (an encoded `:`) is the backend, the rest is the id: `nook://stremio/movie/tt1160419`. Add `movie` or `series` yourself.

The media browser and the search action give channel, station and music ids the same way. For a channel you rarely need one: `nook://open?channel=5` plays the channel the TV's guide numbers 5 (or, with a name, `nook://open?channel=HRT%201`).

### TV links

A `nook://open?...` id goes to the TV as it is, and the server checks it first (a typo fails with the server's reason). What a link can say:

| Link | Does |
|---|---|
| `nook://open` | brings nook to the front |
| `nook://open?screen=radio` | opens a section: `movies`, `shows`, `live`, `radio` or `music` |
| `nook://open?channel=5` | plays a Live TV channel by its number, else by its name |
| `nook://open?station=Otvoreni` | plays a radio station: a favourite (by TuneIn id such as `s18415`, or by name) first, then TuneIn |
| `nook://open?playlist=<id>&shuffle=1` | plays music shuffled (also with `album=`, `artist=`, `music=liked`) |
| `nook://open?music=shuffle` | shuffles the whole music library |

Values are URL-encoded (`station=BBC%20Radio%201`) and at most 100 characters long.

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

## Live TV, radio and music

In the media browser, after the film and show sections:

- **Live TV**: Favourites, then one folder per channel group, as the TV lists them. A channel shows as "5 · HRT 1 · Dnevnik": its number, its name and the programme on now.
- **Radio**: your favourite stations. Any other station is found through the search box.
- **Music**: Playlists, Liked songs, Recently played, Recently added, Top artists, Albums, Artists and Shuffle all. An album, a playlist, an artist and Liked songs play whole, or open to their songs; a song plays its list from that song on. Albums, Artists and Liked songs come 100 at a time, with a More folder at the end.

A section with nothing in it is left out: no Live TV without channels, no Radio without a favourite station, no Music without songs.

A button that plays channel 5 (by the number the TV's guide shows):

```yaml
type: button
name: Channel 5
icon: mdi:television-classic
tap_action:
  action: perform-action
  perform_action: media_player.play_media
  target:
    entity_id: media_player.living_room
  data:
    media_content_type: channel
    media_content_id: nook://open?channel=5
```

A favourite radio station (the id and name as the media browser lists them):

```yaml
type: button
name: Otvoreni
icon: mdi:radio
tap_action:
  action: perform-action
  perform_action: media_player.play_media
  target:
    entity_id: media_player.living_room
  data:
    media_content_type: music
    media_content_id: nook://radio/s18415/Otvoreni
```

A playlist (its id as the media browser lists it), and the whole library shuffled:

```yaml
type: horizontal-stack
cards:
  - type: button
    name: Running
    icon: mdi:playlist-music
    tap_action:
      action: perform-action
      perform_action: media_player.play_media
      target:
        entity_id: media_player.living_room
      data:
        media_content_type: playlist
        media_content_id: nook://music/playlist/0b6f6c1e-9d4a-4c55-8a51-3f0d2a1c7e42
  - type: button
    name: Shuffle all
    icon: mdi:shuffle-variant
    tap_action:
      action: perform-action
      perform_action: media_player.play_media
      target:
        entity_id: media_player.living_room
      data:
        media_content_type: music
        media_content_id: nook://music/shuffle
```

### Stop, pause and resume

`media_player.media_stop`, `media_player.media_pause` and `media_player.media_play` (and the media card's buttons) stop, pause or resume whatever the TV plays: a film or episode (its progress is kept), a station or music. Live TV ignores pause and resume; stop closes it.

```yaml
type: button
name: Stop
icon: mdi:stop
tap_action:
  action: perform-action
  perform_action: media_player.media_stop
  target:
    entity_id: media_player.living_room
```

## Turning the TV on: link its Android TV Remote

nook can only play on a TV where nook is running. To let Home Assistant wake the TV first:

1. Set up Home Assistant's own **Android TV Remote** integration for the TV (Shield or any Google TV / Android TV). It creates a `remote.` entity.
2. Open **Settings > Devices & services > nook > Configure**. Each nook TV has a field: pick its `remote.` entity.

With a remote linked:

- the nook entity gets **Turn on**: Home Assistant powers the TV on and opens nook (it sends the app link `nook://open` through the remote);
- playing on that TV while it is `off` turns it on first, waits up to 20 seconds for nook to come online, then plays.

Without a linked remote, playing on an `off` TV fails with "<TV> is offline". nook's auto-start (on boot and on wake) keeps the wait short.

## Limits

- The TV must be online, or have a linked remote to wake it. Stop / Pause / Play never wake it.
- A play, a link and a control are fire-and-forget: success means the server sent it to the TV. The state is polled again 2 seconds later, and shows the change once the TV has reported it.
- No seek, next / previous track or volume from Home Assistant. Volume and power stay with the Android TV Remote integration.
- Episodes play through their show (the episode its Continue button names); a specific episode cannot be picked yet.
- A song in the state has no `media_content_id`: the TV does not say which album or playlist it plays from.
- Media browser images served over plain `http://` (some station and channel logos, a Jellyfin server without HTTPS) are fetched by Home Assistant and passed on, so an HTTPS Home Assistant shows them too.

## Development

Tests use `pytest-homeassistant-custom-component` (Python 3.14). From the folder that holds `custom_components/` and `tests/`:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

Home Assistant does not run on Windows; run the tests on Linux, macOS or WSL.
