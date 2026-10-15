import 'dart:async';

import 'package:flutter/foundation.dart';

import '../core/api.dart';
import '../core/models.dart';

/// Your playlists, followed albums/artists/playlists and liked songs, shared by every screen.
class Library extends ChangeNotifier {
  Library(this.api);
  final Api api;

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
    try {
      final results = await Future.wait<Object>([api.playlists(), api.follows(), api.starred()]);
      playlists = results[0] as List<PlaylistInfo>;
      follows = results[1] as List<Follow>;
      final st = results[2] as ({List<Song> songs, List<Album> albums, List<Artist> artists});
      likedSongs = st.songs;
      _likedIds
        ..clear()
        ..addAll(likedSongs.map((s) => s.id));
      _likedKeys
        ..clear()
        ..addAll(likedSongs.map((s) => s.key));
      error = null;
    } catch (e) {
      error = e.toString();
    }
    loading = false;
    _safeNotify();
  }

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  /// While something is downloading, keep progress fresh.
  void startPolling() {
    _poll?.cancel();
    _poll = Timer.periodic(const Duration(seconds: 6), (_) async {
      if (!follows.any((f) => f.active)) return;
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
        await api.star(s.id);
      }
      likedSongs = (await api.starred()).songs;
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
    _poll?.cancel();
    super.dispose();
  }
}
