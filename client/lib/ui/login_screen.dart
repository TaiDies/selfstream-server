import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/session.dart';
import 'widgets.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  late final TextEditingController server;
  late final TextEditingController user;
  final TextEditingController password = TextEditingController();
  bool busy = false;
  bool hide = true;
  String? error;

  @override
  void initState() {
    super.initState();
    final s = context.read<Session>();
    server = TextEditingController(text: s.server);
    user = TextEditingController(text: s.user);
  }

  @override
  void dispose() {
    server.dispose();
    user.dispose();
    password.dispose();
    super.dispose();
  }

  Future<void> _go() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await context.read<Session>().signIn(server.text, user.text, password.text);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
    if (mounted) setState(() => busy = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(28),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Icon(Icons.graphic_eq, size: 64, color: kAccent),
                const SizedBox(height: 12),
                const Text('Selfstream', textAlign: TextAlign.center, style: TextStyle(fontSize: 34, fontWeight: FontWeight.w800)),
                const SizedBox(height: 6),
                const Text('Your music, your server', textAlign: TextAlign.center, style: TextStyle(color: kMuted)),
                const SizedBox(height: 28),
                TextField(
                  controller: server,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: const InputDecoration(labelText: 'Server address', hintText: '192.168.1.50:8080', prefixIcon: Icon(Icons.dns_outlined), border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: user,
                  autocorrect: false,
                  decoration: const InputDecoration(labelText: 'Username', prefixIcon: Icon(Icons.person_outline), border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: password,
                  obscureText: hide,
                  onSubmitted: (_) => busy ? null : _go(),
                  decoration: InputDecoration(
                    labelText: 'Password',
                    prefixIcon: const Icon(Icons.lock_outline),
                    border: const OutlineInputBorder(),
                    suffixIcon: IconButton(icon: Icon(hide ? Icons.visibility : Icons.visibility_off), onPressed: () => setState(() => hide = !hide)),
                  ),
                ),
                if (error != null)
                  Padding(padding: const EdgeInsets.only(top: 14), child: Text(error!, style: const TextStyle(color: Colors.redAccent))),
                const SizedBox(height: 22),
                FilledButton(
                  onPressed: busy ? null : _go,
                  style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
                  child: busy ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)) : const Text('Sign in', style: TextStyle(fontSize: 16)),
                ),
                const SizedBox(height: 18),
                const Text(
                  'Use the address of your selfstream server (port 8080) and your Navidrome login.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: kMuted, fontSize: 12),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
