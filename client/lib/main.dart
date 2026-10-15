import 'package:cached_network_image/cached_network_image.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:just_audio/just_audio.dart';
import 'package:just_audio_background/just_audio_background.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await JustAudioBackground.init(
    androidNotificationChannelId: 'dev.selfstream.audio',
    androidNotificationChannelName: 'Selfstream playback',
    androidNotificationOngoing: true,
  );
  runApp(const SelfstreamApp());
}

class SelfstreamApp extends StatelessWidget {
  const SelfstreamApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => _Probe(),
      child: MaterialApp(
        title: 'Selfstream',
        theme: ThemeData(colorSchemeSeed: Colors.green, brightness: Brightness.dark, useMaterial3: true),
        home: const _Home(),
      ),
    );
  }
}

class _Probe extends ChangeNotifier {
  final AudioPlayer player = AudioPlayer();
  String hash = md5.convert('selfstream'.codeUnits).toString();
  Future<void> touch() async {
    await SharedPreferences.getInstance();
    await http.get(Uri.parse('http://localhost:1')).catchError((_) => http.Response('', 500));
  }
}

class _Home extends StatelessWidget {
  const _Home();

  @override
  Widget build(BuildContext context) {
    final probe = context.watch<_Probe>();
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('Selfstream', style: TextStyle(fontSize: 28)),
            Text(probe.hash),
            const SizedBox(
              width: 48,
              height: 48,
              child: CachedNetworkImage(imageUrl: 'http://localhost:1/x.jpg', errorWidget: _err),
            ),
          ],
        ),
      ),
    );
  }

  static Widget _err(BuildContext c, String u, Object e) => const Icon(Icons.music_note);
}
