// Talks to the selfstream server: the Subsonic API for music and selfstream's own /selfstream/api/v1.
// Pure Dart (http + crypto only), so the same file works on every platform.
import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;

import 'caps.dart';
import 'models.dart';

class ApiException implements Exception {
  ApiException(this.message, {this.status = 0});
  final String message;
  final int status;
  @override
  String toString() => message;
}

String normalizeServer(String input) {
  var s = input.trim();
  if (s.isEmpty) return s;
  if (!s.startsWith('http://') && !s.startsWith('https://')) s = 'http://$s';
  while (s.endsWith('/')) {
    s = s.substring(0, s.length - 1);
  }
  return s;
}

/// Where answers are kept so pages still open without a connection. Implemented per platform.
abstract class ResponseCache {
  Future<String?> read(String key);
  Future<void> write(String key, String body);
}

class Api {
  Api({required this.server, required this.user, required this.password, http.Client? client, this.cache})
      : _http = client ?? http.Client();

  final ResponseCache? cache;

  /// True while the last answers came from the saved copies because the server could not be reached.
  bool offline = false;
  bool _forcedOffline = false;

  /// The device says there is no network: fail fast and use saved copies instead of waiting for timeouts.
  void setForcedOffline(bool v) {
    _forcedOffline = v;
    _setOffline(v);
  }

  final StreamController<bool> _offlineChanges = StreamController<bool>.broadcast();
  Stream<bool> get offlineChanges => _offlineChanges.stream;

  void _setOffline(bool v) {
    if (offline != v) {
      offline = v;
      _offlineChanges.add(v);
    }
  }

  static const Set<String> _cacheableSub = {
    'search3', 'getArtist', 'getAlbum', 'getAlbumList2', 'getArtists', 'getStarred2', 'getLyricsBySongId',
  };
  static const Duration _cachedTimeout = Duration(seconds: 12);

  String _cacheKey(String path, Map<String, Object?> query) {
    final keys = query.keys.toList()..sort();
    return '$path?${keys.map((k) => '$k=${query[k]}').join('&')}';
  }

  Future<Map<String, dynamic>?> _fromCache(String key) async {
    try {
      final raw = await cache?.read(key);
      if (raw == null) return null;
      final v = jsonDecode(raw);
      return v is Map ? Map<String, dynamic>.from(v) : null;
    } catch (_) {
      return null;
    }
  }

  final String server;
  final String user;
  final String password;
  final http.Client _http;
  final Random _rng = Random.secure();

  static const String _clientName = 'selfstream-app';
  static const Duration _timeout = Duration(seconds: 40);

  Map<String, String> authParams() {
    final salt = List<String>.generate(10, (_) => _rng.nextInt(36).toRadixString(36)).join();
    final token = md5.convert(utf8.encode('$password$salt')).toString();
    return {'u': user, 't': token, 's': salt, 'v': '1.16.1', 'c': _clientName, 'f': 'json'};
  }

  Uri _uri(String path, Map<String, Object?> query) {
    final q = <String, String>{...authParams()};
    query.forEach((k, v) {
      if (v != null) q[k] = v.toString();
    });
    return Uri.parse('$server$path').replace(queryParameters: q);
  }

  // ------------------------------------------------------------ Subsonic
  Future<Map<String, dynamic>> sub(String endpoint, [Map<String, Object?> query = const {}]) async {
    final key = cache != null && _cacheableSub.contains(endpoint) ? _cacheKey('rest/$endpoint', query) : null;
    http.Response res;
    try {
      res = await _send('GET', _uri('/rest/$endpoint', query), timeout: key != null ? _cachedTimeout : null);
      if (key != null && res.statusCode >= 500) throw ApiException('Server error ${res.statusCode}', status: res.statusCode);
    } on ApiException catch (e) {
      if (key != null && (e.status == 0 || e.status >= 500)) {
        final saved = await _fromCache(key);
        if (saved != null) {
          _setOffline(true);
          return _unwrapSub(saved, 200);
        }
      }
      rethrow;
    }
    _setOffline(false);
    final body = _decode(res);
    final r = _unwrapSub(body, res.statusCode);
    if (key != null) unawaited(cache!.write(key, jsonEncode(body)).catchError((Object _) {}));
    return r;
  }

  Map<String, dynamic> _unwrapSub(Map<String, dynamic> body, int status) {
    final r = body['subsonic-response'];
    if (r is! Map) throw ApiException('Unexpected answer from the server', status: status);
    if (r['status'] != 'ok') {
      final e = r['error'];
      throw ApiException(e is Map ? '${e['message']}' : 'The server refused the request', status: status);
    }
    return Map<String, dynamic>.from(r);
  }

  Uri streamUri(String id, {int maxBitRate = 0}) =>
      _uri('/rest/stream', {'id': id, if (maxBitRate > 0) 'maxBitRate': maxBitRate});

  Uri coverUri(String id, {int size = 300}) => _uri('/rest/getCoverArt', {'id': id, 'size': size});

  /// Cover URL without the changing salt/token, to key image caches on.
  String coverKey(String id, int size) => '$server|$id|$size';

  Future<SearchResults> search(String query) async {
    final futures = await Future.wait([
      sub('search3', {'query': query, 'songCount': 25, 'artistCount': 8, 'albumCount': 12}),
      v1Get('remote/playlists', {'q': query}).catchError((Object _) => <String, dynamic>{'playlists': <dynamic>[]}),
    ]);
    final r = _asMap(futures[0]['searchResult3']);
    final pl = (_asMap(futures[1])['playlists'] as List? ?? [])
        .map((e) => RemotePlaylist(Map<String, dynamic>.from(e as Map)))
        .toList();
    return SearchResults(songsFrom(r['song']), artistsFrom(r['artist']), albumsFrom(r['album']), pl);
  }

  Future<List<String>> suggestions(String q) async =>
      ((await v1Get('suggest', {'q': q}))['suggestions'] as List? ?? []).map((e) => '$e').toList();

  Future<({Artist artist, List<Album> albums})> artist(String id) async {
    final a = _asMap((await sub('getArtist', {'id': id}))['artist']);
    return (artist: Artist.fromJson(a), albums: albumsFrom(a['album']));
  }

  Future<({Album album, List<Song> songs})> album(String id) async {
    final a = _asMap((await sub('getAlbum', {'id': id}))['album']);
    return (album: Album.fromJson(a), songs: songsFrom(a['song']));
  }

  Future<List<Album>> albumList(String type, {int size = 30, int offset = 0}) async {
    final r = _asMap((await sub('getAlbumList2', {'type': type, 'size': size, 'offset': offset}))['albumList2']);
    return albumsFrom(r['album']);
  }

  Future<List<Artist>> artists() async {
    final r = _asMap((await sub('getArtists'))['artists']);
    final out = <Artist>[];
    for (final idx in (r['index'] as List? ?? [])) {
      out.addAll(artistsFrom(_asMap(idx)['artist']));
    }
    return out;
  }

  Future<({List<Song> songs, List<Album> albums, List<Artist> artists})> starred() async {
    final r = _asMap((await sub('getStarred2'))['starred2']);
    return (songs: songsFrom(r['song']), albums: albumsFrom(r['album']), artists: artistsFrom(r['artist']));
  }

  Future<List<Song>> randomSongs({int size = 50}) async {
    final r = _asMap((await sub('getRandomSongs', {'size': size}))['randomSongs']);
    return songsFrom(r['song']);
  }

  Future<void> star(String id, {bool download = true}) async {
    await sub('star', {'id': id, if (!download) 'ssDownload': '0'});
  }

  Future<void> unstar(String id) async {
    await sub('unstar', {'id': id});
  }

  Future<void> scrobble(String id) async {
    await sub('scrobble', {'id': id, 'submission': 'true'});
  }

  /// The server's lyrics answer for a song (kept for offline use), or null if there is none.
  Future<Map<String, dynamic>?> lyricsRaw(String id) async {
    try {
      return _asMap((await sub('getLyricsBySongId', {'id': id}))['lyricsList']);
    } on ApiException {
      return null;
    }
  }

  static Lyrics? parseLyrics(Map<String, dynamic>? r) {
    if (r == null) return null;
    final list = r['structuredLyrics'] as List? ?? [];
    if (list.isEmpty) return null;
    final synced = list.cast<dynamic>().map(_asMap).firstWhere((l) => l['synced'] == true, orElse: () => _asMap(list.first));
    final lines = <LyricLine>[];
    for (final raw in (synced['line'] as List? ?? [])) {
      final l = _asMap(raw);
      lines.add(LyricLine(Duration(milliseconds: (l['start'] as num?)?.toInt() ?? 0), '${l['value'] ?? ''}'));
    }
    if (lines.isEmpty) return null;
    return Lyrics(lines, synced['synced'] == true);
  }

  Future<Lyrics?> lyrics(String id) async => parseLyrics(await lyricsRaw(id));

  // ------------------------------------------------------- selfstream v1
  static const String _v1 = '/selfstream/api/v1/';

  Future<Map<String, dynamic>> v1Get(String path, [Map<String, Object?> query = const {}]) =>
      _v1Call('GET', path, query: query);

  Future<Map<String, dynamic>> _v1Call(String method, String path,
      {Map<String, Object?> query = const {}, Object? body}) async {
    final key = cache != null && method == 'GET' && !path.startsWith('suggest') && path != 'ping'
        ? _cacheKey('v1/$path', {for (final e in query.entries) if (e.key != 'refresh') e.key: e.value})
        : null;
    http.Response res;
    try {
      res = await _send(method, _uri('$_v1$path', query), body: body, timeout: key != null ? _cachedTimeout : null);
      if (key != null && res.statusCode >= 500) throw ApiException('Server error ${res.statusCode}', status: res.statusCode);
    } on ApiException catch (e) {
      if (key != null && (e.status == 0 || e.status >= 500)) {
        final saved = await _fromCache(key);
        if (saved != null) {
          _setOffline(true);
          return saved;
        }
      }
      rethrow;
    }
    if (method == 'GET') _setOffline(false);
    final decoded = _decode(res);
    if (res.statusCode >= 400) {
      throw ApiException('${decoded['error'] ?? 'Request failed (${res.statusCode})'}', status: res.statusCode);
    }
    final emptyDiscover = path == 'discover' && (decoded['songs'] as List? ?? []).isEmpty;
    if (key != null && !emptyDiscover) unawaited(cache!.write(key, jsonEncode(decoded)).catchError((Object _) {}));
    return decoded;
  }

  /// Checks that this is a selfstream server and that the login works.
  Future<void> checkLogin() async {
    final res = await _send('GET', _uri('${_v1}ping', const {}));
    if (res.statusCode == 404) {
      throw ApiException('Connected, but this is not a selfstream server. Use the selfstream port (8080), not Navidrome\'s (4533).');
    }
    final body = _decode(res);
    if (body['selfstream'] != true) throw ApiException('This server is not selfstream.');
    if (body['authenticated'] != true) throw ApiException('Wrong username or password.', status: 401);
  }

  Future<List<PlaylistInfo>> playlists() async =>
      ((await v1Get('playlists'))['playlists'] as List? ?? []).map(PlaylistInfo.fromJson).toList();

  Future<PlaylistFull> playlist(String id) async => PlaylistFull.fromJson(await v1Get('playlists/$id'));

  Future<PlaylistInfo> createPlaylist(String name, {List<Song> songs = const []}) async => PlaylistInfo.fromJson(
      await _v1Call('POST', 'playlists', body: {'name': name, 'songs': songs.map((s) => s.toJson()).toList()}));

  Future<void> renamePlaylist(String id, String name) => _v1Call('PATCH', 'playlists/$id', body: {'name': name});

  Future<void> deletePlaylist(String id) => _v1Call('DELETE', 'playlists/$id');

  Future<void> addToPlaylist(String id, List<Song> songs) =>
      _v1Call('POST', 'playlists/$id/songs', body: {'songs': songs.map((s) => s.toJson()).toList()});

  Future<void> removeFromPlaylist(String id, int itemId) => _v1Call('DELETE', 'playlists/$id/songs/$itemId');

  Future<void> reorderPlaylist(String id, List<int> itemIds) =>
      _v1Call('PUT', 'playlists/$id/order', body: {'itemIds': itemIds});

  Future<void> reportPlay(Map<String, dynamic> play) => _v1Call('POST', 'plays', body: play);

  Future<void> reportPlays(List<Map<String, dynamic>> plays) => _v1Call('POST', 'plays/batch', body: {'plays': plays});

  Future<Stats> stats(String range) async =>
      Stats(await v1Get('stats', {'range': range, 'tz': DateTime.now().timeZoneOffset.inMinutes}));

  Future<HomeData> home() async =>
      HomeData.fromJson(await v1Get('home', {'tz': DateTime.now().timeZoneOffset.inMinutes}));

  /// What this server offers. A server that is not selfstream answers 404: then only the plain player parts are used.
  Future<Capabilities> capabilities() async {
    try {
      return Capabilities.fromJson(await v1Get('manifest'));
    } on ApiException catch (e) {
      if (e.status == 0) rethrow; // not reachable and nothing saved: the caller decides
      return Capabilities.generic;
    }
  }

  Future<ScreenData> screen(String id, [Map<String, Object?> params = const {}]) async =>
      ScreenData(await v1Get('screens/$id', {'tz': DateTime.now().timeZoneOffset.inMinutes, ...params}));

  Future<DiscoverData> discover({bool refresh = false}) async {
    final d = DiscoverData.fromJson(await v1Get('discover', {if (refresh) 'refresh': '1'}));
    if (d.songs.isEmpty && !d.needsHistory) {
      // YouTube Music gave nothing this time: keep showing the last mix
      final saved = await _fromCache(_cacheKey('v1/discover', const {}));
      if (saved != null) {
        final old = DiscoverData.fromJson(saved);
        if (old.songs.isNotEmpty) return old;
      }
    }
    return d;
  }

  Future<List<Follow>> follows() async =>
      ((await v1Get('follows'))['follows'] as List? ?? []).map(Follow.fromJson).toList();

  Future<Follow> follow(String kind, String id, String name, {String cover = '', String artist = ''}) async =>
      Follow.fromJson(await _v1Call('POST', 'follows', body: {'kind': kind, 'id': id, 'name': name, 'cover': cover, 'artist': artist}));

  Future<void> unfollow(String id, {bool deletePlaylist = false}) =>
      _v1Call('DELETE', 'follows/$id', query: {if (deletePlaylist) 'deletePlaylist': '1'});

  Future<void> retryFollow(String id) => _v1Call('POST', 'follows/$id/retry');

  Future<({List<Song> songs, String name, String author, String cover})> remotePlaylist(String id) async {
    final r = await v1Get('remote/playlists/$id');
    return (songs: songsFrom(r['songs']), name: '${r['name'] ?? ''}', author: '${r['author'] ?? ''}', cover: '${r['cover'] ?? ''}');
  }

  Future<Object?> getState(String key) async => (await v1Get('state/$key'))['value'];

  Future<void> putState(String key, Object? value) => _v1Call('PUT', 'state/$key', body: {'value': value});

  // ------------------------------------------------------------ plumbing
  Future<http.Response> _send(String method, Uri uri, {Object? body, Duration? timeout}) async {
    if (_forcedOffline) throw ApiException('You are offline');
    final limit = timeout ?? _timeout;
    try {
      final req = http.Request(method, uri);
      if (body != null) {
        req.headers['content-type'] = 'application/json';
        req.body = jsonEncode(body);
      }
      final streamed = await _http.send(req).timeout(limit);
      return await http.Response.fromStream(streamed).timeout(limit);
    } on ApiException {
      rethrow;
    } catch (e) {
      throw ApiException('Cannot reach the server ($e)');
    }
  }

  Map<String, dynamic> _decode(http.Response res) {
    try {
      final v = jsonDecode(utf8.decode(res.bodyBytes));
      return v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};
    } catch (_) {
      if (res.statusCode >= 400) throw ApiException('Server answered ${res.statusCode}', status: res.statusCode);
      return <String, dynamic>{};
    }
  }

  static Map<String, dynamic> _asMap(Object? v) => v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};

  void close() {
    _http.close();
    _offlineChanges.close();
  }
}
