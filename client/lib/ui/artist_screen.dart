import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../state/library.dart';
import 'album_screen.dart';
import 'widgets.dart';

class ArtistScreen extends StatefulWidget {
  const ArtistScreen({super.key, required this.artistId, this.name = ''});
  final String artistId;
  final String name;

  @override
  State<ArtistScreen> createState() => _ArtistScreenState();
}

class _ArtistScreenState extends State<ArtistScreen> {
  Artist? artist;
  List<Album> albums = [];
  String? error;
  bool loading = true;
  bool busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await context.api.artist(widget.artistId);
      if (!mounted) return;
      setState(() {
        artist = r.artist;
        albums = r.albums;
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

  /// Load every album and play everything in a random order.
  Future<void> _shuffleAll() async {
    setState(() => busy = true);
    try {
      final songs = <Song>[];
      final seen = <String>{};
      for (final a in albums.take(25)) {
        final r = await context.api.album(a.id);
        for (final s in r.songs) {
          if (seen.add(s.key)) songs.add(s);
        }
      }
      if (!mounted) return;
      if (songs.isEmpty) {
        context.toast('Nothing to play yet');
      } else {
        await context.player.playSongs(songs, shuffle: true);
      }
    } catch (e) {
      if (mounted) context.toast('$e');
    }
    if (mounted) setState(() => busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final lib = context.watch<Library>();
    final name = artist?.name ?? widget.name;
    final isCloud = widget.artistId.startsWith('ya-');
    final albumsOnly = albums;
    return PageFrame(
      title: name,
      body: loading
          ? const Center(child: CircularProgressIndicator())
          : error != null
              ? EmptyNote(error!, icon: Icons.error_outline)
              : ListView(children: [
                  Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(children: [
                      Cover(artist?.coverArt ?? widget.artistId, size: 140, circle: true),
                      const SizedBox(height: 12),
                      Text(name, style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800), textAlign: TextAlign.center),
                      Text('${albumsOnly.length} releases', style: TextStyle(color: kMuted)),
                    ]),
                  ),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Wrap(alignment: WrapAlignment.center, spacing: 8, runSpacing: 8, children: [
                      FilledButton.icon(
                        onPressed: busy || albums.isEmpty ? null : _shuffleAll,
                        icon: busy ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.shuffle),
                        label: const Text('Shuffle all'),
                      ),
                      if (isCloud)
                        FollowButton(kind: 'artist', remoteId: widget.artistId, name: name, cover: '', artist: name, follow: lib.followFor(widget.artistId)),
                    ]),
                  ),
                  if (isCloud)
                    Padding(
                      padding: EdgeInsets.fromLTRB(24, 8, 24, 0),
                      child: Text('Following downloads every song by this artist into your library and keeps it as a playlist.',
                          textAlign: TextAlign.center, style: TextStyle(color: kMuted, fontSize: 12)),
                    ),
                  const SectionHeader('Albums and singles'),
                  if (albumsOnly.isEmpty)
                    const EmptyNote('No releases found')
                  else
                    GridView.builder(
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(maxCrossAxisExtent: 180, childAspectRatio: 0.78),
                      itemCount: albumsOnly.length,
                      itemBuilder: (_, i) {
                        final a = albumsOnly[i];
                        return CardTile(
                          coverId: a.coverArt,
                          title: a.name,
                          subtitle: a.year > 0 ? '${a.year}' : '',
                          width: 170,
                          onTap: () => context.open(AlbumScreen(albumId: a.id, name: a.name)),
                        );
                      },
                    ),
                  const SizedBox(height: 24),
                ]),
    );
  }
}
