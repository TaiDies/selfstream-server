import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../state/library.dart';
import '../state/offline.dart';
import 'album_screen.dart';
import 'artist_screen.dart';
import 'playlist_screen.dart';
import 'widgets.dart';

class LibraryScreen extends StatelessWidget {
  const LibraryScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 6,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Library', style: TextStyle(fontWeight: FontWeight.w800)),
          bottom: const TabBar(isScrollable: true, tabAlignment: TabAlignment.start, tabs: [
            Tab(text: 'Playlists'),
            Tab(text: 'Following'),
            Tab(text: 'Liked'),
            Tab(text: 'Albums'),
            Tab(text: 'Artists'),
            Tab(text: 'Offline'),
          ]),
        ),
        body: const TabBarView(children: [
          _PlaylistsTab(),
          _FollowingTab(),
          _LikedTab(),
          _AlbumsTab(),
          _ArtistsTab(),
          _OfflineTab(),
        ]),
      ),
    );
  }
}

class _PlaylistsTab extends StatelessWidget {
  const _PlaylistsTab();
  @override
  Widget build(BuildContext context) {
    final lib = context.watch<Library>();
    return Scaffold(
      backgroundColor: Colors.transparent,
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () async {
          final name = await askText(context, 'New playlist', 'Name');
          if (name == null || name.trim().isEmpty || !context.mounted) return;
          try {
            final p = await lib.createPlaylist(name.trim());
            if (context.mounted) context.open(PlaylistScreen(playlistId: p.id, name: p.name));
          } catch (e) {
            if (context.mounted) context.toast('$e');
          }
        },
        icon: const Icon(Icons.add),
        label: const Text('New'),
      ),
      body: RefreshIndicator(
        onRefresh: lib.refresh,
        child: lib.playlists.isEmpty
            ? ListView(children: const [SizedBox(height: 80), EmptyNote('No playlists yet.\nCreate one here, or add songs from any song menu.\nPlaylists live on your server, so every device sees them.', icon: Icons.queue_music)])
            : ListView(padding: const EdgeInsets.only(bottom: 90), children: [
                for (final p in lib.playlists)
                  ListTile(
                    leading: Cover(p.coverArt, size: 52),
                    title: Text(p.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
                    subtitle: Text('${p.songCount} songs${p.isFollow ? ' · followed' : ''}', style: const TextStyle(color: kMuted)),
                    onTap: () => context.open(PlaylistScreen(playlistId: p.id, name: p.name)),
                  ),
              ]),
      ),
    );
  }
}

class _FollowingTab extends StatelessWidget {
  const _FollowingTab();
  @override
  Widget build(BuildContext context) {
    final lib = context.watch<Library>();
    if (lib.follows.isEmpty) {
      return const EmptyNote('Follow an album, artist or playlist and it becomes its own playlist here, with every song downloaded to your server.', icon: Icons.favorite_border);
    }
    return ListView(children: [
      for (final f in lib.follows)
        ListTile(
          leading: Icon(f.kind == 'artist' ? Icons.person : f.kind == 'album' ? Icons.album : Icons.queue_music, size: 30),
          title: Text(f.name.isEmpty ? f.remoteId : f.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
          subtitle: f.status == 'error'
              ? const Text('Something went wrong. Tap retry.', style: TextStyle(color: Colors.orangeAccent))
              : f.active
                  ? Padding(padding: const EdgeInsets.only(top: 4), child: LinearProgressIndicator(value: f.total == 0 ? null : f.progress))
                  : Text('${f.kind} · ${f.done} of ${f.total} downloaded${f.failed > 0 ? ' · ${f.failed} failed' : ''}', style: const TextStyle(color: kMuted)),
          trailing: f.status == 'error'
              ? IconButton(
                  icon: const Icon(Icons.refresh),
                  onPressed: () async {
                    await context.api.retryFollow(f.id);
                    await lib.refresh();
                  },
                )
              : IconButton(
                  icon: const Icon(Icons.favorite, color: kAccent),
                  onPressed: () async {
                    final remove = await confirm(context, 'Unfollow "${f.name}"?', 'Its playlist and downloaded songs stay. It just stops updating.', ok: 'Unfollow');
                    if (remove && context.mounted) await lib.unfollow(f);
                  },
                ),
          onTap: f.playlistId.isEmpty ? null : () => context.open(PlaylistScreen(playlistId: f.playlistId, name: f.name)),
        ),
    ]);
  }
}

class _LikedTab extends StatelessWidget {
  const _LikedTab();
  @override
  Widget build(BuildContext context) {
    final lib = context.watch<Library>();
    final songs = lib.likedSongs;
    if (songs.isEmpty) return const EmptyNote('Songs you like show up here. Liking a YouTube song downloads it to your server.', icon: Icons.favorite_border);
    return ListView(children: [
      PlayButtons(songs: songs),
      for (var i = 0; i < songs.length; i++) SongTile(song: songs[i], onTap: () => context.player.playSongs(songs, start: i)),
      const SizedBox(height: 24),
    ]);
  }
}

class _AlbumsTab extends StatefulWidget {
  const _AlbumsTab();
  @override
  State<_AlbumsTab> createState() => _AlbumsTabState();
}

class _AlbumsTabState extends State<_AlbumsTab> with AutomaticKeepAliveClientMixin {
  List<Album>? albums;
  String? error;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final a = await context.api.albumList('alphabeticalByName', size: 500);
      if (mounted) setState(() => albums = a);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    if (error != null) return EmptyNote(error!, icon: Icons.error_outline);
    final a = albums;
    if (a == null) return const Center(child: CircularProgressIndicator());
    if (a.isEmpty) return const EmptyNote('No albums downloaded yet.', icon: Icons.album_outlined);
    return RefreshIndicator(
      onRefresh: _load,
      child: GridView.builder(
        padding: const EdgeInsets.all(12),
        gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(maxCrossAxisExtent: 180, childAspectRatio: 0.78),
        itemCount: a.length,
        itemBuilder: (_, i) => CardTile(
          coverId: a[i].coverArt,
          title: a[i].name,
          subtitle: a[i].artist,
          width: 170,
          onTap: () => context.open(AlbumScreen(albumId: a[i].id, name: a[i].name)),
        ),
      ),
    );
  }
}

class _ArtistsTab extends StatefulWidget {
  const _ArtistsTab();
  @override
  State<_ArtistsTab> createState() => _ArtistsTabState();
}

class _ArtistsTabState extends State<_ArtistsTab> with AutomaticKeepAliveClientMixin {
  List<Artist>? artists;
  String? error;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final a = await context.api.artists();
      if (mounted) setState(() => artists = a);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    if (error != null) return EmptyNote(error!, icon: Icons.error_outline);
    final a = artists;
    if (a == null) return const Center(child: CircularProgressIndicator());
    if (a.isEmpty) return const EmptyNote('No artists yet.', icon: Icons.person_outline);
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.builder(
        itemCount: a.length,
        itemBuilder: (_, i) => ListTile(
          leading: Cover(a[i].coverArt, size: 48, circle: true),
          title: Text(a[i].name, maxLines: 1, overflow: TextOverflow.ellipsis),
          subtitle: a[i].albumCount > 0 ? Text('${a[i].albumCount} albums', style: const TextStyle(color: kMuted)) : null,
          onTap: () => context.open(ArtistScreen(artistId: a[i].id, name: a[i].name)),
        ),
      ),
    );
  }
}

class _OfflineTab extends StatelessWidget {
  const _OfflineTab();
  @override
  Widget build(BuildContext context) {
    final off = context.watch<OfflineStore>();
    final songs = off.songs;
    if (songs.isEmpty && off.progress.isEmpty) {
      return const EmptyNote('Songs you save for offline listening live on this device only. Use the menu on any song.', icon: Icons.download_for_offline_outlined);
    }
    return ListView(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 8, 0),
        child: Row(children: [
          Expanded(child: Text('${songs.length} songs · ${fmtBytes(off.bytes)}', style: const TextStyle(color: kMuted))),
          TextButton(
            onPressed: songs.isEmpty
                ? null
                : () async {
                    final ok = await confirm(context, 'Remove all offline songs?', 'They stay on your server.', ok: 'Remove');
                    if (ok) await off.clear();
                  },
            child: const Text('Remove all'),
          ),
        ]),
      ),
      if (songs.isNotEmpty) PlayButtons(songs: songs),
      for (final id in off.progress.keys)
        ListTile(leading: const Icon(Icons.downloading), title: const Text('Saving...'), subtitle: LinearProgressIndicator(value: off.progress[id])),
      for (var i = 0; i < songs.length; i++) SongTile(song: songs[i], onTap: () => context.player.playSongs(songs, start: i)),
    ]);
  }
}
