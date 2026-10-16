import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api.dart';
import '../platform/platform.dart';
import '../core/models.dart';
import '../state/library.dart';
import '../state/offline.dart';
import 'artist_screen.dart';
import 'widgets.dart';

class AlbumScreen extends StatefulWidget {
  const AlbumScreen({super.key, required this.albumId, this.name = ''});
  final String albumId;
  final String name;

  @override
  State<AlbumScreen> createState() => _AlbumScreenState();
}

class _AlbumScreenState extends State<AlbumScreen> {
  Album? album;
  List<Song> songs = [];
  String? error;
  bool loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final off = context.read<OfflineStore>();
    try {
      if (widget.albumId.startsWith('local:')) throw ApiException('On this device');
      final r = await context.api.album(widget.albumId);
      if (!mounted) return;
      setState(() {
        album = r.album;
        songs = r.songs;
        loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      final local = off.songsOfAlbum(widget.albumId);
      if (local.isNotEmpty) {
        // no connection: show what is saved on this device
        setState(() {
          album = Album({'id': widget.albumId, 'name': local.first.album, 'artist': local.first.artist, 'coverArt': local.first.coverArt});
          songs = local;
          loading = false;
          error = null;
        });
        return;
      }
      setState(() {
        error = e.toString();
        loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final lib = context.watch<Library>();
    final a = album;
    final follow = lib.followFor(widget.albumId);
    return PageFrame(
      title: a?.name ?? widget.name,
      body: loading
          ? const Center(child: CircularProgressIndicator())
          : error != null
              ? EmptyNote(error!, icon: Icons.error_outline)
              : ListView(children: [
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: Row(children: [
                      Cover(a?.coverArt ?? widget.albumId, size: 132, radius: 10),
                      const SizedBox(width: 16),
                      Expanded(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Text(a?.name ?? '', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
                          const SizedBox(height: 4),
                          if ((a?.artist ?? '').isNotEmpty)
                            InkWell(
                              onTap: (a?.artistId ?? '').isEmpty ? null : () => context.open(ArtistScreen(artistId: a!.artistId, name: a.artist)),
                              child: Text(a!.artist, style: TextStyle(color: kAccent, fontWeight: FontWeight.w600)),
                            ),
                          Text([if ((a?.year ?? 0) > 0) '${a!.year}', '${songs.length} songs', fmtListening(songs.fold(0, (t, s) => t + s.duration))].join(' · '),
                              style: TextStyle(color: kMuted)),
                        ]),
                      ),
                    ]),
                  ),
                  PlayButtons(songs: songs, extra: [
                    FollowButton(
                      kind: 'album',
                      remoteId: widget.albumId,
                      name: a?.name ?? widget.name,
                      cover: '',
                      artist: a?.artist ?? '',
                      follow: follow,
                      localSongs: songs,
                    ),
                    if (context.has('playlists'))
                    OutlinedButton.icon(
                      onPressed: songs.isEmpty ? null : () => showPlaylistPicker(context, songs),
                      icon: const Icon(Icons.playlist_add),
                      label: const Text('Add to playlist'),
                    ),
                    if (!isDesktop)
                      OutlinedButton.icon(
                      onPressed: songs.isEmpty
                          ? null
                          : () {
                              context.toast('Saving ${songs.length} songs for offline');
                              context.read<OfflineStore>().downloadAll(songs);
                            },
                      icon: const Icon(Icons.download_for_offline_outlined),
                      label: const Text('Offline'),
                    ),
                  ]),
                  for (var i = 0; i < songs.length; i++)
                    SongTile(song: songs[i], number: songs[i].track > 0 ? songs[i].track : i + 1, showCover: false, onTap: () => context.player.playSongs(songs, start: i)),
                  const SizedBox(height: 24),
                ]),
    );
  }
}

/// "Follow" = like it: it becomes its own playlist that keeps itself up to date.
class FollowButton extends StatelessWidget {
  const FollowButton({
    super.key,
    required this.kind,
    required this.remoteId,
    required this.name,
    required this.follow,
    this.cover = '',
    this.artist = '',
    this.localSongs = const [],
  });
  final String kind;
  final String remoteId;
  final String name;
  final String cover;
  final String artist;
  final Follow? follow;
  final List<Song> localSongs;

  @override
  Widget build(BuildContext context) {
    final lib = context.read<Library>();
    final f = follow;
    final isRemote = remoteId.startsWith('yb-') || remoteId.startsWith('ya-') || remoteId.startsWith('yp-');
    if (!context.has('playlists') || (isRemote && !context.has('follows'))) return const SizedBox.shrink();
    if (f != null) {
      return FilledButton.tonalIcon(
        onPressed: () async {
          final remove = await confirm(context, 'Unfollow "$name"?', 'The playlist stays. It just stops updating.', ok: 'Unfollow');
          if (!remove || !context.mounted) return;
          try {
            await lib.unfollow(f);
          } catch (e) {
            if (context.mounted) context.toast('$e');
          }
        },
        icon: f.active ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)) : Icon(Icons.favorite, color: kAccent),
        label: Text(f.active ? (f.total > 0 ? 'Downloading ${f.done + f.failed}/${f.total}' : 'Preparing...') : 'Following'),
      );
    }
    return FilledButton.tonalIcon(
      onPressed: () async {
        if (!isRemote) {
          // a library album: there is nothing to download, make it a playlist instead
          if (localSongs.isEmpty) return;
          try {
            await lib.createPlaylist(name, songs: localSongs);
            if (context.mounted) context.toast('Saved as a playlist');
          } catch (e) {
            if (context.mounted) context.toast('$e');
          }
          return;
        }
        try {
          await lib.follow(kind, remoteId, name, cover: cover, artist: artist);
          if (context.mounted) context.toast('Following "$name". Its songs are in a new playlist.');
        } catch (e) {
          if (context.mounted) context.toast('$e');
        }
      },
      icon: const Icon(Icons.favorite_border),
      label: Text(isRemote ? 'Follow' : 'Save as playlist'),
    );
  }
}
