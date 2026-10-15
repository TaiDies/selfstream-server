import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api.dart';
import '../core/models.dart';
import '../state/desktop_window.dart';
import '../state/offline.dart';
import '../state/player.dart';
import 'now_playing.dart';
import 'widgets.dart';

/// The compact player window: cover, controls, volume and (optionally) synced lyrics.
class MiniWindow extends StatefulWidget {
  const MiniWindow({super.key});
  @override
  State<MiniWindow> createState() => _MiniWindowState();
}

class _MiniWindowState extends State<MiniWindow> {
  final Map<String, Future<Lyrics?>> _lyrics = {};

  Future<Lyrics?> _lyricsFor(Song s) {
    final api = context.read<Api>();
    final off = context.read<OfflineStore>();
    return _lyrics.putIfAbsent(s.id, () async => await off.lyricsFor(s.id) ?? await api.lyrics(s.id));
  }

  @override
  Widget build(BuildContext context) {
    final p = context.watch<PlayerController>();
    final s = p.current;
    return ListenableBuilder(
      listenable: desktopWindow,
      builder: (context, _) => Scaffold(
        body: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 8, 8),
          child: Column(children: [
            Row(children: [
              if (s != null) Cover(s.coverArt, size: 140, radius: 10) else Container(width: 140, height: 140, color: kCard, child: Icon(Icons.music_note, color: kMuted)),
              const SizedBox(width: 14),
              Expanded(
                child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(s?.title ?? 'Nothing playing', maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 17)),
                  Text(s?.artist ?? '', maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: kMuted, fontSize: 14)),
                  const SizedBox(height: 6),
                  Row(children: [
                    IconButton(visualDensity: VisualDensity.compact, icon: const Icon(Icons.skip_previous), onPressed: s == null ? null : p.previous),
                    IconButton(
                        icon: Icon(p.playing ? Icons.pause_circle_filled : Icons.play_circle_fill, size: 42),
                        onPressed: s == null ? null : p.toggle),
                    IconButton(visualDensity: VisualDensity.compact, icon: const Icon(Icons.skip_next), onPressed: s == null ? null : p.next),
                  ]),
                  const VolumeControl(width: 120),
                ]),
              ),
              Column(children: [
                IconButton(
                  tooltip: desktopWindow.pinned ? 'Unpin from top' : 'Keep on top',
                  visualDensity: VisualDensity.compact,
                  icon: Icon(desktopWindow.pinned ? Icons.push_pin : Icons.push_pin_outlined, color: desktopWindow.pinned ? kAccent : kMuted),
                  onPressed: desktopWindow.togglePin,
                ),
                IconButton(
                  tooltip: desktopWindow.lyrics ? 'Hide lyrics' : 'Show lyrics',
                  visualDensity: VisualDensity.compact,
                  icon: Icon(Icons.lyrics_outlined, color: desktopWindow.lyrics ? kAccent : kMuted),
                  onPressed: desktopWindow.toggleLyrics,
                ),
                IconButton(
                  tooltip: 'Back to the full app',
                  visualDensity: VisualDensity.compact,
                  icon: Icon(Icons.open_in_full, color: kMuted),
                  onPressed: desktopWindow.toggleMini,
                ),
              ]),
            ]),
            StreamBuilder<Duration>(
              stream: p.positionStream,
              builder: (_, snap) {
                final pos = snap.data?.inMilliseconds ?? 0;
                final total = s == null ? 0 : (s.duration > 0 ? s.duration * 1000 : (p.player.duration?.inMilliseconds ?? 0));
                return Padding(
                  padding: const EdgeInsets.only(top: 6, right: 4),
                  child: LinearProgressIndicator(value: total > 0 ? (pos / total).clamp(0.0, 1.0) : 0, minHeight: 3, backgroundColor: kFaint),
                );
              },
            ),
            if (desktopWindow.lyrics)
              Expanded(
                child: s == null
                    ? const SizedBox.shrink()
                    : LyricsView(key: ValueKey('mini${s.id}'), future: _lyricsFor(s), player: p),
              ),
          ]),
        ),
      ),
    );
  }
}
