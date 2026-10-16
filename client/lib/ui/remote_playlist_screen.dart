import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../state/library.dart';
import 'album_screen.dart';
import 'widgets.dart';

/// A public YouTube Music playlist: browse, play, or follow it (download everything and keep it as a playlist).
class RemotePlaylistScreen extends StatefulWidget {
  const RemotePlaylistScreen({super.key, required this.playlistId, this.name = '', this.author = ''});
  final String playlistId;
  final String name;
  final String author;
  @override
  State<RemotePlaylistScreen> createState() => _RemotePlaylistScreenState();
}

class _RemotePlaylistScreenState extends State<RemotePlaylistScreen> {
  List<Song> songs = [];
  String name = '';
  String author = '';
  String cover = '';
  String? error;
  String remoteId = ''; // the id the server follows this playlist by
  bool loading = true;

  @override
  void initState() {
    super.initState();
    name = widget.name;
    author = widget.author;
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await context.api.remotePlaylist(widget.playlistId);
      if (!mounted) return;
      setState(() {
        songs = r.songs;
        if (r.name.isNotEmpty) name = r.name;
        if (r.author.isNotEmpty) author = r.author;
        cover = r.cover;
        remoteId = r.id;
        loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        error = e.toString();
        loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final lib = context.watch<Library>();
    return PageFrame(
      title: name,
      body: loading
          ? const Center(child: CircularProgressIndicator())
          : error != null
              ? EmptyNote(error!, icon: Icons.error_outline)
              : ListView(children: [
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: Row(children: [
                      Cover(songs.isEmpty ? '' : songs.first.coverArt, size: 110, radius: 10),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Text(name, style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
                          if (author.isNotEmpty) Text(author, style: TextStyle(color: kMuted)),
                          Text('${songs.length} songs', style: TextStyle(color: kMuted)),
                        ]),
                      ),
                    ]),
                  ),
                  PlayButtons(songs: songs, extra: [
                    FollowButton(kind: 'playlist', remote: true, remoteId: remoteId, name: name, cover: cover, artist: author, follow: lib.followFor(remoteId)),
                    if (context.has('playlists'))
                    OutlinedButton.icon(
                      onPressed: songs.isEmpty ? null : () => showPlaylistPicker(context, songs),
                      icon: const Icon(Icons.playlist_add),
                      label: const Text('Add to playlist'),
                    ),
                  ]),
                  for (var i = 0; i < songs.length; i++) SongTile(song: songs[i], onTap: () => context.player.playSongs(songs, start: i)),
                  const SizedBox(height: 24),
                ]),
    );
  }
}
