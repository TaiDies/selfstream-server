import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api.dart';
import '../core/caps.dart';
import '../core/models.dart';
import '../platform/platform.dart';
import '../state/offline.dart';
import 'album_screen.dart';
import 'artist_screen.dart';
import 'playlist_screen.dart';
import 'remote_playlist_screen.dart';
import 'settings_screen.dart';
import 'widgets.dart';

typedef TabOpener = void Function(String tabId);

/// Names the server may use for icons. Anything else gets a plain dot.
IconData iconFor(String name) => switch (name) {
      'shuffle' => Icons.shuffle,
      'schedule' => Icons.schedule,
      'fire' => Icons.local_fire_department,
      'discover' => Icons.auto_awesome,
      'activity' => Icons.insights,
      'headphones' => Icons.headphones,
      'refresh' => Icons.refresh,
      'playlist_add' => Icons.playlist_add,
      'home' => Icons.home,
      'search' => Icons.search,
      'library' => Icons.library_music,
      'play' => Icons.play_arrow,
      'favorite' => Icons.favorite,
      'download' => Icons.download_for_offline_outlined,
      _ => Icons.circle_outlined,
    };

/// (outlined, filled) icons for the bottom bar / side rail.
(IconData, IconData) tabIconsFor(String name) => switch (name) {
      'home' => (Icons.home_outlined, Icons.home),
      'search' => (Icons.search, Icons.search),
      'library' => (Icons.library_music_outlined, Icons.library_music),
      'discover' => (Icons.auto_awesome_outlined, Icons.auto_awesome),
      'activity' => (Icons.insights_outlined, Icons.insights),
      'favorite' => (Icons.favorite_border, Icons.favorite),
      _ => (Icons.circle_outlined, Icons.circle),
    };

/// A page the server described as sections. The app only knows how to draw the section types, not what they mean.
class ServerScreen extends StatefulWidget {
  const ServerScreen({super.key, required this.screenId, required this.title, required this.onOpenTab, this.loader});
  final String screenId;
  final String title;
  final TabOpener onOpenTab;

  /// Builds the page on this device instead (used against a plain Navidrome server).
  final Future<ScreenData> Function(Api api)? loader;

  @override
  State<ServerScreen> createState() => _ServerScreenState();
}

class _ServerScreenState extends State<ServerScreen> with AutomaticKeepAliveClientMixin {
  ScreenData? data;
  String? error;
  bool loading = false;
  final Map<String, String> _params = {};

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load([Map<String, String> extra = const {}]) async {
    for (final e in extra.entries) {
      if (e.key != 'refresh') _params[e.key] = e.value; // 'refresh' only applies once
    }
    setState(() {
      loading = true;
      error = null;
    });
    final api = context.read<Api>();
    try {
      final d = widget.loader != null ? await widget.loader!(api) : await api.screen(widget.screenId, {..._params, ...extra});
      if (mounted) setState(() => data = d);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  String _greeting() {
    final h = DateTime.now().hour;
    if (h < 5) return 'Late night';
    if (h < 12) return 'Good morning';
    if (h < 18) return 'Good afternoon';
    return 'Good evening';
  }

  Future<void> _run(Map<String, dynamic> a) async {
    final player = context.player;
    switch ('${a['type']}') {
      case 'play':
        final songs = songsFrom(a['songs']);
        if (songs.isNotEmpty) await player.playSongs(songs, shuffle: a['shuffle'] == true);
      case 'tab':
        widget.onOpenTab('${a['tab']}');
      case 'open':
        _open('${a['kind']}', '${a['id']}', '${a['name'] ?? ''}');
      case 'reload':
        await _load({for (final e in ((a['params'] as Map?) ?? {}).entries) '${e.key}': '${e.value}'});
      case 'randomSongs':
        try {
          final songs = await context.api.randomSongs(size: 60);
          if (!mounted) return;
          if (songs.isEmpty) {
            context.toast('Your library is empty. Search for something and like it.');
          } else {
            await player.playSongs(songs);
          }
        } catch (e) {
          if (mounted) context.toast('$e');
        }
      case 'savePlaylist':
        try {
          await context.lib.createPlaylist('${a['name']}', songs: songsFrom(a['songs']));
          if (mounted) context.toast('Saved as a playlist');
        } catch (e) {
          if (mounted) context.toast('$e');
        }
    }
  }

  void _open(String kind, String id, String name) {
    if (id.isEmpty) return;
    switch (kind) {
      case 'album':
        context.open(AlbumScreen(albumId: id, name: name));
      case 'artist':
        context.open(ArtistScreen(artistId: id, name: name));
      case 'playlist':
        context.open(PlaylistScreen(playlistId: id, name: name));
      case 'remotePlaylist':
        context.open(RemotePlaylistScreen(playlistId: id, name: name));
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final d = data;
    final title = (d?.title.isNotEmpty ?? false) ? d!.title.replaceAll('{greeting}', _greeting()) : widget.title.replaceAll('{greeting}', _greeting());
    return Scaffold(
      appBar: AppBar(
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
        actions: [
          for (final a in d?.actions ?? const <Map<String, dynamic>>[])
            IconButton(
              tooltip: '${a['tooltip'] ?? ''}',
              icon: Icon(iconFor('${a['icon']}')),
              onPressed: loading ? null : () => _run(Map<String, dynamic>.from(a['action'] as Map)),
            ),
          if (d?.settings ?? false) IconButton(icon: const Icon(Icons.settings_outlined), onPressed: () => context.open(const SettingsScreen())),
        ],
      ),
      body: ReloadOnReconnect(
        onReconnect: _load,
        child: RefreshIndicator(
          onRefresh: _load,
          child: ListView(children: [
            if (loading && d == null) const Padding(padding: EdgeInsets.all(48), child: Center(child: CircularProgressIndicator())),
            if (loading && d != null) const LinearProgressIndicator(minHeight: 2),
            if (error != null && d == null) ..._savedHere(context),
            if (d != null) ...[for (final s in d.sections) _section(context, s)],
            const SizedBox(height: 24),
          ]),
        ),
      ),
    );
  }

  /// No page saved yet and no connection: show what is on this device.
  List<Widget> _savedHere(BuildContext context) {
    final songs = context.watch<OfflineStore>().songs;
    if (songs.isEmpty || isDesktop) return [EmptyNote(error!, icon: Icons.cloud_off)];
    return [
      const SectionHeader('Saved on this device'),
      PlayButtons(songs: songs),
      for (var i = 0; i < songs.length; i++) SongTile(song: songs[i], onTap: () => context.player.playSongs(songs, start: i)),
    ];
  }

  VoidCallback? _tap(Map<String, dynamic>? action) => action == null ? null : () => _run(action);

  Widget _section(BuildContext context, Map<String, dynamic> s) {
    switch ('${s['type']}') {
      case 'header':
        final action = s['action'] is Map ? Map<String, dynamic>.from(s['action'] as Map) : null;
        final sub = '${s['subtitle'] ?? ''}';
        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (s['big'] == true)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
              child: Text('${s['title']}', style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800)),
            )
          else
            SectionHeader('${s['title']}',
                action: action == null ? null : '${action['label']}',
                onAction: action == null ? null : () => _run(Map<String, dynamic>.from(action['action'] as Map))),
          if (sub.isNotEmpty) Padding(padding: const EdgeInsets.fromLTRB(16, 4, 16, 0), child: Text(sub, style: TextStyle(color: kMuted))),
        ]);
      case 'pills':
        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: Wrap(spacing: 8, runSpacing: 8, children: [
            for (final i in _list(s['items']))
              Chip(
                avatar: Icon(iconFor('${i['icon']}'), size: 18, color: '${i['icon']}' == 'fire' ? Colors.orange : null),
                label: Text('${i['label']}'),
              ),
          ]),
        );
      case 'buttons':
        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
          child: Wrap(spacing: 8, runSpacing: 8, children: [
            for (final i in _list(s['items']))
              ActionChip(
                avatar: Icon(iconFor('${i['icon']}'), size: 18),
                label: Text('${i['label']}'),
                onPressed: _tap(i['action'] is Map ? Map<String, dynamic>.from(i['action'] as Map) : null),
              ),
          ]),
        );
      case 'link':
        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: Card(
            child: ListTile(
              leading: Icon(iconFor('${s['icon']}'), color: kAccent),
              title: Text('${s['title']}', style: const TextStyle(fontWeight: FontWeight.w700)),
              subtitle: Text('${s['subtitle'] ?? ''}'),
              trailing: const Icon(Icons.chevron_right),
              onTap: _tap(s['action'] is Map ? Map<String, dynamic>.from(s['action'] as Map) : null),
            ),
          ),
        );
      case 'chips':
        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
          child: Wrap(spacing: 8, children: [
            for (final o in _list(s['options']))
              ChoiceChip(
                label: Text('${o['label']}'),
                selected: (_params['${s['param']}'] ?? '${s['selected']}') == '${o['id']}',
                onSelected: (_) => _load({'${s['param']}': '${o['id']}'}),
              ),
          ]),
        );
      case 'hero':
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('${s['value']}', style: TextStyle(fontSize: 56, fontWeight: FontWeight.w900, color: kAccent, height: 1)),
                Text('${s['label']}', style: TextStyle(color: kMuted, fontSize: 16)),
                if ('${s['note'] ?? ''}'.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text('${s['note']}', style: TextStyle(color: kMuted, fontSize: 12)),
                ],
              ]),
            ),
          ),
        );
      case 'tiles':
        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: GridView.count(
            crossAxisCount: 3,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 8,
            crossAxisSpacing: 8,
            childAspectRatio: 1.35,
            children: [for (final i in _list(s['items'])) _Tile('${i['value']}', '${i['label']}')],
          ),
        );
      case 'bars':
        return _Bars(
          title: '${s['title'] ?? ''}',
          values: [for (final v in (s['values'] as List? ?? [])) (v as num).toInt()],
          labels: [for (final v in (s['labels'] as List? ?? [])) '$v'],
        );
      case 'songs':
        final songs = songsFrom(s['songs']);
        final extra = _list(s['extra']);
        return Column(children: [
          if (s['buttons'] == true)
            PlayButtons(songs: songs, extra: [
              for (final e in extra)
                OutlinedButton.icon(
                  onPressed: songs.isEmpty ? null : _tap(e['action'] is Map ? Map<String, dynamic>.from(e['action'] as Map) : null),
                  icon: Icon(iconFor('${e['icon']}')),
                  label: Text('${e['label']}'),
                ),
            ]),
          for (var i = 0; i < songs.length; i++)
            SongTile(
              song: songs[i],
              number: s['numbered'] == true ? i + 1 : null,
              showReason: s['showReason'] == true,
              onTap: () => context.player.playSongs(songs, start: i),
            ),
        ]);
      case 'songcards':
        final songs = songsFrom(s['songs']);
        return HRow(children: [
          for (var i = 0; i < songs.length; i++)
            CardTile(coverId: songs[i].coverArt, title: songs[i].title, subtitle: songs[i].artist, onTap: () => context.player.playSongs(songs, start: i)),
        ]);
      case 'cards':
        return HRow(children: [
          for (final i in _list(s['items']))
            CardTile(
              coverId: '${i['cover'] ?? ''}',
              title: '${i['title']}',
              subtitle: '${i['subtitle'] ?? ''}',
              circle: i['circle'] == true,
              onTap: '${i['id'] ?? ''}'.isEmpty ? null : () => _open('${i['kind']}', '${i['id']}', '${i['title']}'),
            ),
        ]);
      case 'ranks':
        final items = _list(s['items']);
        return Column(children: [
          for (var i = 0; i < items.length; i++)
            _RankRow(
              rank: i + 1,
              title: '${items[i]['title']}',
              subtitle: '${items[i]['subtitle'] ?? ''}',
              fraction: (items[i]['fraction'] as num?)?.toDouble() ?? 0,
              leading: '${items[i]['cover'] ?? ''}'.isEmpty ? null : Cover('${items[i]['cover']}', size: 44, circle: items[i]['circle'] == true),
            ),
        ]);
      case 'note':
        return Padding(padding: const EdgeInsets.only(top: 24), child: EmptyNote('${s['text']}', icon: iconFor('${s['icon']}')));
      case 'text':
        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
          child: Text('${s['text']}', style: TextStyle(color: kMuted, fontSize: s['small'] == true ? 11 : 14)),
        );
    }
    return const SizedBox.shrink(); // a section type this app version does not know
  }

  static List<Map<String, dynamic>> _list(Object? v) =>
      v is List ? v.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList() : <Map<String, dynamic>>[];
}

/// Home page for a plain Navidrome server: built here from its album lists.
Future<ScreenData> nativeHome(Api api) async {
  Future<List<Album>> safe(String type) => api.albumList(type, size: 20).catchError((Object _) => <Album>[]);
  final lists = await Future.wait([safe('recent'), safe('newest'), safe('random')]);
  Map<String, dynamic> cards(List<Album> a) => {
        'type': 'cards',
        'items': [for (final x in a) {'kind': 'album', 'id': x.id, 'title': x.name, 'subtitle': x.artist, 'cover': x.coverArt}],
      };
  final sections = <Map<String, dynamic>>[
    {
      'type': 'buttons',
      'items': [
        {'label': 'Shuffle my library', 'icon': 'shuffle', 'action': {'type': 'randomSongs'}},
      ],
    },
  ];
  const titles = ['Recently played', 'Newly added', 'Something different'];
  for (var i = 0; i < lists.length; i++) {
    if (lists[i].isEmpty) continue;
    sections..add({'type': 'header', 'title': titles[i]})..add(cards(lists[i]));
  }
  if (sections.length == 1) {
    sections.add({'type': 'note', 'icon': 'headphones', 'text': 'Your library is empty. Add music to the server and it shows up here.'});
  }
  return ScreenData({'title': '{greeting}', 'settings': true, 'sections': sections});
}

class _Tile extends StatelessWidget {
  const _Tile(this.value, this.label);
  final String value;
  final String label;
  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.start, children: [
            FittedBox(fit: BoxFit.scaleDown, child: Text(value, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w800))),
            Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: kMuted, fontSize: 12)),
          ]),
        ),
      );
}

class _Bars extends StatelessWidget {
  const _Bars({required this.title, required this.values, required this.labels});
  final String title;
  final List<int> values;
  final List<String> labels;

  @override
  Widget build(BuildContext context) {
    final peak = values.isEmpty ? 1 : values.reduce((a, b) => a > b ? a : b);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (title.isNotEmpty) Padding(padding: const EdgeInsets.only(bottom: 6), child: Text(title, style: TextStyle(color: kMuted, fontSize: 12))),
        SizedBox(
          height: 90,
          child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            for (var i = 0; i < values.length; i++)
              Expanded(
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: values.length > 40 ? 0.3 : 1.5),
                  child: Container(
                    height: peak == 0 ? 2.0 : (86 * values[i] / peak).clamp(2.0, 86.0).toDouble(),
                    decoration: BoxDecoration(color: values[i] == peak && peak > 0 ? kAccent : kAccent.withValues(alpha: 0.45), borderRadius: BorderRadius.circular(2)),
                  ),
                ),
              ),
          ]),
        ),
        const SizedBox(height: 4),
        Row(children: [
          for (var i = 0; i < values.length; i++)
            Expanded(child: Text(i < labels.length ? labels[i] : '', textAlign: TextAlign.center, maxLines: 1, overflow: TextOverflow.visible, softWrap: false, style: TextStyle(color: kMuted, fontSize: 9))),
        ]),
      ]),
    );
  }
}

class _RankRow extends StatelessWidget {
  const _RankRow({required this.rank, required this.title, required this.subtitle, required this.fraction, this.leading});
  final int rank;
  final String title;
  final String subtitle;
  final double fraction;
  final Widget? leading;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        child: Row(children: [
          SizedBox(width: 26, child: Text('$rank', style: TextStyle(color: kMuted, fontWeight: FontWeight.w700))),
          if (leading != null) ...[leading!, const SizedBox(width: 12)],
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              ClipRRect(
                borderRadius: BorderRadius.circular(3),
                child: LinearProgressIndicator(value: fraction.isNaN ? 0.02 : fraction.clamp(0.02, 1.0).toDouble(), minHeight: 5, backgroundColor: kFaint),
              ),
              const SizedBox(height: 3),
              Text(subtitle, style: TextStyle(color: kMuted, fontSize: 12)),
            ]),
          ),
        ]),
      );
}
