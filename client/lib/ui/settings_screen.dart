import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/offline.dart';
import '../state/session.dart';
import '../state/settings.dart';
import 'widgets.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();
    final off = context.watch<OfflineStore>();
    final session = context.read<Session>();
    return PageFrame(
      title: 'Settings',
      body: ListView(children: [
        ListTile(
          leading: const Icon(Icons.dns_outlined),
          title: Text(session.server),
          subtitle: Text('Signed in as ${session.user}'),
        ),
        const Divider(),
        const SectionHeader('Playback'),
        ListTile(
          leading: const Icon(Icons.speed),
          title: const Text('Streaming quality'),
          subtitle: Text(settings.maxBitRate == 0 ? 'Original (best)' : 'Up to ${settings.maxBitRate} kbps. Saves data on mobile.'),
          onTap: () async {
            final v = await showModalBottomSheet<int>(
              context: context,
              showDragHandle: true,
              builder: (sheet) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  for (final q in const [(0, 'Original (best)'), (320, '320 kbps'), (192, '192 kbps'), (128, '128 kbps')])
                    ListTile(
                      title: Text(q.$2),
                      trailing: settings.maxBitRate == q.$1 ? const Icon(Icons.check, color: kAccent) : null,
                      onTap: () => Navigator.of(sheet).pop(q.$1),
                    ),
                ]),
              ),
            );
            if (v != null) settings.maxBitRate = v;
          },
        ),
        SwitchListTile(
          secondary: const Icon(Icons.insights),
          title: const Text('Record my listening'),
          subtitle: const Text('Feeds Activity and Discover. Stored only on your server.'),
          value: settings.trackListening,
          onChanged: (v) => settings.trackListening = v,
        ),
        const Divider(),
        const SectionHeader('Storage on this device'),
        ListTile(
          leading: const Icon(Icons.download_for_offline_outlined),
          title: const Text('Offline songs'),
          subtitle: Text('${off.songs.length} songs · ${fmtBytes(off.bytes)}'),
          trailing: TextButton(
            onPressed: off.songs.isEmpty
                ? null
                : () async {
                    if (await confirm(context, 'Remove all offline songs?', 'They stay on your server.', ok: 'Remove')) await off.clear();
                  },
            child: const Text('Clear'),
          ),
        ),
        const Divider(),
        const SectionHeader('Account'),
        ListTile(
          leading: const Icon(Icons.logout),
          title: const Text('Sign out'),
          onTap: () async {
            if (!await confirm(context, 'Sign out?', 'Your playlists and history stay on the server.', ok: 'Sign out')) return;
            if (!context.mounted) return;
            Navigator.of(context).popUntil((r) => r.isFirst);
            await session.signOut();
          },
        ),
        const Padding(
          padding: EdgeInsets.all(20),
          child: Text('Selfstream 0.3\nYour music, playlists and listening history live on your own server.', style: TextStyle(color: kMuted, fontSize: 12)),
        ),
      ]),
    );
  }
}
