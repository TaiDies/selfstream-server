import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api.dart';
import '../core/models.dart';
import '../state/library.dart';
import '../state/offline.dart';
import '../state/player.dart';
import 'artist_screen.dart';
import 'album_screen.dart';
import 'now_playing.dart';

const Color kAccent = Color(0xFF1ED760);
const Color kBg = Color(0xFF0C0C10);
const Color kCard = Color(0xFF181820);
const Color kMuted = Color(0xFF9A9AA8);

ThemeData buildTheme() {
  final scheme = ColorScheme.fromSeed(seedColor: kAccent, brightness: Brightness.dark).copyWith(
    primary: kAccent,
    surface: kBg,
    onPrimary: Colors.black,
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: kBg,
    appBarTheme: const AppBarTheme(backgroundColor: kBg, surfaceTintColor: Colors.transparent, elevation: 0),
    navigationBarTheme: const NavigationBarThemeData(backgroundColor: Color(0xFF111116), surfaceTintColor: Colors.transparent),
    cardTheme: const CardThemeData(color: kCard, surfaceTintColor: Colors.transparent, elevation: 0),
    bottomSheetTheme: const BottomSheetThemeData(backgroundColor: Color(0xFF16161C), surfaceTintColor: Colors.transparent),
    dialogTheme: const DialogThemeData(backgroundColor: Color(0xFF16161C), surfaceTintColor: Colors.transparent),
    sliderTheme: const SliderThemeData(trackHeight: 3, thumbShape: RoundSliderThumbShape(enabledThumbRadius: 6)),
  );
}

extension AppContext on BuildContext {
  Api get api => read<Api>();
  Library get lib => read<Library>();
  PlayerController get player => read<PlayerController>();
  OfflineStore get offline => read<OfflineStore>();

  void toast(String message) {
    ScaffoldMessenger.of(this)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message), duration: const Duration(seconds: 3)));
  }

  Future<T?> open<T>(Widget page) => Navigator.of(this).push<T>(MaterialPageRoute<T>(builder: (_) => page));
}

String fmtDuration(int seconds) {
  final m = seconds ~/ 60;
  final s = seconds % 60;
  if (m >= 60) return '${m ~/ 60}:${(m % 60).toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  return '$m:${s.toString().padLeft(2, '0')}';
}

String fmtListening(int seconds) {
  final h = seconds ~/ 3600;
  final m = (seconds % 3600) ~/ 60;
  if (h > 0) return '${h}h ${m}m';
  if (m > 0) return '${m}m';
  return '${seconds}s';
}

String fmtBytes(int b) {
  if (b > 1024 * 1024 * 1024) return '${(b / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
  if (b > 1024 * 1024) return '${(b / (1024 * 1024)).toStringAsFixed(0)} MB';
  return '${(b / 1024).toStringAsFixed(0)} KB';
}

// ------------------------------------------------------------------ cover
class Cover extends StatelessWidget {
  const Cover(this.id, {super.key, this.size = 56, this.radius = 6, this.circle = false});
  final String id;
  final double size;
  final double radius;
  final bool circle;

  @override
  Widget build(BuildContext context) {
    final api = context.read<Api>();
    final px = (size * 2).clamp(120, 800).round();
    final placeholder = Container(
      width: size,
      height: size,
      color: kCard,
      child: Icon(Icons.music_note, color: kMuted, size: size * 0.4),
    );
    final Widget image = id.isEmpty
        ? placeholder
        : CachedNetworkImage(
            imageUrl: api.coverUri(id, size: px).toString(),
            cacheKey: api.coverKey(id, px),
            width: size,
            height: size,
            fit: BoxFit.cover,
            fadeInDuration: const Duration(milliseconds: 120),
            placeholder: (_, __) => placeholder,
            errorWidget: (_, __, ___) => placeholder,
          );
    return circle
        ? ClipOval(child: image)
        : ClipRRect(borderRadius: BorderRadius.circular(radius), child: image);
  }
}

// ------------------------------------------------------------ section bits
class SectionHeader extends StatelessWidget {
  const SectionHeader(this.title, {super.key, this.action, this.onAction});
  final String title;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 8, 8),
      child: Row(
        children: [
          Expanded(child: Text(title, style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700))),
          if (action != null) TextButton(onPressed: onAction, child: Text(action!)),
        ],
      ),
    );
  }
}

class CardTile extends StatelessWidget {
  const CardTile({super.key, required this.coverId, required this.title, this.subtitle = '', this.onTap, this.circle = false, this.width = 140});
  final String coverId;
  final String title;
  final String subtitle;
  final VoidCallback? onTap;
  final bool circle;
  final double width;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: onTap,
      child: SizedBox(
        width: width,
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Column(
            crossAxisAlignment: circle ? CrossAxisAlignment.center : CrossAxisAlignment.start,
            children: [
              Cover(coverId, size: width - 8, radius: 8, circle: circle),
              const SizedBox(height: 6),
              Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
              if (subtitle.isNotEmpty)
                Text(subtitle, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: kMuted, fontSize: 12)),
            ],
          ),
        ),
      ),
    );
  }
}

class HRow extends StatelessWidget {
  const HRow({super.key, required this.children, this.height = 190});
  final List<Widget> children;
  final double height;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        itemCount: children.length,
        separatorBuilder: (_, __) => const SizedBox(width: 6),
        itemBuilder: (_, i) => children[i],
      ),
    );
  }
}

class EmptyNote extends StatelessWidget {
  const EmptyNote(this.text, {super.key, this.icon = Icons.music_off_outlined});
  final String text;
  final IconData icon;
  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 44, color: kMuted),
            const SizedBox(height: 12),
            Text(text, textAlign: TextAlign.center, style: const TextStyle(color: kMuted)),
          ]),
        ),
      );
}

// --------------------------------------------------------------- song rows
class SongTile extends StatelessWidget {
  const SongTile({
    super.key,
    required this.song,
    required this.onTap,
    this.number,
    this.showCover = true,
    this.showReason = false,
    this.menuExtras = const [],
  });
  final Song song;
  final VoidCallback onTap;
  final int? number;
  final bool showCover;
  final bool showReason;
  final List<SongAction> menuExtras;

  @override
  Widget build(BuildContext context) {
    final player = context.watch<PlayerController>();
    final lib = context.watch<Library>();
    final off = context.watch<OfflineStore>();
    final playingNow = player.current?.id == song.id;
    final sub = showReason && song.reason.isNotEmpty ? '${song.artist} · ${song.reason}' : song.artist;
    return ListTile(
      onTap: onTap,
      dense: true,
      leading: number != null && !showCover
          ? SizedBox(width: 28, child: Center(child: Text('$number', style: const TextStyle(color: kMuted))))
          : Cover(song.coverArt, size: 48),
      title: Text(song.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: playingNow ? kAccent : null, fontWeight: FontWeight.w600)),
      subtitle: Row(children: [
        if (off.has(song.id)) const Padding(padding: EdgeInsets.only(right: 4), child: Icon(Icons.download_done, size: 14, color: kAccent)),
        if (song.isCloud) const Padding(padding: EdgeInsets.only(right: 4), child: Icon(Icons.cloud_outlined, size: 13, color: kMuted)),
        Expanded(child: Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: kMuted))),
      ]),
      trailing: Row(mainAxisSize: MainAxisSize.min, children: [
        if (song.duration > 0) Text(fmtDuration(song.duration), style: const TextStyle(color: kMuted, fontSize: 12)),
        IconButton(
          visualDensity: VisualDensity.compact,
          icon: Icon(lib.isLiked(song) ? Icons.favorite : Icons.favorite_border, color: lib.isLiked(song) ? kAccent : kMuted, size: 22),
          onPressed: () => likeWithToast(context, song),
        ),
        IconButton(
          visualDensity: VisualDensity.compact,
          icon: const Icon(Icons.more_vert, color: kMuted, size: 22),
          onPressed: () => showSongMenu(context, song, extras: menuExtras),
        ),
      ]),
    );
  }
}

Future<void> likeWithToast(BuildContext context, Song s) async {
  final lib = context.read<Library>();
  final wasLiked = lib.isLiked(s);
  try {
    await lib.toggleLike(s);
    if (context.mounted) {
      context.toast(wasLiked ? 'Removed from liked songs' : (s.isCloud ? 'Liked, downloading to your library' : 'Added to liked songs'));
    }
  } catch (e) {
    if (context.mounted) context.toast('Could not update: $e');
  }
}

class SongAction {
  const SongAction(this.icon, this.label, this.run);
  final IconData icon;
  final String label;
  final Future<void> Function() run;
}

Future<void> showSongMenu(BuildContext context, Song s, {List<SongAction> extras = const []}) async {
  final player = context.read<PlayerController>();
  final offline = context.read<OfflineStore>();
  await showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (sheet) {
      Widget item(IconData icon, String label, Future<void> Function() run) => ListTile(
            leading: Icon(icon),
            title: Text(label),
            onTap: () async {
              Navigator.of(sheet).pop();
              await run();
            },
          );
      return SafeArea(
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            ListTile(
              leading: Cover(s.coverArt, size: 48),
              title: Text(s.title, maxLines: 1, overflow: TextOverflow.ellipsis),
              subtitle: Text(s.artist, maxLines: 1, overflow: TextOverflow.ellipsis),
            ),
            const Divider(height: 1),
            item(Icons.playlist_play, 'Play next', () async {
              await player.playNext(s);
              if (context.mounted) context.toast('Playing next');
            }),
            item(Icons.queue_music, 'Add to queue', () async {
              await player.addToQueue(s);
              if (context.mounted) context.toast('Added to queue');
            }),
            item(Icons.playlist_add, 'Add to playlist', () async {
              await showPlaylistPicker(context, [s]);
            }),
            item(offline.has(s.id) ? Icons.delete_outline : Icons.download_for_offline_outlined,
                offline.has(s.id) ? 'Remove from this device' : 'Save for offline', () async {
              if (offline.has(s.id)) {
                await offline.remove(s.id);
              } else {
                if (context.mounted) context.toast('Saving for offline');
                await offline.download(s);
              }
            }),
            if (s.artistId.isNotEmpty)
              item(Icons.person_outline, 'Go to artist', () async {
                if (context.mounted) await context.open(ArtistScreen(artistId: s.artistId, name: s.artist));
              }),
            if (s.albumId.isNotEmpty)
              item(Icons.album_outlined, 'Go to album', () async {
                if (context.mounted) await context.open(AlbumScreen(albumId: s.albumId, name: s.album));
              }),
            for (final a in extras) item(a.icon, a.label, a.run),
          ]),
        ),
      );
    },
  );
}

/// Pick one of your playlists (or make a new one) and add [songs] to it.
Future<void> showPlaylistPicker(BuildContext context, List<Song> songs) async {
  final lib = context.read<Library>();
  final picked = await showModalBottomSheet<Object>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (sheet) => SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(sheet).size.height * 0.7),
        child: ListView(shrinkWrap: true, children: [
          const Padding(padding: EdgeInsets.fromLTRB(16, 0, 16, 8), child: Text('Add to playlist', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700))),
          ListTile(
            leading: const Icon(Icons.add_circle_outline),
            title: const Text('New playlist'),
            onTap: () => Navigator.of(sheet).pop('new'),
          ),
          for (final p in lib.playlists)
            ListTile(
              leading: Cover(p.coverArt, size: 44),
              title: Text(p.name, maxLines: 1, overflow: TextOverflow.ellipsis),
              subtitle: Text('${p.songCount} songs', style: const TextStyle(color: kMuted)),
              onTap: () => Navigator.of(sheet).pop(p),
            ),
        ]),
      ),
    ),
  );
  if (picked == null || !context.mounted) return;
  try {
    if (picked == 'new') {
      final name = await askText(context, 'New playlist', 'Name');
      if (name == null || name.trim().isEmpty) return;
      await lib.createPlaylist(name.trim(), songs: songs);
      if (context.mounted) context.toast('Created "${name.trim()}"');
    } else if (picked is PlaylistInfo) {
      await lib.addToPlaylist(picked, songs);
      if (context.mounted) context.toast('Added to ${picked.name}');
    }
  } catch (e) {
    if (context.mounted) context.toast('Could not add: $e');
  }
}

Future<String?> askText(BuildContext context, String title, String hint, {String initial = ''}) {
  final ctl = TextEditingController(text: initial);
  return showDialog<String>(
    context: context,
    builder: (d) => AlertDialog(
      title: Text(title),
      content: TextField(controller: ctl, autofocus: true, decoration: InputDecoration(hintText: hint), onSubmitted: (v) => Navigator.of(d).pop(v)),
      actions: [
        TextButton(onPressed: () => Navigator.of(d).pop(), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.of(d).pop(ctl.text), child: const Text('OK')),
      ],
    ),
  );
}

Future<bool> confirm(BuildContext context, String title, String body, {String ok = 'OK'}) async {
  final r = await showDialog<bool>(
    context: context,
    builder: (d) => AlertDialog(
      title: Text(title),
      content: Text(body),
      actions: [
        TextButton(onPressed: () => Navigator.of(d).pop(false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.of(d).pop(true), child: Text(ok)),
      ],
    ),
  );
  return r ?? false;
}

// --------------------------------------------------------------- mini player
class MiniPlayer extends StatelessWidget {
  const MiniPlayer({super.key});

  @override
  Widget build(BuildContext context) {
    final p = context.watch<PlayerController>();
    final s = p.current;
    if (s == null) return const SizedBox.shrink();
    return Material(
      color: const Color(0xFF1B1B23),
      child: InkWell(
        onTap: () => openNowPlaying(context),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          StreamBuilder<Duration>(
            stream: p.positionStream,
            builder: (_, snap) {
              final pos = snap.data?.inMilliseconds ?? 0;
              final total = (s.duration > 0 ? s.duration * 1000 : (p.player.duration?.inMilliseconds ?? 0));
              return LinearProgressIndicator(value: total > 0 ? (pos / total).clamp(0.0, 1.0) : 0, minHeight: 2, backgroundColor: Colors.white10);
            },
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(10, 6, 4, 6),
            child: Row(children: [
              Cover(s.coverArt, size: 44),
              const SizedBox(width: 10),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(s.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
                  Text(s.artist, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: kMuted, fontSize: 12)),
                ]),
              ),
              IconButton(icon: const Icon(Icons.skip_previous), onPressed: p.previous),
              IconButton(icon: Icon(p.playing ? Icons.pause_circle_filled : Icons.play_circle_fill, size: 36), onPressed: p.toggle),
              IconButton(icon: const Icon(Icons.skip_next), onPressed: p.next),
            ]),
          ),
        ]),
      ),
    );
  }
}

/// A page with the mini player at the bottom.
class PageFrame extends StatelessWidget {
  const PageFrame({super.key, required this.title, required this.body, this.actions = const []});
  final String title;
  final Widget body;
  final List<Widget> actions;
  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text(title), actions: actions),
        body: body,
        bottomNavigationBar: const MiniPlayer(),
      );
}

/// Play / shuffle buttons used on album, playlist and artist pages.
class PlayButtons extends StatelessWidget {
  const PlayButtons({super.key, required this.songs, this.extra = const []});
  final List<Song> songs;
  final List<Widget> extra;
  @override
  Widget build(BuildContext context) {
    final player = context.read<PlayerController>();
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
        FilledButton.icon(
          onPressed: songs.isEmpty ? null : () => player.playSongs(songs),
          icon: const Icon(Icons.play_arrow),
          label: const Text('Play'),
        ),
        OutlinedButton.icon(
          onPressed: songs.isEmpty ? null : () => player.playSongs(songs, shuffle: true),
          icon: const Icon(Icons.shuffle),
          label: const Text('Shuffle'),
        ),
        ...extra,
      ]),
    );
  }
}
