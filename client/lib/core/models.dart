// Plain data classes. No Flutter imports: this layer is shared by every platform.

Map<String, dynamic> _map(Object? v) => v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};
List<dynamic> _list(Object? v) => v is List ? v : const <dynamic>[];
String _str(Object? v) => v == null ? '' : v.toString();
int _int(Object? v) {
  if (v is int) return v;
  if (v is num) return v.toInt();
  return int.tryParse(_str(v)) ?? 0;
}

class Song {
  Song(this.raw);

  /// The song exactly as the server sent it (plain values only), so it can be sent back unchanged.
  final Map<String, dynamic> raw;

  factory Song.fromJson(Object? json) {
    final m = _map(json);
    m.removeWhere((k, v) => v is Map || v is List);
    return Song(m);
  }

  String get id => _str(raw['id']);
  String get title => _str(raw['title']);
  String get artist => _str(raw['artist']);
  String get album => _str(raw['album']);
  String get coverArt => raw['coverArt'] == null ? id : _str(raw['coverArt']);
  String get artistId => _str(raw['artistId']);
  String get albumId => _str(raw['albumId']);
  String get genre => _str(raw['genre']);
  String get reason => _str(raw['reason']);
  int get duration => _int(raw['duration']);
  int get track => _int(raw['track']);
  int? get playlistItemId => raw['playlistItemId'] == null ? null : _int(raw['playlistItemId']);
  bool get isCloud => raw['cloud'] == true;
  bool get starred => raw['starred'] != null && _str(raw['starred']).isNotEmpty;

  /// Same song whether it lives in the library or on YouTube.
  String get key => '${artist.toLowerCase().trim()}|${title.toLowerCase().trim()}';

  Map<String, dynamic> toJson() => Map<String, dynamic>.from(raw);

  @override
  bool operator ==(Object other) => other is Song && other.id == id;

  @override
  int get hashCode => id.hashCode;
}

List<Song> songsFrom(Object? list) => _list(list).map(Song.fromJson).where((s) => s.id.isNotEmpty).toList();

class Album {
  Album(this.raw);
  final Map<String, dynamic> raw;
  factory Album.fromJson(Object? json) {
    final m = _map(json);
    m.removeWhere((k, v) => v is Map || v is List);
    return Album(m);
  }
  String get id => _str(raw['id']);
  String get name => _str(raw['name'] ?? raw['title'] ?? raw['album']);
  String get artist => _str(raw['artist']);
  String get artistId => _str(raw['artistId']);
  String get coverArt => raw['coverArt'] == null ? id : _str(raw['coverArt']);
  int get year => _int(raw['year']);
  int get songCount => _int(raw['songCount']);
  bool get isCloud => raw['cloud'] == true;
}

List<Album> albumsFrom(Object? list) => _list(list).map(Album.fromJson).where((a) => a.id.isNotEmpty).toList();

class Artist {
  Artist(this.raw);
  final Map<String, dynamic> raw;
  factory Artist.fromJson(Object? json) {
    final m = _map(json);
    m.removeWhere((k, v) => v is Map || v is List);
    return Artist(m);
  }
  String get id => _str(raw['id']);
  String get name => _str(raw['name']);
  String get coverArt => raw['coverArt'] == null ? id : _str(raw['coverArt']);
  int get albumCount => _int(raw['albumCount']);
  bool get isCloud => raw['cloud'] == true;
}

List<Artist> artistsFrom(Object? list) => _list(list).map(Artist.fromJson).where((a) => a.id.isNotEmpty).toList();

class PlaylistInfo {
  PlaylistInfo(this.json);
  final Map<String, dynamic> json;
  factory PlaylistInfo.fromJson(Object? j) => PlaylistInfo(_map(j));
  String get id => _str(json['id']);
  String get name => _str(json['name']);
  String get description => _str(json['description']);
  String get kind => _str(json['kind']);
  String get coverArt => _str(json['coverArt']);
  int get songCount => _int(json['songCount']);
  int get duration => _int(json['duration']);
  bool get isFollow => kind == 'follow';
}

class PlaylistFull {
  PlaylistFull(this.info, this.songs);
  final PlaylistInfo info;
  final List<Song> songs;
  factory PlaylistFull.fromJson(Object? j) {
    final m = _map(j);
    return PlaylistFull(PlaylistInfo(m), songsFrom(m['songs']));
  }
}

class Follow {
  Follow(this.json);
  final Map<String, dynamic> json;
  factory Follow.fromJson(Object? j) => Follow(_map(j));
  String get id => _str(json['id']);
  String get kind => _str(json['kind']);
  String get remoteId => _str(json['remote_id']);
  String get name => _str(json['name']);
  String get cover => _str(json['cover']);
  String get artist => _str(json['artist']);
  String get playlistId => _str(json['playlist_id']);
  String get status => _str(json['status']);
  int get total => _int(json['total']);
  int get done => _int(json['done']);
  int get failed => _int(json['failed']);
  bool get active => status == 'queued' || status == 'working';
  double get progress => total == 0 ? 0 : ((done + failed) / total).clamp(0.0, 1.0);
}

class RemotePlaylist {
  RemotePlaylist(this.json);
  final Map<String, dynamic> json;
  String get id => _str(json['id']);
  String get title => _str(json['title']);
  String get author => _str(json['author']);
  String get cover => _str(json['cover']);
}

class StatItem {
  StatItem(this.json);
  final Map<String, dynamic> json;
  String get name => _str(json['name']);
  String get artist => _str(json['artist']);
  String get coverArt => _str(json['coverArt']);
  String get albumId => _str(json['albumId']);
  int get seconds => _int(json['seconds']);
  int get plays => _int(json['plays']);
  int get tracks => _int(json['tracks']);
}

class Stats {
  Stats(this.json);
  final Map<String, dynamic> json;

  int get totalSeconds => _int(json['totalSeconds']);
  double get totalHours => (json['totalHours'] is num) ? (json['totalHours'] as num).toDouble() : 0;
  double get lifetimeHours => (json['lifetimeHours'] is num) ? (json['lifetimeHours'] as num).toDouble() : 0;
  double get avgMinutesPerDay => (json['avgMinutesPerDay'] is num) ? (json['avgMinutesPerDay'] as num).toDouble() : 0;
  double get skipRate => (json['skipRate'] is num) ? (json['skipRate'] as num).toDouble() : 0;
  int get plays => _int(json['plays']);
  int get uniqueTracks => _int(json['uniqueTracks']);
  int get uniqueArtists => _int(json['uniqueArtists']);
  int get newArtists => _int(json['newArtists']);
  int get activeDays => _int(json['activeDays']);
  int get currentStreak => _int(json['currentStreak']);
  int get longestStreak => _int(json['longestStreak']);
  List<StatItem> get topArtists => _list(json['topArtists']).map((e) => StatItem(_map(e))).toList();
  List<StatItem> get topAlbums => _list(json['topAlbums']).map((e) => StatItem(_map(e))).toList();
  List<StatItem> get topGenres => _list(json['topGenres']).map((e) => StatItem(_map(e))).toList();
  List<MapEntry<Song, int>> get topTracks =>
      _list(json['topTracks']).map((e) => MapEntry(Song.fromJson(_map(e)['song']), _int(_map(e)['plays']))).toList();
  List<int> get byWeekday => _list(json['byWeekday']).map(_int).toList();
  List<int> get byHour => _list(json['byHour']).map(_int).toList();
  List<MapEntry<String, int>> get series =>
      _list(json['series']).map((e) => MapEntry(_str(_map(e)['day']), _int(_map(e)['seconds']))).toList();
}

class HomeData {
  HomeData(this.recent, this.mostPlayed, this.weekHours, this.streak);
  final List<Song> recent;
  final List<Song> mostPlayed;
  final double weekHours;
  final int streak;
  factory HomeData.fromJson(Object? j) {
    final m = _map(j);
    return HomeData(songsFrom(m['recent']), songsFrom(m['mostPlayed']),
        (m['weekHours'] is num) ? (m['weekHours'] as num).toDouble() : 0, _int(m['streak']));
  }
}

class DiscoverData {
  DiscoverData(this.week, this.songs, this.needsHistory, this.seeds);
  final String week;
  final List<Song> songs;
  final bool needsHistory;
  final List<String> seeds;
  factory DiscoverData.fromJson(Object? j) {
    final m = _map(j);
    return DiscoverData(_str(m['week']), songsFrom(m['songs']), m['needsHistory'] == true,
        _list(m['seeds']).map(_str).toList());
  }
}

class LyricLine {
  const LyricLine(this.start, this.text);
  final Duration start;
  final String text;
}

class Lyrics {
  const Lyrics(this.lines, this.synced);
  final List<LyricLine> lines;
  final bool synced;
}

class SearchResults {
  SearchResults(this.songs, this.artists, this.albums, this.playlists);
  final List<Song> songs;
  final List<Artist> artists;
  final List<Album> albums;
  final List<RemotePlaylist> playlists;
}
