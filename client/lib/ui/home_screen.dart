import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../state/library.dart';
import '../state/offline.dart';
import 'playlist_screen.dart';
import 'settings_screen.dart';
import 'widgets.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.onOpenTab});
  final void Function(int) onOpenTab;
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  HomeData? data;
  String? error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await context.api.home();
      if (mounted) {
        setState(() {
          data = d;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
  }

  /// Nothing saved from the server yet and no connection: show what is on this device.
  List<Widget> _savedHere(BuildContext context) {
    final off = context.watch<OfflineStore>();
    final songs = off.songs;
    if (songs.isEmpty) return [EmptyNote(error!, icon: Icons.cloud_off)];
    return [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
        child: Text('Saved on this device', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
      ),
      PlayButtons(songs: songs),
      for (var i = 0; i < songs.length; i++) SongTile(song: songs[i], onTap: () => context.player.playSongs(songs, start: i)),
    ];
  }

  String _greeting() {
    final h = DateTime.now().hour;
    if (h < 5) return 'Late night';
    if (h < 12) return 'Good morning';
    if (h < 18) return 'Good afternoon';
    return 'Good evening';
  }

  @override
  Widget build(BuildContext context) {
    final lib = context.watch<Library>();
    final d = data;
    final active = lib.follows.where((f) => f.active).toList();
    return Scaffold(
      appBar: AppBar(
        title: Text(_greeting(), style: const TextStyle(fontWeight: FontWeight.w800)),
        actions: [
          IconButton(icon: const Icon(Icons.settings_outlined), onPressed: () => context.open(const SettingsScreen())),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          await Future.wait([_load(), lib.refresh()]);
        },
        child: ListView(children: [
          ReloadOnReconnect(onReconnect: _load, child: const SizedBox.shrink()),
          if (error != null && d == null) ..._savedHere(context),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
            child: Wrap(spacing: 8, runSpacing: 8, children: [
              ActionChip(
                avatar: const Icon(Icons.shuffle, size: 18),
                label: const Text('Shuffle my library'),
                onPressed: () async {
                  try {
                    final songs = await context.api.randomSongs(size: 60);
                    if (!context.mounted) return;
                    if (songs.isEmpty) {
                      context.toast('Your library is empty. Search for something and like it.');
                    } else {
                      await context.player.playSongs(songs);
                    }
                  } catch (e) {
                    if (context.mounted) context.toast('$e');
                  }
                },
              ),
              if (d != null)
                Chip(avatar: const Icon(Icons.schedule, size: 18), label: Text('${d.weekHours.toStringAsFixed(1)} h this week')),
              if (d != null && d.streak > 1)
                Chip(avatar: const Icon(Icons.local_fire_department, size: 18, color: Colors.orange), label: Text('${d.streak} day streak')),
            ]),
          ),
          if (active.isNotEmpty) ...[
            const SectionHeader('Downloading'),
            for (final f in active)
              ListTile(
                leading: const Icon(Icons.downloading),
                title: Text(f.name.isEmpty ? 'Preparing...' : f.name),
                subtitle: Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: LinearProgressIndicator(value: f.total == 0 ? null : f.progress),
                ),
                trailing: Text(f.total == 0 ? '' : '${f.done + f.failed}/${f.total}', style: TextStyle(color: kMuted)),
              ),
          ],
          if (d != null && d.recent.isNotEmpty) ...[
            const SectionHeader('Recently played'),
            HRow(children: [
              for (var i = 0; i < d.recent.length; i++)
                CardTile(coverId: d.recent[i].coverArt, title: d.recent[i].title, subtitle: d.recent[i].artist, onTap: () => context.player.playSongs(d.recent, start: i)),
            ]),
          ],
          if (lib.playlists.isNotEmpty) ...[
            SectionHeader('Your playlists', action: 'See all', onAction: () => widget.onOpenTab(2)),
            HRow(children: [
              for (final p in lib.playlists.take(12))
                CardTile(
                  coverId: p.coverArt,
                  title: p.name,
                  subtitle: '${p.songCount} songs',
                  onTap: () => context.open(PlaylistScreen(playlistId: p.id, name: p.name)),
                ),
            ]),
          ],
          if (d != null && d.mostPlayed.isNotEmpty) ...[
            const SectionHeader('Most played this week'),
            for (var i = 0; i < d.mostPlayed.length && i < 5; i++)
              SongTile(song: d.mostPlayed[i], number: i + 1, onTap: () => context.player.playSongs(d.mostPlayed, start: i)),
          ],
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
            child: Card(
              child: ListTile(
                leading: Icon(Icons.auto_awesome, color: kAccent),
                title: const Text('Your weekly Discover', style: TextStyle(fontWeight: FontWeight.w700)),
                subtitle: const Text('New songs picked from what you actually listen to'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => widget.onOpenTab(3),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Card(
              child: ListTile(
                leading: Icon(Icons.insights, color: kAccent),
                title: const Text('Your activity', style: TextStyle(fontWeight: FontWeight.w700)),
                subtitle: const Text('Hours listened, top songs, artists and genres'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => widget.onOpenTab(4),
              ),
            ),
          ),
          if (d != null && d.recent.isEmpty && lib.playlists.isEmpty)
            const EmptyNote('Search for a song, play it, and your history, playlists and recommendations start filling in here.', icon: Icons.headphones),
          const SizedBox(height: 24),
        ]),
      ),
    );
  }
}
