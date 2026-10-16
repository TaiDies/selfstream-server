import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter_cache_manager/flutter_cache_manager.dart';
import 'package:just_audio/just_audio.dart';
import 'package:just_audio_background/just_audio_background.dart';

import '../core/api.dart';
import '../core/models.dart';
import 'library.dart';
import '../platform/platform.dart';
import 'offline.dart';
import 'settings.dart';

/// Playback, the queue, listening reports, sleep timer and "continue where you left off" across devices.
class PlayerController extends ChangeNotifier {
  PlayerController(this.api, this.library, this.offline, this.settings) {
    _subs.add(player.currentIndexStream.listen(_onIndex));
    _subs.add(player.playerStateStream.listen(_onState));
    _subs.add(player.positionStream.listen(_onPosition));
    _subs.add(player.playbackEventStream.listen((_) {}, onError: _onError));
    _subs.add(player.shuffleModeEnabledStream.listen((_) => notifyListeners()));
    _subs.add(player.loopModeStream.listen((_) => notifyListeners()));
    _subs.add(player.speedStream.listen((_) => notifyListeners()));
    if (isDesktop) {
      player.setVolume(settings.volume);
      _subs.add(player.volumeStream.listen((_) => notifyListeners()));
    }
  }

  double get volume => player.volume;
  Future<void> setVolume(double v) {
    settings.volume = v;
    return player.setVolume(v.clamp(0.0, 1.0));
  }

  final Api api;
  final Library library;
  final OfflineStore offline;
  final Settings settings;

  /// Does the connected server offer this feature? (set by the session once it knows)
  bool Function(String) has = (_) => false;
  final AudioPlayer player = AudioPlayer();
  final List<StreamSubscription<Object?>> _subs = [];
  final StreamController<String> _errors = StreamController<String>.broadcast();

  Stream<String> get errors => _errors.stream;
  Stream<Duration> get positionStream => player.positionStream;

  List<Song> queue = [];
  int index = -1;
  ConcatenatingAudioSource? _source;
  bool _disposed = false;

  Song? get current => index >= 0 && index < queue.length ? queue[index] : null;
  bool get playing => player.playing;
  bool get shuffle => player.shuffleModeEnabled;
  LoopMode get loop => player.loopMode;
  double get speed => player.speed;
  bool get hasQueue => queue.isNotEmpty;

  // ------------------------------------------------------------ building
  // Covers as files on this device: the notification, lock screen and Android Auto load these
  // directly instead of fetching a signed server URL themselves.
  final Map<String, Uri> _coverFiles = {};

  Future<void> _warmCovers(Iterable<Song> songs) async {
    if (isDesktop) return;
    final ids = <String>{for (final s in songs) s.coverArt}..removeWhere((i) => i.isEmpty || _coverFiles.containsKey(i));
    final list = ids.toList();
    Future<void> one(String id) async {
      try {
        final f = await DefaultCacheManager().getSingleFile(api.coverUri(id, size: 512).toString(), key: api.coverKey(id, 512));
        _coverFiles[id] = Uri.file(f.path);
      } catch (_) {}
    }

    Future<void> all() async {
      for (var i = 0; i < list.length; i += 8) {
        await Future.wait(list.skip(i).take(8).map(one));
      }
    }

    await all().timeout(const Duration(seconds: 5), onTimeout: () {});
  }

  AudioSource _sourceFor(Song s) {
    final path = offline.pathForSong(s);
    final uri = path != null ? Uri.file(path) : api.streamUri(s.id, maxBitRate: settings.maxBitRate);
    return AudioSource.uri(
      uri,
      tag: MediaItem(
        id: s.id,
        title: s.title,
        artist: s.artist,
        album: s.album,
        artUri: _coverFiles[s.coverArt] ?? api.coverUri(s.coverArt, size: 512),
        duration: s.duration > 0 ? Duration(seconds: s.duration) : null,
      ),
    );
  }

  // ------------------------------------------------------------ playing
  Future<void> playSongs(List<Song> songs, {int start = 0, bool shuffle = false, bool checked = false}) async {
    if (songs.isEmpty) return;
    if (api.offline && !checked) {
      // no connection: only what is saved on this device can play
      final picked = songs[start.clamp(0, songs.length - 1)];
      final playable = songs.where(offline.hasSong).toList();
      if (playable.isEmpty) {
        _errors.add('Offline: none of these songs are saved on this device.');
        return;
      }
      if (!offline.hasSong(picked)) _errors.add('Offline: skipping songs that are not saved on this device.');
      final at = playable.indexOf(picked);
      return playSongs(playable, start: at < 0 ? 0 : at, shuffle: shuffle, checked: true);
    }
    await _endTrack();
    queue = List<Song>.of(songs);
    var first = start.clamp(0, queue.length - 1);
    if (shuffle) first = Random().nextInt(queue.length);
    index = first;
    await _warmCovers(queue);
    _source = ConcatenatingAudioSource(children: queue.map(_sourceFor).toList());
    notifyListeners();
    try {
      await player.setShuffleModeEnabled(shuffle);
      await player.setAudioSource(_source!, initialIndex: first, initialPosition: Duration.zero);
      if (shuffle) await player.shuffle();
      unawaited(player.play());
    } catch (e) {
      _errors.add('Could not start playback: ${_clean(e)}');
      if (player.hasNext) {
        await player.seekToNext();
        unawaited(player.play());
      }
    }
  }

  Future<void> playSong(Song s) => playSongs([s]);

  Future<void> toggle() async {
    if (player.playing) {
      await player.pause();
    } else {
      unawaited(player.play());
    }
    notifyListeners();
  }

  Future<void> next() => player.seekToNext();

  Future<void> previous() async {
    if (player.position > const Duration(seconds: 4) || !player.hasPrevious) {
      await player.seek(Duration.zero);
    } else {
      await player.seekToPrevious();
    }
  }

  Future<void> seek(Duration d) => player.seek(d);
  Future<void> jumpTo(int i) => player.seek(Duration.zero, index: i);

  Future<void> toggleShuffle() async {
    await player.setShuffleModeEnabled(!player.shuffleModeEnabled);
    if (player.shuffleModeEnabled) await player.shuffle();
    _scheduleSave();
  }

  Future<void> cycleLoop() async {
    final next = switch (player.loopMode) {
      LoopMode.off => LoopMode.all,
      LoopMode.all => LoopMode.one,
      LoopMode.one => LoopMode.off,
    };
    await player.setLoopMode(next);
    _scheduleSave();
  }

  Future<void> setSpeed(double v) => player.setSpeed(v);

  // --------------------------------------------------------------- queue
  Future<void> addToQueue(Song s) async {
    if (queue.isEmpty || _source == null) return playSongs([s]);
    queue.add(s);
    await _warmCovers([s]);
    await _source!.add(_sourceFor(s));
    notifyListeners();
    _scheduleSave();
  }

  Future<void> playNext(Song s) async {
    if (queue.isEmpty || _source == null) return playSongs([s]);
    final at = (index + 1).clamp(0, queue.length);
    queue.insert(at, s);
    await _warmCovers([s]);
    await _source!.insert(at, _sourceFor(s));
    notifyListeners();
    _scheduleSave();
  }

  Future<void> addAllToQueue(List<Song> songs) async {
    for (final s in songs) {
      await addToQueue(s);
    }
  }

  Future<void> removeAt(int i) async {
    if (i < 0 || i >= queue.length || _source == null) return;
    queue.removeAt(i);
    await _source!.removeAt(i);
    if (queue.isEmpty) {
      index = -1;
      await player.stop();
    }
    notifyListeners();
    _scheduleSave();
  }

  Future<void> reorder(int oldIndex, int newIndex) async {
    if (_source == null) return;
    if (newIndex > oldIndex) newIndex -= 1;
    final s = queue.removeAt(oldIndex);
    queue.insert(newIndex, s);
    await _source!.move(oldIndex, newIndex);
    notifyListeners();
    _scheduleSave();
  }

  Future<void> clearQueue() async {
    await _endTrack();
    await player.stop();
    queue = [];
    index = -1;
    _source = null;
    notifyListeners();
    _scheduleSave();
  }

  // -------------------------------------------------------- sleep timer
  Timer? _sleepTimer;
  Duration? sleepLeft;
  bool sleepAtTrackEnd = false;
  bool get sleepActive => sleepLeft != null || sleepAtTrackEnd;

  void setSleep(Duration? d, {bool atTrackEnd = false}) {
    _sleepTimer?.cancel();
    sleepLeft = d;
    sleepAtTrackEnd = atTrackEnd;
    if (d != null) {
      _sleepTimer = Timer.periodic(const Duration(seconds: 1), (t) {
        final left = (sleepLeft ?? Duration.zero) - const Duration(seconds: 1);
        if (left <= Duration.zero) {
          t.cancel();
          sleepLeft = null;
          player.pause();
        } else {
          sleepLeft = left;
        }
        notifyListeners();
      });
    }
    notifyListeners();
  }

  // ----------------------------------------------------------- listening
  Song? _tSong;
  double _tSeconds = 0;
  double _tStart = 0;
  Duration _lastPos = Duration.zero;
  bool _tSentHalf = false;
  bool _flushing = false;

  void _beginTrack(Song? s) {
    _tSong = s;
    _tSeconds = 0;
    _tSentHalf = false;
    _tStart = DateTime.now().millisecondsSinceEpoch / 1000.0;
    _lastPos = Duration.zero;
  }

  void _onPosition(Duration p) {
    final s = _tSong;
    if (s == null) return;
    if (player.playing) {
      final d = p - _lastPos;
      if (d.inMilliseconds > 0 && d.inMilliseconds < 3000) _tSeconds += d.inMilliseconds / 1000.0;
    }
    _lastPos = p;
    if (!_tSentHalf && _tSeconds >= 30) {
      _tSentHalf = true;
      _report(s, final_: false);
      if (!s.isCloud) api.scrobble(s.id).catchError((Object _) {});
    }
    _maybeSave(p);
  }

  Future<void> _endTrack() async {
    final s = _tSong;
    if (s != null && _tSeconds >= 1) await _report(s, final_: true);
    _tSong = null;
    _tSeconds = 0;
  }

  Future<void> _report(Song s, {required bool final_}) async {
    if (!settings.trackListening || !has('history')) return;
    final play = <String, dynamic>{
      'song': s.toJson(),
      'seconds': double.parse(_tSeconds.toStringAsFixed(1)),
      'duration': s.duration,
      'ts': _tStart,
    };
    try {
      final pending = settings.pendingPlays;
      if (pending.isNotEmpty && !_flushing) {
        _flushing = true;
        try {
          await api.reportPlays(pending);
          settings.pendingPlays = [];
        } finally {
          _flushing = false;
        }
      }
      await api.reportPlay(play);
    } catch (_) {
      if (final_) settings.pendingPlays = [...settings.pendingPlays, play];
    }
  }

  // ------------------------------------------------------------- events
  void _onIndex(int? i) {
    final newIndex = i ?? -1;
    if (newIndex == index && _tSong != null) return;
    final wasEnd = sleepAtTrackEnd;
    unawaited(_endTrack().then((_) {
      index = newIndex;
      _beginTrack(current);
      notifyListeners();
      _scheduleSave();
    }));
    if (wasEnd) {
      sleepAtTrackEnd = false;
      player.pause();
      player.seek(Duration.zero);
    }
  }

  void _onState(PlayerState state) {
    if (state.playing && _tSong == null && current != null) _beginTrack(current);
    if (state.processingState == ProcessingState.completed) {
      unawaited(_endTrack());
    }
    notifyListeners();
  }

  void _onError(Object error, StackTrace st) {
    _errors.add('Playback problem: ${_clean(error)}');
    if (player.hasNext) {
      player.seekToNext();
    }
  }

  String _clean(Object e) {
    final s = e.toString();
    return s.length > 120 ? '${s.substring(0, 120)}...' : s;
  }

  // -------------------------------------------------- sync across devices
  Timer? _saveTimer;
  DateTime _lastPosSave = DateTime.fromMillisecondsSinceEpoch(0);

  void _scheduleSave() {
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(seconds: 4), _save);
  }

  void _maybeSave(Duration p) {
    if (player.playing && DateTime.now().difference(_lastPosSave) > const Duration(seconds: 30)) {
      _lastPosSave = DateTime.now();
      _save();
    }
  }

  Future<void> _save() async {
    if (_disposed) return;
    try {
      if (!has('state')) return;
      await api.putState('queue', {
        'songs': queue.take(300).map((s) => s.toJson()).toList(),
        'index': queue.length > 300 ? min(index, 299) : index,
        'positionMs': player.position.inMilliseconds,
        'shuffle': shuffle,
        'loop': loop.index,
      });
    } catch (_) {}
  }

  /// Load the queue saved by the last device, paused, so you can continue.
  Future<void> restore() async {
    if (queue.isNotEmpty || !has('state')) return;
    try {
      final v = await api.getState('queue');
      if (v is! Map) return;
      final songs = songsFrom(v['songs']);
      if (songs.isEmpty || queue.isNotEmpty) return;
      final i = ((v['index'] as num?)?.toInt() ?? 0).clamp(0, songs.length - 1);
      queue = songs;
      index = i;
      await _warmCovers(songs);
      _source = ConcatenatingAudioSource(children: songs.map(_sourceFor).toList());
      await player.setAudioSource(_source!,
          initialIndex: i, initialPosition: Duration(milliseconds: (v['positionMs'] as num?)?.toInt() ?? 0));
      final loopIndex = ((v['loop'] as num?)?.toInt() ?? 0).clamp(0, LoopMode.values.length - 1);
      await player.setLoopMode(LoopMode.values[loopIndex]);
      await player.setShuffleModeEnabled(v['shuffle'] == true);
      _beginTrack(null); // do not count the restored track as a listen until it plays
      notifyListeners();
    } catch (_) {
      // nothing to restore, or the saved song is gone
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _saveTimer?.cancel();
    _sleepTimer?.cancel();
    for (final s in _subs) {
      s.cancel();
    }
    _errors.close();
    player.dispose();
    super.dispose();
  }
}
