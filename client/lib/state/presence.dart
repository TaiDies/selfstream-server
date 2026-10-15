import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../core/api.dart';
import '../core/models.dart';
import '../platform/discord_ipc.dart';
import 'player.dart';
import 'settings.dart';

/// Shows what is playing on Discord (Windows). The server decides the text, cover picture and link;
/// this only hands the finished card to the Discord app.
class PresenceService {
  PresenceService(this.api, this.player, this.settings);
  final Api api;
  final PlayerController player;
  final Settings settings;

  DiscordIpc? _ipc;
  String _clientId = '';
  String _lastKey = '';
  Timer? _debounce;
  final Map<String, Map<String, dynamic>> _cards = {};
  bool _disposed = false;

  static bool get supported => Platform.isWindows;

  void start() {
    player.addListener(_changed);
    settings.addListener(_changed);
  }

  void _changed() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 600), _update);
  }

  Future<Map<String, dynamic>?> _card(Song s) async {
    final cached = _cards[s.id];
    if (cached != null) return cached;
    try {
      final c = await api.v1Get('presence', {'id': s.id, 'title': s.title, 'artist': s.artist, 'album': s.album});
      _cards[s.id] = c;
      if (_cards.length > 200) _cards.remove(_cards.keys.first);
      return c;
    } catch (_) {
      return null;
    }
  }

  Future<void> _update() async {
    if (_disposed) return;
    try {
      final song = player.current;
      final playing = player.playing && song != null && settings.discordPresence;
      final key = '${playing ? song.id : ''}|${settings.discordPresence}|${settings.discordClientId}';
      if (key == _lastKey) return;
      _lastKey = key;
      if (!playing) {
        await _ipc?.setActivity(null);
        if (!settings.discordPresence) {
          _ipc?.close();
          _ipc = null;
        }
        return;
      }
      final card = await _card(song);
      if (card == null || _disposed) return;
      final id = settings.discordClientId.isNotEmpty ? settings.discordClientId : '${card['clientId'] ?? ''}';
      if (id.isEmpty) return; // no Discord application configured
      if (id != _clientId) {
        _ipc?.close();
        _ipc = DiscordIpc(id);
        _clientId = id;
      }
      final now = DateTime.now().millisecondsSinceEpoch;
      final start = now - player.player.position.inMilliseconds;
      final image = '${card['largeImage'] ?? ''}';
      final buttons = (card['buttons'] as List? ?? []).whereType<Map>().take(2).map((b) => {'label': '${b['label']}', 'url': '${b['url']}'}).toList();
      final activity = <String, Object?>{
        'type': 2,
        'details': _pad('${card['details'] ?? song.title}'),
        'state': _pad('${card['state'] ?? song.artist}'),
        'timestamps': {'start': start, if (song.duration > 0) 'end': start + song.duration * 1000},
        if (image.isNotEmpty) 'assets': {'large_image': image, 'large_text': '${card['largeText'] ?? ''}'},
        if (buttons.isNotEmpty) 'buttons': buttons,
      };
      if (!await _ipc!.setActivity(activity)) _lastKey = ''; // Discord not running: try again on the next change
    } catch (e) {
      if (kDebugMode) debugPrint('presence: $e');
      _lastKey = '';
    }
  }

  String _pad(String s) => s.length >= 2 ? s : '$s ';

  void dispose() {
    _disposed = true;
    _debounce?.cancel();
    player.removeListener(_changed);
    settings.removeListener(_changed);
    unawaited(_ipc?.setActivity(null).then((_) => _ipc?.close()) ?? Future<void>.value());
  }
}
