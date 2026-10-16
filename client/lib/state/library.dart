import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../core/api.dart';
import '../core/models.dart';

/// Your playlists, followed albums/artists/playlists and liked songs, shared by every screen.
class Library extends ChangeNotifier {
  Library(this.api) {
    _offlineSub = api.offlineChanges.listen((_) {
      epoch++;
      _safeNotify();
    });
  }
  final Api api;

  /// Asked when liking a YouTube song: also download it into the server library?
  bool Function() downloadOnLike = () => false;
  StreamSubscription<bool>? _offlineSub;

  /// True while pages come from saved copies.
  bool get offline => api.offline;

  /// Does the connected server offer this feature? (set by the session once it knows)
  bool Function(String) has = (_) => false;

  /// Goes up whenever the connection comes or goes, so open pages can reload themselves.
  int epoch = 0;
  static const _snapshotKey = 'library/snapshot';

  /// Last saved playlists, follows and liked songs, shown before (or without) the network.
  Future<void> loadSaved() async {
    try {
      final raw = await api.cache?.read(_snapshotKey);
      if (raw == null) return;
      final m = jsonDecode(raw) as Map;
      playlists = (m['playlists'] as List? ?? []).map(PlaylistInfo.fromJson).toList();
      follows = (m['follows'] as List? ?? []).map(Follow.fromJson).toList();
      likedSongs = songsFrom(m['liked']);
      _indexLiked();
      _safeNotify();
    } catch (_) {}
  }

  void _saveSnapshot() {
    try {
      final body = jsonEncode({
        'playlists': playlists.map((p) => p.json).toList(),
        'follows': follows.map((f) => f.json).toList(),
        'liked': likedSongs.map((s) => s.toJson()).toList(),
      });
      unawaited(api.cache?.write(_snapshotKey, body).catchError((Object _) {}));
    } catch (_) {}
  }

  void _indexLiked() {
    _likedIds
      ..clear()
      ..addAll(likedSongs.map((s) => s.id));
    _likedKeys
      ..clear()
      ..addAll(likedSongs.map((s) => s.key));
  }

  List<PlaylistInfo> playlists = [];
  List<Follow> follows = [];
  List<Song> likedSongs = [];
  bool loading = false;
  String? error;

  final Set<String> _likedIds = {};
  final Set<String> _likedKeys = {};
  Timer? _poll;
  bool _disposed = false;

  Future<void> refresh() async {
    loading = true;
    notifyListeners();
    // each part on its own: one failing call must not wipe the others
    Object? firstError;
    Future<void> part(Future<void> Function() f) async {
      try {
        await f();
      } catch (e) {
        firstError ??= e;
      }
    }

    await Future.wait([
      if (has('playlists')) part(() async => playlists = await api.playlists()),
      if (has('follows')) part(() async => follows = await api.follows()),
      part(() async {
        likedSongs = (await api.starred()).songs;
        _indexLiked();
      }),
    ]);
    error = firstError?.toString();
    if (firstError == null) {
      _saveSnapshot();
      unawaited(_prefetchPlaylists());
    }
    loading = false;
    _safeNotify();
  }

  /// Open every playlist once in the background so its songs are saved for offline use too.
  Future<void> _prefetchPlaylists() async {
    if (!has('playlists')) return;
    for (final p in playlists.take(40)) {
      if (_disposed || api.offline) return;
      try {
        await api.playlist(p.id);
      } catch (_) {}
    }
  }

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  /// While something is downloading, keep progress fresh.
  void startPolling() {
    _poll?.cancel();
    _poll = Timer.periodic(const Duration(seconds: 6), (_) async {
      if (!has('follows') || !follows.any((f) => f.active)) return;
      try {
        follows = await api.follows();
        playlists = await api.playlists();
        _safeNotify();
      } catch (_) {}
    });
  }

  // -------------------------------------------------------------- likes
  bool isLiked(Song s) => _likedIds.contains(s.id) || _likedKeys.contains(s.key);

  Future<void> toggleLike(Song s) async {
    final liked = isLiked(s);
    if (liked) {
      _likedIds.remove(s.id);
      _likedKeys.remove(s.key);
    } else {
      _likedIds.add(s.id);
      _likedKeys.add(s.key);
    }
    notifyListeners();
    try {
      if (liked) {
        await api.unstar(s.id);
      } else {
        await api.star(s.id, download: downloadOnLike());
      }
      likedSongs = (await api.starred()).songs;
      _saveSnapshot();
      _safeNotify();
    } catch (e) {
      // undo
      if (liked) {
        _likedIds.add(s.id);
        _likedKeys.add(s.key);
      } else {
        _likedIds.remove(s.id);
        _likedKeys.remove(s.key);
      }
      error = e.toString();
      _safeNotify();
      rethrow;
    }
  }

  // ------------------------------------------------------------ follows
  Follow? followFor(String remoteId) {
    for (final f in follows) {
      if (f.remoteId == remoteId) return f;
    }
    return null;
  }

  Future<void> follow(String kind, String remoteId, String name, {String cover = '', String artist = ''}) async {
    final f = await api.follow(kind, remoteId, name, cover: cover, artist: artist);
    follows = [f, ...follows.where((x) => x.id != f.id)];
    notifyListeners();
  }

  Future<void> unfollow(Follow f, {bool deletePlaylist = false}) async {
    await api.unfollow(f.id, deletePlaylist: deletePlaylist);
    follows = follows.where((x) => x.id != f.id).toList();
    if (deletePlaylist) playlists = playlists.where((p) => p.id != f.playlistId).toList();
    notifyListeners();
  }

  // ---------------------------------------------------------- playlists
  Future<PlaylistInfo> createPlaylist(String name, {List<Song> songs = const []}) async {
    final p = await api.createPlaylist(name, songs: songs);
    playlists = [p, ...playlists];
    notifyListeners();
    return p;
  }

  Future<void> addToPlaylist(PlaylistInfo p, List<Song> songs) async {
    await api.addToPlaylist(p.id, songs);
    playlists = await api.playlists();
    notifyListeners();
  }

  Future<void> deletePlaylist(PlaylistInfo p) async {
    await api.deletePlaylist(p.id);
    playlists = playlists.where((x) => x.id != p.id).toList();
    notifyListeners();
  }

  Future<void> renamePlaylist(PlaylistInfo p, String name) async {
    await api.renamePlaylist(p.id, name);
    playlists = await api.playlists();
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _offlineSub?.cancel();
    _poll?.cancel();
    super.dispose();
  }
}
