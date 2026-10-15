import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/desktop_window.dart';
import '../state/player.dart';
import 'widgets.dart';

/// The small always-handy player window.
class MiniWindow extends StatelessWidget {
  const MiniWindow({super.key});

  @override
  Widget build(BuildContext context) {
    final p = context.watch<PlayerController>();
    final s = p.current;
    return ListenableBuilder(
      listenable: desktopWindow,
      builder: (context, _) => Scaffold(
        body: Padding(
          padding: const EdgeInsets.all(10),
          child: Row(children: [
            if (s != null) Cover(s.coverArt, size: 120, radius: 8) else Container(width: 120, height: 120, color: kCard, child: Icon(Icons.music_note, color: kMuted)),
            const SizedBox(width: 12),
            Expanded(
              child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(s?.title ?? 'Nothing playing', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15)),
                Text(s?.artist ?? '', maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: kMuted, fontSize: 13)),
                const SizedBox(height: 4),
                Row(children: [
                  IconButton(visualDensity: VisualDensity.compact, icon: const Icon(Icons.skip_previous), onPressed: s == null ? null : p.previous),
                  IconButton(
                      visualDensity: VisualDensity.compact,
                      icon: Icon(p.playing ? Icons.pause_circle_filled : Icons.play_circle_fill, size: 34),
                      onPressed: s == null ? null : p.toggle),
                  IconButton(visualDensity: VisualDensity.compact, icon: const Icon(Icons.skip_next), onPressed: s == null ? null : p.next),
                ]),
              ]),
            ),
            Column(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
              IconButton(
                tooltip: desktopWindow.pinned ? 'Unpin from top' : 'Keep on top',
                visualDensity: VisualDensity.compact,
                icon: Icon(desktopWindow.pinned ? Icons.push_pin : Icons.push_pin_outlined, color: desktopWindow.pinned ? kAccent : kMuted),
                onPressed: desktopWindow.togglePin,
              ),
              IconButton(
                tooltip: 'Back to the full app',
                visualDensity: VisualDensity.compact,
                icon: Icon(Icons.open_in_full, color: kMuted),
                onPressed: desktopWindow.toggleMini,
              ),
            ]),
          ]),
        ),
      ),
    );
  }
}
