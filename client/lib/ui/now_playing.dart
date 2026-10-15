import 'dart:async';

import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'package:provider/provider.dart';

import '../core/models.dart';
import '../state/library.dart';
import '../state/offline.dart';
import '../state/player.dart';
import 'artist_screen.dart';
import 'widgets.dart';

void openNowPlaying(BuildContext context) {
  Navigator.of(context).push(MaterialPageRoute<void>(fullscreenDialog: true, builder: (_) => const NowPlayingScreen()));
}

class NowPlayingScreen extends StatefulWidget {
  const NowPlayingScreen({super.key});
  @override
  State<NowPlayingScreen> createState() => _NowPlayingScreenState();
}

class _NowPlayingScreenState extends State<NowPlayingScreen> {
  bool showLyrics = false;
  final Map<String, Future<Lyrics?>> _lyrics = {};

  Future<Lyrics?> _lyricsFor(Song s) {
    final off = context.offline;
    final api = context.api;
    return _lyrics.putIfAbsent(s.id, () async => await off.lyricsFor(s.id) ?? await api.lyrics(s.id));
  }

  @override
  Widget build(BuildContext context) {
    final p = context.watch<PlayerController>();
    final lib = context.watch<Library>();
    final off = context.watch<OfflineStore>();
    final s = p.current;
    if (s == null) {
      return Scaffold(appBar: AppBar(), body: const EmptyNote('Nothing is playing', icon: Icons.music_note));
    }
    final width = MediaQuery.of(context).size.width;
    final coverSize = (width - 48).clamp(180.0, 420.0).toDouble();
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(icon: const Icon(Icons.keyboard_arrow_down, size: 30), onPressed: () => Navigator.of(context).pop()),
        title: Text('Now playing', style: TextStyle(fontSize: 14, color: kMuted)),
        centerTitle: true,
        actions: [
          IconButton(icon: const Icon(Icons.more_vert), onPressed: () => showSongMenu(context, s)),
        ],
      ),
      body: SafeArea(
        child: Column(children: [
          Expanded(
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 200),
              child: showLyrics
                  ? _LyricsView(key: ValueKey('l${s.id}'), future: _lyricsFor(s), player: p)
                  : Center(key: ValueKey('c${s.id}'), child: Cover(s.coverArt, size: coverSize, radius: 14)),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 8, 12, 0),
            child: Row(children: [
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(s.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
                  GestureDetector(
                    onTap: s.artistId.isEmpty
                        ? null
                        : () {
                            Navigator.of(context).pop();
                            context.open(ArtistScreen(artistId: s.artistId, name: s.artist));
                          },
                    child: Text(s.artist, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: kMuted, fontSize: 16)),
                  ),
                ]),
              ),
              IconButton(
                iconSize: 28,
                icon: Icon(lib.isLiked(s) ? Icons.favorite : Icons.favorite_border, color: lib.isLiked(s) ? kAccent : null),
                onPressed: () => likeWithToast(context, s),
              ),
            ]),
          ),
          _SeekBar(player: p, song: s),
          Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
            IconButton(icon: Icon(Icons.shuffle, color: p.shuffle ? kAccent : kMuted), onPressed: p.toggleShuffle),
            IconButton(iconSize: 38, icon: const Icon(Icons.skip_previous), onPressed: p.previous),
            _PlayButton(p),
            IconButton(iconSize: 38, icon: const Icon(Icons.skip_next), onPressed: p.next),
            IconButton(
              icon: Icon(p.loop == LoopMode.one ? Icons.repeat_one : Icons.repeat, color: p.loop == LoopMode.off ? kMuted : kAccent),
              onPressed: p.cycleLoop,
            ),
          ]),
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 4, 8, 8),
            child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
              IconButton(
                tooltip: 'Lyrics',
                icon: Icon(Icons.lyrics_outlined, color: showLyrics ? kAccent : kMuted),
                onPressed: () => setState(() => showLyrics = !showLyrics),
              ),
              IconButton(tooltip: 'Queue', icon: Icon(Icons.queue_music, color: kMuted), onPressed: () => _showQueue(context)),
              IconButton(
                tooltip: 'Sleep timer',
                icon: Icon(Icons.bedtime_outlined, color: p.sleepActive ? kAccent : kMuted),
                onPressed: () => _sleepDialog(context, p),
              ),
              IconButton(
                tooltip: 'Speed',
                icon: Icon(Icons.speed, color: p.speed == 1.0 ? kMuted : kAccent),
                onPressed: () => _speedDialog(context, p),
              ),
              IconButton(tooltip: 'Add to playlist', icon: Icon(Icons.playlist_add, color: kMuted), onPressed: () => showPlaylistPicker(context, [s])),
              IconButton(
                tooltip: 'Offline',
                icon: Icon(off.has(s.id) ? Icons.download_done : Icons.download_for_offline_outlined, color: off.has(s.id) ? kAccent : kMuted),
                onPressed: () async {
                  if (off.has(s.id)) {
                    await off.remove(s.id);
                  } else {
                    context.toast('Saving for offline');
                    await off.download(s);
                  }
                },
              ),
            ]),
          ),
          if (p.sleepActive)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(p.sleepAtTrackEnd ? 'Sleep: after this song' : 'Sleep in ${fmtDuration(p.sleepLeft?.inSeconds ?? 0)}', style: TextStyle(color: kAccent, fontSize: 12)),
            ),
        ]),
      ),
    );
  }

  Future<void> _sleepDialog(BuildContext context, PlayerController p) async {
    final choice = await showModalBottomSheet<int>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(padding: EdgeInsets.all(8), child: Text('Sleep timer', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700))),
          for (final m in const [15, 30, 45, 60, 90])
            ListTile(title: Text('$m minutes'), onTap: () => Navigator.of(sheet).pop(m)),
          ListTile(title: const Text('End of this song'), onTap: () => Navigator.of(sheet).pop(-1)),
          if (p.sleepActive) ListTile(title: const Text('Turn off'), onTap: () => Navigator.of(sheet).pop(0)),
        ]),
      ),
    );
    if (choice == null) return;
    if (choice == 0) {
      p.setSleep(null);
    } else if (choice == -1) {
      p.setSleep(null, atTrackEnd: true);
    } else {
      p.setSleep(Duration(minutes: choice));
    }
  }

  Future<void> _speedDialog(BuildContext context, PlayerController p) async {
    final v = await showModalBottomSheet<double>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(padding: EdgeInsets.all(8), child: Text('Playback speed', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700))),
          for (final sp in const [0.75, 1.0, 1.25, 1.5, 2.0])
            ListTile(title: Text('${sp}x'), trailing: p.speed == sp ? Icon(Icons.check, color: kAccent) : null, onTap: () => Navigator.of(sheet).pop(sp)),
        ]),
      ),
    );
    if (v != null) await p.setSpeed(v);
  }

  void _showQueue(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => const _QueueSheet(),
    );
  }
}

class _PlayButton extends StatelessWidget {
  const _PlayButton(this.p);
  final PlayerController p;
  @override
  Widget build(BuildContext context) {
    return StreamBuilder<PlayerState>(
      stream: p.player.playerStateStream,
      builder: (_, snap) {
        final st = snap.data;
        final loading = st != null && (st.processingState == ProcessingState.loading || st.processingState == ProcessingState.buffering) && st.playing;
        return Container(
          width: 68,
          height: 68,
          decoration: BoxDecoration(color: kText, shape: BoxShape.circle),
          child: loading
              ? Padding(padding: EdgeInsets.all(20), child: CircularProgressIndicator(strokeWidth: 3, color: kBg))
              : IconButton(iconSize: 38, color: kBg, icon: Icon(p.playing ? Icons.pause : Icons.play_arrow), onPressed: p.toggle),
        );
      },
    );
  }
}

class _SeekBar extends StatefulWidget {
  const _SeekBar({required this.player, required this.song});
  final PlayerController player;
  final Song song;
  @override
  State<_SeekBar> createState() => _SeekBarState();
}

class _SeekBarState extends State<_SeekBar> {
  double? drag;

  @override
  Widget build(BuildContext context) {
    final p = widget.player;
    return StreamBuilder<Duration?>(
      stream: p.player.durationStream,
      builder: (_, dSnap) {
        final total = (dSnap.data?.inMilliseconds ?? 0) > 0 ? dSnap.data!.inMilliseconds : widget.song.duration * 1000;
        return StreamBuilder<Duration>(
          stream: p.positionStream,
          builder: (_, pSnap) {
            final pos = (pSnap.data?.inMilliseconds ?? 0).clamp(0, total > 0 ? total : 1 << 30);
            final value = drag ?? pos.toDouble();
            return Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Column(children: [
                Slider(
                  value: total > 0 ? value.clamp(0.0, total.toDouble()).toDouble() : 0.0,
                  max: total > 0 ? total.toDouble() : 1,
                  onChanged: total > 0 ? (v) => setState(() => drag = v) : null,
                  onChangeEnd: (v) {
                    p.seek(Duration(milliseconds: v.round()));
                    setState(() => drag = null);
                  },
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                    Text(fmtDuration((value / 1000).round()), style: TextStyle(color: kMuted, fontSize: 12)),
                    Text(fmtDuration((total / 1000).round()), style: TextStyle(color: kMuted, fontSize: 12)),
                  ]),
                ),
              ]),
            );
          },
        );
      },
    );
  }
}

class _LyricsView extends StatefulWidget {
  const _LyricsView({super.key, required this.future, required this.player});
  final Future<Lyrics?> future;
  final PlayerController player;
  @override
  State<_LyricsView> createState() => _LyricsViewState();
}

class _LyricsViewState extends State<_LyricsView> {
  static const double rowHeight = 54;
  final ScrollController scroll = ScrollController();
  int lastActive = -1;
  StreamSubscription<Duration>? sub;

  @override
  void dispose() {
    sub?.cancel();
    scroll.dispose();
    super.dispose();
  }

  void _follow(Lyrics l) {
    if (sub != null || !l.synced) return;
    sub = widget.player.positionStream.listen((pos) {
      var active = -1;
      for (var i = 0; i < l.lines.length; i++) {
        if (l.lines[i].start <= pos) {
          active = i;
        } else {
          break;
        }
      }
      if (active != lastActive) {
        lastActive = active;
        if (mounted) setState(() {});
        if (active >= 0 && scroll.hasClients) {
          final target = (active * rowHeight - scroll.position.viewportDimension / 2 + rowHeight / 2).clamp(0.0, scroll.position.maxScrollExtent);
          scroll.animateTo(target, duration: const Duration(milliseconds: 350), curve: Curves.easeOut);
        }
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<Lyrics?>(
      future: widget.future,
      builder: (_, snap) {
        if (snap.connectionState != ConnectionState.done) return const Center(child: CircularProgressIndicator());
        final l = snap.data;
        if (l == null) return const EmptyNote('No lyrics found for this song', icon: Icons.lyrics_outlined);
        _follow(l);
        return ListView.builder(
          controller: scroll,
          itemExtent: rowHeight,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 24),
          itemCount: l.lines.length,
          itemBuilder: (_, i) {
            final active = i == lastActive;
            return InkWell(
              onTap: l.synced ? () => widget.player.seek(l.lines[i].start) : null,
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  l.lines[i].text.isEmpty ? '...' : l.lines[i].text,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: active ? 22 : 18,
                    fontWeight: FontWeight.w800,
                    color: !l.synced || active ? kText : kMuted,
                  ),
                ),
              ),
            );
          },
        );
      },
    );
  }
}

class _QueueSheet extends StatelessWidget {
  const _QueueSheet();

  @override
  Widget build(BuildContext context) {
    final p = context.watch<PlayerController>();
    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.75,
      child: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 8, 8),
          child: Row(children: [
            Expanded(child: Text('Queue (${p.queue.length})', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700))),
            TextButton(
              onPressed: p.queue.isEmpty ? null : () => showPlaylistPicker(context, p.queue),
              child: const Text('Save as playlist'),
            ),
            TextButton(onPressed: p.queue.isEmpty ? null : p.clearQueue, child: const Text('Clear')),
          ]),
        ),
        Expanded(
          child: ReorderableListView.builder(
            itemCount: p.queue.length,
            onReorder: p.reorder,
            itemBuilder: (_, i) {
              final s = p.queue[i];
              final now = i == p.index;
              return Dismissible(
                key: ValueKey('${s.id}#$i'),
                direction: DismissDirection.endToStart,
                background: Container(color: Colors.red.shade900, alignment: Alignment.centerRight, padding: const EdgeInsets.only(right: 20), child: const Icon(Icons.delete)),
                onDismissed: (_) => p.removeAt(i),
                child: ListTile(
                  dense: true,
                  onTap: () => p.jumpTo(i),
                  leading: Cover(s.coverArt, size: 44),
                  title: Text(s.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: now ? kAccent : null, fontWeight: FontWeight.w600)),
                  subtitle: Text(s.artist, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: kMuted)),
                  trailing: ReorderableDragStartListener(index: i, child: Icon(Icons.drag_handle, color: kMuted)),
                ),
              );
            },
          ),
        ),
      ]),
    );
  }
}
