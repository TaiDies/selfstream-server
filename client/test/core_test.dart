import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:selfstream/core/api.dart';
import 'package:selfstream/core/models.dart';

void main() {
  test('server addresses are tidied', () {
    expect(normalizeServer('192.168.1.5:8080'), 'http://192.168.1.5:8080');
    expect(normalizeServer(' https://music.example.com/ '), 'https://music.example.com');
    expect(normalizeServer(''), '');
  });

  test('login token follows the Subsonic scheme', () {
    final api = Api(server: 'http://x', user: 'u', password: 'sesame');
    final p = api.authParams();
    expect(p['u'], 'u');
    expect(p['s']!.length, 10);
    expect(p['t']!.length, 32);
    expect(api.authParams()['s'], isNot(p['s']));
    final uri = api.streamUri('yt-abc', maxBitRate: 128);
    expect(uri.path, '/rest/stream');
    expect(uri.queryParameters['id'], 'yt-abc');
    expect(uri.queryParameters['maxBitRate'], '128');
  });

  test('songs read the server format', () {
    final s = Song.fromJson({'id': 'yt-aaaaaaaaaaa', 'title': 'Dumb', 'artist': 'Nirvana', 'duration': 172, 'coverArt': 'yt-aaaaaaaaaaa', 'cloud': true, 'nested': {'x': 1}});
    expect(s.isCloud, true);
    expect(s.key, 'nirvana|dumb');
    expect(s.duration, 172);
    expect(s.toJson().containsKey('nested'), false);
    expect(s.starred, false);
    expect(Song.fromJson({'id': 'yt-bbbbbbbbbbb', 'title': 'x', 'artist': 'y'}).isCloud, false); // only the server's flag counts, never the id
    expect(Song.fromJson({'id': '5', 'title': 'a', 'artist': 'b', 'starred': '2026-01-01T00:00:00Z'}).starred, true);
  });

  test('stats and follows parse', () {
    final st = Stats({
      'totalSeconds': 7200,
      'totalHours': 2.0,
      'plays': 3,
      'topTracks': [
        {'song': {'id': '1', 'title': 'A', 'artist': 'B'}, 'plays': 2}
      ],
      'topGenres': [
        {'name': 'Rock', 'seconds': 100, 'plays': 2}
      ],
      'byWeekday': [1, 2, 3, 4, 5, 6, 7],
    });
    expect(st.topTracks.single.key.title, 'A');
    expect(st.topTracks.single.value, 2);
    expect(st.topGenres.single.name, 'Rock');
    expect(st.byWeekday.length, 7);
    final f = Follow({'id': 'x', 'status': 'working', 'total': 4, 'done': 1, 'failed': 1});
    expect(f.active, true);
    expect(f.progress, 0.5);
    expect(Follow({'status': 'done'}).active, false);
  });

  test('saved pages are used when the server fails or the device is offline', () async {
    var down = false;
    final store = <String, String>{};
    final api = Api(
      server: 'http://x',
      user: 'u',
      password: 'p',
      cache: _MemCache(store),
      client: MockClient((req) async {
        if (down) return http.Response('bad gateway', 502);
        return http.Response(
            jsonEncode({'subsonic-response': {'status': 'ok', 'starred2': {'song': [{'id': 'a1', 'title': 'T', 'artist': 'A'}]}}}), 200);
      }),
    );
    expect((await api.starred()).songs.length, 1);
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(store, isNotEmpty);
    down = true; // server answers 502
    expect((await api.starred()).songs.first.id, 'a1');
    expect(api.offline, true);
    down = false;
    api.setForcedOffline(true); // device has no network at all
    expect((await api.starred()).songs.first.id, 'a1');
    await expectLater(api.playlists(), throwsA(isA<ApiException>()));
  });
}

class _MemCache implements ResponseCache {
  _MemCache(this.m);
  final Map<String, String> m;
  @override
  Future<String?> read(String key) async => m[key];
  @override
  Future<void> write(String key, String body) async => m[key] = body;
}
