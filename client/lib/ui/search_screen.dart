import 'dart:async';

import 'package:flutter/material.dart';

import '../core/models.dart';
import 'album_screen.dart';
import 'artist_screen.dart';
import 'remote_playlist_screen.dart';
import 'widgets.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});
  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final TextEditingController ctl = TextEditingController();
  Timer? _debounce;
  SearchResults? results;
  bool loading = false;
  String? error;
  int _ticket = 0;

  @override
  void dispose() {
    _debounce?.cancel();
    ctl.dispose();
    super.dispose();
  }

  void _changed(String q) {
    setState(() {});
    _debounce?.cancel();
    if (q.trim().length < 2) {
      setState(() {
        results = null;
        loading = false;
      });
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 450), () => _run(q.trim()));
  }

  Future<void> _run(String q) async {
    final ticket = ++_ticket;
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final r = await context.api.search(q);
      if (!mounted || ticket != _ticket) return;
      setState(() {
        results = r;
        loading = false;
      });
    } catch (e) {
      if (!mounted || ticket != _ticket) return;
      setState(() {
        error = e.toString();
        loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = results;
    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: ctl,
          onChanged: _changed,
          onSubmitted: (q) => q.trim().length >= 2 ? _run(q.trim()) : null,
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
            hintText: 'Songs, artists, albums, playlists',
            border: InputBorder.none,
            prefixIcon: const Icon(Icons.search),
            suffixIcon: ctl.text.isEmpty
                ? null
                : IconButton(
                    icon: const Icon(Icons.close),
                    onPressed: () {
                      ctl.clear();
                      _changed('');
                    },
                  ),
          ),
        ),
        bottom: loading ? const PreferredSize(preferredSize: Size.fromHeight(2), child: LinearProgressIndicator(minHeight: 2)) : null,
      ),
      body: error != null
          ? EmptyNote(error!, icon: Icons.error_outline)
          : r == null
              ? const EmptyNote('Search your library and all of YouTube Music.\nSongs you have not downloaded stream instantly; like one to keep it.', icon: Icons.search)
              : (r.songs.isEmpty && r.artists.isEmpty && r.albums.isEmpty && r.playlists.isEmpty)
                  ? const EmptyNote('Nothing found')
                  : ListView(children: [
                      if (r.artists.isNotEmpty) ...[
                        const SectionHeader('Artists'),
                        HRow(height: 160, children: [
                          for (final a in r.artists)
                            CardTile(
                              coverId: a.coverArt,
                              title: a.name,
                              circle: true,
                              width: 112,
                              onTap: () => context.open(ArtistScreen(artistId: a.id, name: a.name)),
                            ),
                        ]),
                      ],
                      if (r.songs.isNotEmpty) ...[
                        const SectionHeader('Songs'),
                        for (var i = 0; i < r.songs.length; i++) SongTile(song: r.songs[i], onTap: () => context.player.playSongs(r.songs, start: i)),
                      ],
                      if (r.albums.isNotEmpty) ...[
                        const SectionHeader('Albums'),
                        HRow(children: [
                          for (final a in r.albums)
                            CardTile(
                              coverId: a.coverArt,
                              title: a.name,
                              subtitle: a.artist,
                              onTap: () => context.open(AlbumScreen(albumId: a.id, name: a.name)),
                            ),
                        ]),
                      ],
                      if (r.playlists.isNotEmpty) ...[
                        const SectionHeader('Playlists'),
                        for (final p in r.playlists)
                          ListTile(
                            leading: const Icon(Icons.queue_music, size: 32),
                            title: Text(p.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                            subtitle: Text(p.author, style: const TextStyle(color: kMuted)),
                            trailing: const Icon(Icons.chevron_right),
                            onTap: () => context.open(RemotePlaylistScreen(playlistId: p.id, name: p.title, author: p.author)),
                          ),
                      ],
                      const SizedBox(height: 24),
                    ]),
    );
  }
}
