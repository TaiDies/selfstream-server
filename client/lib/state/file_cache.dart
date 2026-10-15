import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:path_provider/path_provider.dart';

import '../core/api.dart';

/// Keeps server answers as small files so pages open without a connection.
class FileResponseCache implements ResponseCache {
  FileResponseCache(this.scope);
  final String scope; // server + user: two accounts never share saved pages
  Directory? _dir;
  int _writes = 0;

  Future<Directory?> _directory() async {
    if (_dir != null) return _dir;
    try {
      final base = await getApplicationSupportDirectory();
      final id = md5.convert(utf8.encode(scope)).toString().substring(0, 12);
      final d = Directory('${base.path}/cache/$id');
      await d.create(recursive: true);
      _dir = d;
    } catch (_) {
      _dir = null;
    }
    return _dir;
  }

  File _file(Directory d, String key) => File('${d.path}/${md5.convert(utf8.encode(key))}.json');

  @override
  Future<String?> read(String key) async {
    final d = await _directory();
    if (d == null) return null;
    final f = _file(d, key);
    return await f.exists() ? f.readAsString() : null;
  }

  @override
  Future<void> write(String key, String body) async {
    final d = await _directory();
    if (d == null) return;
    await _file(d, key).writeAsString(body);
    if (++_writes % 40 == 0) await _prune(d);
  }

  /// Keep the newest 600 answers.
  Future<void> _prune(Directory d) async {
    final files = <MapEntry<File, DateTime>>[];
    await for (final e in d.list()) {
      if (e is File) files.add(MapEntry(e, (await e.stat()).modified));
    }
    if (files.length <= 600) return;
    files.sort((a, b) => a.value.compareTo(b.value));
    for (final f in files.take(files.length - 600)) {
      try {
        await f.key.delete();
      } catch (_) {}
    }
  }
}
