import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

import '../core/api.dart';
import '../core/models.dart';
import 'settings.dart';

/// Songs saved on this device so they play without a connection.
class OfflineStore extends ChangeNotifier {
  OfflineStore(this.api, this.settings);
  final Api api;
  final Settings settings;

  Directory? _dir;
  final Map<String, Map<String, dynamic>> _index = {}; // song id -> {file, song}
  final Map<String, double> progress = {}; // song id -> 0..1 while downloading
  final Map<String, String> failed = {};

  Future<void> load() async {
    try {
      final base = await getApplicationDocumentsDirectory();
      _dir = Directory('${base.path}/offline');
      await _dir!.create(recursive: true);
      final f = File('${_dir!.path}/index.json');
      if (await f.exists()) {
        final data = jsonDecode(await f.readAsString()) as Map;
        data.forEach((k, v) {
          final entry = Map<String, dynamic>.from(v as Map);
          if (File('${_dir!.path}/${entry['file']}').existsSync()) _index['$k'] = entry;
        });
      }
    } catch (_) {
      _dir = null;
    }
    notifyListeners();
    unawaited(_backfillLyrics());
  }

  File? _lyricsFile(String id) {
    final e = _index[id];
    return e == null || _dir == null ? null : File('${_dir!.path}/${e['file']}.lyrics.json');
  }

  /// Lyrics saved next to the audio, so they show without a connection.
  Future<Lyrics?> lyricsFor(String id) async {
    try {
      final f = _lyricsFile(id);
      if (f == null || !await f.exists()) return null;
      return Api.parseLyrics(Map<String, dynamic>.from(jsonDecode(await f.readAsString()) as Map));
    } catch (_) {
      return null;
    }
  }

  Future<void> _saveLyrics(String id) async {
    final f = _lyricsFile(id);
    if (f == null || await f.exists()) return;
    final raw = await api.lyricsRaw(id);
    if (raw != null && (raw['structuredLyrics'] as List? ?? []).isNotEmpty) await f.writeAsString(jsonEncode(raw));
  }

  /// Songs saved before lyrics were kept: fetch theirs now, quietly.
  Future<void> _backfillLyrics() async {
    for (final id in _index.keys.toList()) {
      try {
        await _saveLyrics(id);
      } catch (_) {}
    }
  }

  bool get available => _dir != null;
  bool has(String id) => _index.containsKey(id);

  String? pathFor(String id) {
    final e = _index[id];
    return e == null || _dir == null ? null : '${_dir!.path}/${e['file']}';
  }

  List<Song> get songs => _index.values.map((e) => Song.fromJson(e['song'])).toList();

  int get bytes {
    var total = 0;
    for (final e in _index.values) {
      try {
        total += File('${_dir!.path}/${e['file']}').lengthSync();
      } catch (_) {}
    }
    return total;
  }

  Future<void> _saveIndex() async {
    if (_dir == null) return;
    await File('${_dir!.path}/index.json').writeAsString(jsonEncode(_index));
  }

  Future<void> download(Song s) async {
    if (_dir == null || has(s.id) || progress.containsKey(s.id)) return;
    progress[s.id] = 0;
    failed.remove(s.id);
    notifyListeners();
    final name = '${s.id.replaceAll(RegExp(r'[^A-Za-z0-9_-]'), '_')}.audio';
    final file = File('${_dir!.path}/$name');
    final client = http.Client();
    try {
      final res = await client.send(http.Request('GET', api.streamUri(s.id, maxBitRate: settings.maxBitRate)));
      if (res.statusCode >= 300) throw ApiException('Server answered ${res.statusCode}');
      final sink = file.openWrite();
      var received = 0;
      final total = res.contentLength ?? 0;
      await for (final chunk in res.stream) {
        sink.add(chunk);
        received += chunk.length;
        if (total > 0) {
          progress[s.id] = received / total;
          notifyListeners();
        }
      }
      await sink.close();
      if (received < 1000) throw ApiException('Empty answer');
      _index[s.id] = {'file': name, 'song': s.toJson()};
      await _saveIndex();
      try {
        await _saveLyrics(s.id);
      } catch (_) {}
    } catch (e) {
      failed[s.id] = e.toString();
      try {
        if (await file.exists()) await file.delete();
      } catch (_) {}
    } finally {
      client.close();
      progress.remove(s.id);
      notifyListeners();
    }
  }

  Future<void> downloadAll(Iterable<Song> songs) async {
    for (final s in songs) {
      await download(s);
    }
  }

  Future<void> remove(String id) async {
    final e = _index.remove(id);
    if (e != null && _dir != null) {
      try {
        await File('${_dir!.path}/${e['file']}').delete();
        final l = File('${_dir!.path}/${e['file']}.lyrics.json');
        if (await l.exists()) await l.delete();
      } catch (_) {}
      await _saveIndex();
    }
    notifyListeners();
  }

  Future<void> clear() async {
    for (final id in _index.keys.toList()) {
      await remove(id);
    }
  }
}
