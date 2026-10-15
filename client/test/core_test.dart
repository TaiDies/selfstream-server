import 'package:flutter_test/flutter_test.dart';
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
    final s = Song.fromJson({'id': 'yt-aaaaaaaaaaa', 'title': 'Dumb', 'artist': 'Nirvana', 'duration': 172, 'coverArt': 'yt-aaaaaaaaaaa', 'nested': {'x': 1}});
    expect(s.isCloud, true);
    expect(s.key, 'nirvana|dumb');
    expect(s.duration, 172);
    expect(s.toJson().containsKey('nested'), false);
    expect(s.starred, false);
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
}
