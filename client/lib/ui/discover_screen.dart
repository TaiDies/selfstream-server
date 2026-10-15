import 'package:flutter/material.dart';

import '../core/models.dart';
import 'widgets.dart';

class DiscoverScreen extends StatefulWidget {
  const DiscoverScreen({super.key});
  @override
  State<DiscoverScreen> createState() => _DiscoverScreenState();
}

class _DiscoverScreenState extends State<DiscoverScreen> with AutomaticKeepAliveClientMixin {
  DiscoverData? data;
  String? error;
  bool loading = false;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool refresh = false}) async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final d = await context.api.discover(refresh: refresh);
      if (mounted) setState(() => data = d);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
    if (mounted) setState(() => loading = false);
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final d = data;
    final songs = d?.songs ?? [];
    return Scaffold(
      appBar: AppBar(
        title: const Text('Discover', style: TextStyle(fontWeight: FontWeight.w800)),
        actions: [
          IconButton(
            tooltip: 'New mix',
            icon: const Icon(Icons.refresh),
            onPressed: loading ? null : () => _load(refresh: true),
          ),
        ],
      ),
      body: loading && d == null
          ? Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                CircularProgressIndicator(),
                SizedBox(height: 16),
                Text('Finding songs you will like...', style: TextStyle(color: kMuted)),
              ]),
            )
          : error != null && d == null
              ? EmptyNote(error!, icon: Icons.error_outline)
              : d == null
                  ? const SizedBox.shrink()
                  : d.needsHistory
                      ? const EmptyNote('Listen to a few songs first.\nYour weekly Discover is built from what you play, how long you play it, and what you skip.', icon: Icons.auto_awesome)
                      : RefreshIndicator(
                          onRefresh: () => _load(),
                          child: ListView(children: [
                            Padding(
                              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                              child: Text('Week ${d.week.split('-W').last} mix', style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800)),
                            ),
                            if (d.seeds.isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                                child: Text('Based on ${d.seeds.take(3).join(', ')}${d.seeds.length > 3 ? ' and more' : ''}', style: TextStyle(color: kMuted)),
                              ),
                            if (loading) const Padding(padding: EdgeInsets.only(top: 8), child: LinearProgressIndicator(minHeight: 2)),
                            PlayButtons(songs: songs, extra: [
                              OutlinedButton.icon(
                                onPressed: songs.isEmpty
                                    ? null
                                    : () async {
                                        try {
                                          await context.lib.createPlaylist('Discover week ${d.week.split('-W').last}', songs: songs);
                                          if (context.mounted) context.toast('Saved as a playlist');
                                        } catch (e) {
                                          if (context.mounted) context.toast('$e');
                                        }
                                      },
                                icon: const Icon(Icons.playlist_add),
                                label: const Text('Save as playlist'),
                              ),
                            ]),
                            if (songs.isEmpty) const EmptyNote('YouTube Music did not return suggestions right now. Try the refresh button.'),
                            for (var i = 0; i < songs.length; i++)
                              SongTile(song: songs[i], showReason: true, onTap: () => context.player.playSongs(songs, start: i)),
                            Padding(
                              padding: EdgeInsets.all(20),
                              child: Text('Tip: like the songs you enjoy. Liked songs are downloaded to your server and teach Discover what you want more of.', style: TextStyle(color: kMuted, fontSize: 12)),
                            ),
                          ]),
                        ),
    );
  }
}
