import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../platform/platform.dart';
import '../state/offline.dart';
import '../state/presence.dart';
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
        const SectionHeader('Appearance'),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Wrap(spacing: 12, runSpacing: 12, children: [
            for (final p in palettes)
              InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: () {
                  settings.themeId = p.id;
                  WidgetsBinding.instance.addPostFrameCallback((_) {
                    if (context.mounted) rebuildAll(context);
                  });
                },
                child: Container(
                  width: 84,
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: p.card,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: settings.themeId == p.id ? kAccent : Colors.transparent, width: 2),
                  ),
                  child: Column(children: [
                    Container(
                      height: 34,
                      decoration: BoxDecoration(color: p.bg, borderRadius: BorderRadius.circular(8), border: Border.all(color: p.muted.withValues(alpha: 0.4))),
                      child: Center(child: Container(width: 16, height: 16, decoration: BoxDecoration(color: p.accent, shape: BoxShape.circle))),
                    ),
                    const SizedBox(height: 6),
                    Text(p.name, style: TextStyle(fontSize: 12, color: p.text, fontWeight: FontWeight.w600)),
                  ]),
                ),
              ),
          ]),
        ),
        const SizedBox(height: 8),
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
                      trailing: settings.maxBitRate == q.$1 ? Icon(Icons.check, color: kAccent) : null,
                      onTap: () => Navigator.of(sheet).pop(q.$1),
                    ),
                ]),
              ),
            );
            if (v != null) settings.maxBitRate = v;
          },
        ),
        SwitchListTile(
          secondary: const Icon(Icons.cloud_download_outlined),
          title: const Text('Liking downloads to the server'),
          subtitle: const Text('Off: a like is just remembered on your server for every device. Use Download in Liked to keep songs on this phone.'),
          value: settings.likeDownloadsToServer,
          onChanged: (v) => settings.likeDownloadsToServer = v,
        ),
        SwitchListTile(
          secondary: const Icon(Icons.insights),
          title: const Text('Record my listening'),
          subtitle: const Text('Feeds Activity and Discover. Stored only on your server.'),
          value: settings.trackListening,
          onChanged: (v) => settings.trackListening = v,
        ),
        const Divider(),
        if (PresenceService.supported) ...[
          const SectionHeader('Discord'),
          SwitchListTile(
            secondary: const Icon(Icons.sports_esports_outlined),
            title: const Text('Show what I am playing'),
            subtitle: const Text('Title, artist, cover and a YouTube link on your Discord profile. Needs the Discord app open.'),
            value: settings.discordPresence,
            onChanged: (v) => settings.discordPresence = v,
          ),
          ListTile(
            leading: const Icon(Icons.badge_outlined),
            title: const Text('Discord application ID'),
            subtitle: Text(settings.discordClientId.isEmpty ? 'Using the one set on the server' : settings.discordClientId),
            onTap: () async {
              final v = await askText(context, 'Discord application ID', 'From discord.com/developers (leave empty to use the server\'s)', initial: settings.discordClientId);
              if (v != null) settings.discordClientId = v;
            },
          ),
          const Divider(),
        ],
        if (!isDesktop) ...[
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
        ],
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
        Padding(
          padding: EdgeInsets.all(20),
          child: Text('Selfstream 0.3\nYour music, playlists and listening history live on your own server.', style: TextStyle(color: kMuted, fontSize: 12)),
        ),
      ]),
    );
  }
}
