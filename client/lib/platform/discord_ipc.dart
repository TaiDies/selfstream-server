import 'dart:async';
import 'dart:convert';
import 'dart:ffi';
import 'dart:io';
import 'dart:typed_data';

import 'package:ffi/ffi.dart';

/// Talks to the Discord desktop app over its local pipe (Windows only).
///
/// It sends the card the server prepared and nothing else: no YouTube code lives here.
class DiscordIpc {
  DiscordIpc(this.clientId);
  final String clientId;

  int _handle = -1;
  int _nonce = 0;
  bool get connected => _handle != -1;

  static final DynamicLibrary _k = DynamicLibrary.open('kernel32.dll');
  static final int Function(Pointer<Utf16>, int, int, Pointer<Void>, int, int, int) _createFile = _k.lookupFunction<
      IntPtr Function(Pointer<Utf16>, Uint32, Uint32, Pointer<Void>, Uint32, Uint32, IntPtr),
      int Function(Pointer<Utf16>, int, int, Pointer<Void>, int, int, int)>('CreateFileW');
  static final int Function(int, Pointer<Uint8>, int, Pointer<Uint32>, Pointer<Void>) _writeFile = _k.lookupFunction<
      Int32 Function(IntPtr, Pointer<Uint8>, Uint32, Pointer<Uint32>, Pointer<Void>),
      int Function(int, Pointer<Uint8>, int, Pointer<Uint32>, Pointer<Void>)>('WriteFile');
  static final int Function(int, Pointer<Uint8>, int, Pointer<Uint32>, Pointer<Void>) _readFile = _k.lookupFunction<
      Int32 Function(IntPtr, Pointer<Uint8>, Uint32, Pointer<Uint32>, Pointer<Void>),
      int Function(int, Pointer<Uint8>, int, Pointer<Uint32>, Pointer<Void>)>('ReadFile');
  static final int Function(int, Pointer<Uint8>, int, Pointer<Uint32>, Pointer<Uint32>, Pointer<Uint32>) _peek =
      _k.lookupFunction<
          Int32 Function(IntPtr, Pointer<Uint8>, Uint32, Pointer<Uint32>, Pointer<Uint32>, Pointer<Uint32>),
          int Function(int, Pointer<Uint8>, int, Pointer<Uint32>, Pointer<Uint32>, Pointer<Uint32>)>('PeekNamedPipe');
  static final int Function(int) _closeHandle =
      _k.lookupFunction<Int32 Function(IntPtr), int Function(int)>('CloseHandle');

  static const int _genericReadWrite = 0xC0000000;
  static const int _openExisting = 3;

  /// Finds a running Discord and says hello. False when Discord is not open.
  Future<bool> connect() async {
    if (connected) return true;
    for (var i = 0; i < 10 && !connected; i++) {
      final name = '\\\\.\\pipe\\discord-ipc-$i'.toNativeUtf16();
      try {
        final h = _createFile(name, _genericReadWrite, 0, nullptr, _openExisting, 0, 0);
        if (h != -1) _handle = h;
      } finally {
        malloc.free(name);
      }
    }
    if (!connected) return false;
    if (!_send(0, {'v': 1, 'client_id': clientId})) return false;
    for (var i = 0; i < 20; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 100));
      final msgs = _drain();
      if (!connected) return false;
      if (msgs.any((m) => m.contains('"READY"'))) return true;
      if (msgs.any((m) => m.contains('"code"'))) break; // Discord refused (bad application id)
    }
    close();
    return false;
  }

  /// Shows [activity], or clears the card when it is null.
  Future<bool> setActivity(Map<String, Object?>? activity) async {
    if (!await connect()) return false;
    final ok = _send(1, {
      'cmd': 'SET_ACTIVITY',
      'args': {'pid': pid, 'activity': activity},
      'nonce': '${++_nonce}',
    });
    if (ok) _drain();
    return ok;
  }

  bool _send(int opcode, Map<String, Object?> payload) {
    if (!connected) return false;
    final body = utf8.encode(jsonEncode(payload));
    final buf = malloc<Uint8>(8 + body.length);
    final written = malloc<Uint32>();
    try {
      final view = buf.asTypedList(8 + body.length);
      final header = ByteData(8)
        ..setInt32(0, opcode, Endian.little)
        ..setInt32(4, body.length, Endian.little);
      view.setRange(0, 8, header.buffer.asUint8List());
      view.setRange(8, 8 + body.length, body);
      final r = _writeFile(_handle, buf, 8 + body.length, written, nullptr);
      if (r == 0) {
        close();
        return false;
      }
      return true;
    } finally {
      malloc.free(buf);
      malloc.free(written);
    }
  }

  /// Reads whatever Discord has answered, without ever waiting for more.
  List<String> _drain() {
    final out = <String>[];
    final avail = malloc<Uint32>();
    final got = malloc<Uint32>();
    try {
      while (connected) {
        if (_peek(_handle, nullptr, 0, nullptr, avail, nullptr) == 0) {
          close();
          break;
        }
        if (avail.value < 8) break;
        final head = malloc<Uint8>(8);
        try {
          if (_readFile(_handle, head, 8, got, nullptr) == 0) {
            close();
            break;
          }
          final len = ByteData.sublistView(head.asTypedList(8)).getInt32(4, Endian.little);
          if (len <= 0 || len > 65536) break;
          final body = malloc<Uint8>(len);
          try {
            if (_readFile(_handle, body, len, got, nullptr) == 0) {
              close();
              break;
            }
            out.add(utf8.decode(body.asTypedList(got.value), allowMalformed: true));
          } finally {
            malloc.free(body);
          }
        } finally {
          malloc.free(head);
        }
      }
    } finally {
      malloc.free(avail);
      malloc.free(got);
    }
    return out;
  }

  void close() {
    if (_handle != -1) {
      _closeHandle(_handle);
      _handle = -1;
    }
  }
}
